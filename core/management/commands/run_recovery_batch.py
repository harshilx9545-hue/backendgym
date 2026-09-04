"""Run the AI revenue recovery batch and print a measured report.

    python manage.py run_recovery_batch --synthetic-data
    python manage.py run_recovery_batch --gym iron-pit-7 --live
    python manage.py run_recovery_batch --synthetic-data --rounds 4

`--synthetic-data` seeds a throwaway gym with 50 overdue member invoices spread across
all four escalation tiers, runs the whole batch through the agent, and prints what was
recovered, what is still pending, what was stopped, and a per-invoice audit line for
every decision including the ones the guardrails refused.

`--rounds N` runs the batch N times against the same data. That is how the stopping rule
becomes visible: the same invoice is contacted at most three times, and from the fourth
round on it reports `stopped_attempt_limit` instead of a fourth reminder.

A multi-round run leads with `CAMPAIGN TOTAL`, the money recovered across every round,
read back from the ledger. The per-round report that follows is the *last* round's, and
on a four-round run that round recovers nothing by construction - the members who were
going to respond already have, and the rest are stopped - so printing only it would
understate the campaign to zero.

The formatting helpers are module-level and importable, so the conformance tests can
assert on the report text without capturing stdout.
"""
from __future__ import annotations

import sys
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Gym, RecoveryAttempt
from core.services.recovery_agent import (
    BLOCKED_OUTCOMES,
    MAX_AUTOMATED_ATTEMPTS,
    MAX_DISCOUNT_PERCENTAGE,
    MAX_DISCOUNTS_PER_INVOICE,
    SYNTHETIC_TIER_SPREAD,
    TIERS,
    AIRevenueRecoveryAgent,
    Outcome,
    SyntheticGatewayAdapter,
    seed_synthetic_overdue_invoices,
)

DEFAULT_SYNTHETIC_COUNT = 50


# ============ FORMATTING HELPERS ============

def currency_symbol(stream=None):
    """`\u20b9` when the output stream can encode it, `Rs.` when it cannot.

    Windows consoles often run a code page with no U+20B9. Probing the stream is more
    reliable than guessing from the platform, because a redirected stdout is usually
    UTF-8 even when the console is not.
    """
    encoding = getattr(stream, "encoding", None) or getattr(sys.stdout, "encoding", None)
    try:
        "\u20b9".encode(encoding or "utf-8")
    except (LookupError, UnicodeEncodeError, TypeError):
        return "Rs."
    return "\u20b9"


def money(amount, symbol="\u20b9"):
    """Thousands-separated money, so a six-figure total is readable at a glance."""
    return f"{symbol}{Decimal(amount):,.2f}"


def format_header(report, symbol="\u20b9", round_label=""):
    mode = (
        "synthetic (member responses simulated)"
        if report.synthetic
        else "live (collection left to gateway webhooks)"
    )
    title = "AI REVENUE RECOVERY - BATCH REPORT"
    if round_label:
        title = f"{title} ({round_label})"
    return [
        "=" * 100,
        title,
        "=" * 100,
        f"Gym                  : {report.gym_slug} (id {report.gym_id})",
        f"Mode                 : {mode}",
        f"Planner              : {report.planner_name}",
        f"Generated            : {report.generated_at.isoformat() if report.generated_at else '-'}",
        f"Guardrails           : discount cap {MAX_DISCOUNT_PERCENTAGE}%, "
        f"max {MAX_AUTOMATED_ATTEMPTS} automated attempts/invoice, "
        f"{MAX_DISCOUNTS_PER_INVOICE} discount/invoice",
        "",
        f"Invoices processed   : {report.processed}",
        f"Outstanding at start : {money(report.outstanding_before, symbol)}",
    ]


def format_result(report, symbol="\u20b9"):
    lines = [
        "",
        "-" * 100,
        f"RESULT: {report.summary_line(symbol)}",
        "-" * 100,
        f"  recovered   : {report.recovered_count:>4}  {money(report.recovered_amount, symbol):>16}",
        f"  pending     : {report.pending_count:>4}  {money(report.pending_amount, symbol):>16}",
        f"  stopped     : {report.stopped_count:>4}  {money(report.stopped_amount, symbol):>16}",
        f"  discounted  : {'':>4}  {money(report.discount_amount_total, symbol):>16}"
        "  (given up to close invoices)",
        f"  {'':<14}      {'':>4}  {money(report.closing_balance, symbol):>16}"
        f"  = outstanding at start ({money(report.outstanding_before, symbol)})",
        f"  reconciles           : {report.reconciles}",
        f"  recovery rate on the outstanding balance: {report.recovery_rate}",
    ]
    if not report.synthetic:
        lines.append(
            "  (live mode: recovered is 0 by construction - the money arrives later, "
            "through the gateway webhook, and is measured then)"
        )
    return lines


def format_actions(report, symbol="\u20b9"):
    lines = [
        "",
        "Actions taken:",
        f"  reminders sent      : {report.reminders_sent}",
        f"  discounts applied   : {report.discounts_applied}"
        f"  ({money(report.discount_amount_total, symbol)} given up)",
        f"  escalated to human  : {report.escalated_count}",
        f"  guardrail refusals  : {report.blocked_total}",
    ]
    for outcome, count in sorted(report.blocked_counts.items()):
        lines.append(f"      {outcome:<30} {count}")
    if report.planner_fallbacks:
        lines.append(
            f"  planner fallbacks   : {report.planner_fallbacks} "
            "(hosted model unavailable; deterministic planner used)"
        )
    return lines


def format_tiers(report):
    lines = ["", "Escalation tiers reached:"]
    for tier in TIERS:
        count = report.tier_counts.get(tier.number, 0)
        lines.append(
            f"  tier {tier.number}  {tier.window:<12} {count:>4} invoice(s)   {tier.name}"
        )
    return lines


AUDIT_COLUMNS = (
    f"{'INVOICE':<26} {'MEMBER':<34} {'TIER':>4} {'DAYS':>5} "
    f"{'TOOL':<38} {'OUTCOME':<28} {'AMOUNT':>14} {'RECOVERED':>14}  DETAIL"
)


def format_audit_log(report, symbol="\u20b9", with_reasoning=False):
    lines = ["", "Per-invoice audit log:", AUDIT_COLUMNS, "-" * 100]
    for line in report.audit_log:
        lines.append(
            f"{line.invoice_number:<26} {line.member[:34]:<34} {line.tier:>4} "
            f"{line.days_overdue:>5} {line.tool_called:<38} {line.outcome:<28} "
            f"{money(line.amount, symbol):>14} {money(line.recovered, symbol):>14}  "
            f"{line.detail}"
        )
        if with_reasoning and line.reasoning:
            lines.append(f"{'':<26} reasoning: {line.reasoning}")
    return lines


def ledger_recovered(gym_id):
    """Money collected for this gym, summed from the ledger's `payment_observed` rows.

    The single authoritative recovered figure. Deliberately a read-back rather than a
    running total the batch keeps for itself: an agent that reported its own takings
    would be measuring its own prompt.
    """
    rows = RecoveryAttempt.objects.filter(gym_id=gym_id, outcome=Outcome.PAYMENT_OBSERVED)
    return sum((row.amount_recovered for row in rows), Decimal("0.00"))


def format_campaign_summary(reports, symbol="\u20b9"):
    """Cumulative result across every round, for multi-round runs.

    A per-round report is a snapshot, and the last snapshot of a `--rounds 4` run is
    necessarily the quiet one: by round 4 the money has already been collected and the
    survivors are all stopped, so that round alone reads `0 recovered`. Printing only
    it would understate the campaign to zero. The campaign figure is what the track
    actually asks for - money recovered across a batch - so it is read back from the
    ledger and printed first.
    """
    first, last = reports[0], reports[-1]
    gym_id = first.gym_id
    recovered = ledger_recovered(gym_id)
    opening = first.outstanding_before
    rate = (recovered / opening).quantize(Decimal("0.0001")) if opening > 0 else Decimal("0.0000")

    lines = [
        "=" * 100,
        f"CAMPAIGN TOTAL ACROSS {len(reports)} ROUND(S) - measured from the append-only ledger",
        "=" * 100,
        f"  outstanding at start      : {money(opening, symbol)}  ({first.processed} overdue invoices)",
        f"  MONEY RECOVERED           : {money(recovered, symbol)}",
        f"  recovery rate             : {rate}",
        f"  still pending at the end  : {money(last.pending_amount, symbol)}  "
        f"({last.pending_count} invoice(s))",
        f"  stopped by a guardrail    : {money(last.stopped_amount, symbol)}  "
        f"({last.stopped_count} invoice(s), no further automated contact)",
        "",
        "  Per round:",
    ]
    for index, report in enumerate(reports, start=1):
        lines.append(
            f"    round {index}: {report.recovered_count:>3} recovered "
            f"{money(report.recovered_amount, symbol):>14}   "
            f"{report.pending_count:>3} pending   {report.stopped_count:>3} stopped   "
            f"reconciles={report.reconciles}"
        )
    lines.append("")
    return lines


def format_ledger_totals(gym_id, symbol="\u20b9"):
    """Read the whole ledger back for this gym, independently of any one batch."""
    rows = RecoveryAttempt.objects.filter(gym_id=gym_id)
    total = rows.count()
    recovered = ledger_recovered(gym_id)
    lines = [
        "",
        "-" * 100,
        "Audit trail (append-only RecoveryAttempt ledger, all rounds):",
        f"  rows written              : {total}",
        f"  money measured recovered  : {money(recovered, symbol)}",
    ]
    counts = {}
    for row in rows.values_list("outcome", flat=True):
        counts[row] = counts.get(row, 0) + 1
    for outcome, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        flag = "  <- guardrail refusal" if outcome in BLOCKED_OUTCOMES else ""
        lines.append(f"      {outcome:<30} {count:>5}{flag}")
    return lines


def format_report(report, *, symbol="\u20b9", with_reasoning=False, ledger=True, round_label=""):
    """The whole report as a list of lines."""
    lines = []
    lines += format_header(report, symbol, round_label=round_label)
    lines += format_result(report, symbol)
    lines += format_actions(report, symbol)
    lines += format_tiers(report)
    lines += format_audit_log(report, symbol, with_reasoning=with_reasoning)
    if ledger:
        lines += format_ledger_totals(report.gym_id, symbol)
    lines.append("=" * 100)
    return lines


# ============ GYM RESOLUTION ============

def resolve_gym(identifier):
    """A gym by slug or by primary key. Slug first: it is what an operator will type."""
    gym = Gym.objects.filter(slug=str(identifier)).first()
    if gym is None and str(identifier).isdigit():
        gym = Gym.objects.filter(pk=int(identifier)).first()
    if gym is None:
        raise CommandError(f"No gym matches {identifier!r} by slug or by id.")
    return gym


# ============ COMMAND ============

class Command(BaseCommand):
    help = (
        "Run the AI revenue recovery batch over a gym's overdue invoices and print a "
        "measured report with a full per-invoice audit log."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--synthetic-data",
            action="store_true",
            help=(
                f"Seed a throwaway gym with {DEFAULT_SYNTHETIC_COUNT} overdue invoices "
                "across all four tiers, then run against it."
            ),
        )
        parser.add_argument(
            "--gym",
            help="Slug or id of an existing gym to run against.",
        )
        parser.add_argument(
            "--count",
            type=int,
            default=DEFAULT_SYNTHETIC_COUNT,
            help=f"How many synthetic invoices to seed (default {DEFAULT_SYNTHETIC_COUNT}).",
        )
        parser.add_argument(
            "--rounds",
            type=int,
            default=1,
            help=(
                "Run the batch this many times. Rounds beyond the third show the "
                "stopping rule refusing further contact."
            ),
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=0,
            help="Seed for the synthetic response simulation, for a reproducible figure.",
        )
        parser.add_argument(
            "--live",
            action="store_true",
            help=(
                "Do not simulate member responses. Recovery actions are performed and "
                "collection is left to the gateway's webhooks."
            ),
        )
        parser.add_argument(
            "--notify",
            action="store_true",
            help="Actually send the reminder emails. Off by default.",
        )
        parser.add_argument(
            "--reasoning",
            action="store_true",
            help="Include each decision's model reasoning text in the audit log.",
        )

    def handle(self, *args, **options):
        synthetic_data = options["synthetic_data"]
        gym_identifier = options.get("gym")
        rounds = options["rounds"]
        count = options["count"]

        if synthetic_data and gym_identifier:
            raise CommandError(
                "--synthetic-data seeds its own gym; pass one of --synthetic-data or "
                "--gym, not both."
            )
        if not synthetic_data and not gym_identifier:
            raise CommandError(
                "Nothing to run against. Pass --synthetic-data for a seeded demo, or "
                "--gym <slug> to run against real data."
            )
        if rounds < 1:
            raise CommandError("--rounds must be at least 1.")
        if synthetic_data and count < 1:
            raise CommandError("--count must be at least 1.")

        symbol = currency_symbol(self.stdout)

        if synthetic_data:
            self.stdout.write(
                f"Seeding {count} synthetic overdue invoices across "
                f"{len(SYNTHETIC_TIER_SPREAD)} overdue ages..."
            )
            # One transaction: a half-seeded gym would make every figure below a lie.
            with transaction.atomic():
                seeded = seed_synthetic_overdue_invoices(count=count)
            gym = seeded["gym"]
            self.stdout.write(
                self.style.SUCCESS(
                    f"Seeded gym {gym.slug} (id {gym.pk}) with "
                    f"{len(seeded['invoices'])} overdue invoices."
                )
            )
        else:
            gym = resolve_gym(gym_identifier)

        synthetic_run = not options["live"]
        report = None
        reports = []
        # A synthetic run gets the offline gateway, so the tier-3 discount path is
        # actually exercised rather than failing on an absent API key. A live run gets
        # `None` and the agent resolves the configured adapter as normal.
        adapter = SyntheticGatewayAdapter() if synthetic_run else None

        for round_number in range(1, rounds + 1):
            agent = AIRevenueRecoveryAgent(
                tenant_id=gym.pk,
                notify=options["notify"],
                seed=options["seed"],
                adapter=adapter,
            )
            report = agent.run_recovery_batch(gym.pk, synthetic=synthetic_run)
            reports.append(report)

            if rounds > 1:
                self.stdout.write(
                    f"Round {round_number}/{rounds}: {report.summary_line(symbol)}"
                )

            # An invoice that fell into no bucket, or a rupee that cannot be accounted
            # for, means the measurement is wrong - which matters more than the batch
            # finishing. A report nobody can reconcile is worse than no report.
            if report.accounted_for != report.processed:
                raise CommandError(
                    f"Round {round_number} accounted for {report.accounted_for} of "
                    f"{report.processed} invoices. The measurement is unsound."
                )
            if not report.reconciles:
                raise CommandError(
                    f"Round {round_number} does not reconcile: recovered + pending + "
                    f"stopped + discounts = {report.closing_balance}, but "
                    f"{report.outstanding_before} was outstanding at the start."
                )

        # Multi-round runs lead with the campaign total. The final round's own report
        # follows, labelled, because on a `--rounds 4` run that round is the one where
        # the stopping rule has taken over and it recovers nothing by construction.
        round_label = ""
        if len(reports) > 1:
            for line in format_campaign_summary(reports, symbol):
                self.stdout.write(line)
            round_label = f"final round, {len(reports)} of {len(reports)}"

        for line in format_report(
            report,
            symbol=symbol,
            with_reasoning=options["reasoning"],
            round_label=round_label,
        ):
            self.stdout.write(line)

        recovered = ledger_recovered(gym.pk)
        self.stdout.write(
            self.style.SUCCESS(
                f"Recovery batch complete over {len(reports)} round(s): "
                f"{money(recovered, symbol)} recovered, measured from the ledger."
            )
        )
