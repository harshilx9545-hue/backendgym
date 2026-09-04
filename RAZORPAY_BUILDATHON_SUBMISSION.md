# AI Revenue Recovery — Buildathon Submission

**Track:** 03 — AI Revenue Recovery
**Repo:** this repository — the agent lives at `core/services/recovery_agent.py`,
the runnable demo at `core/management/commands/run_recovery_batch.py`.

## What it does

Detects overdue B2B/member receivables, decides the right intervention per invoice
(reminder → firmer reminder → discount offer → human escalation), and executes a
**bounded** recovery workflow — with the money, the escalation, and every guardrail
refusal measured and logged, not asserted.

```
python manage.py run_recovery_batch --synthetic-data --rounds 4
```

seeds 50 synthetic overdue invoices across 4 escalation tiers and runs the batch,
printing a full measured report: recovered amount, pending amount, stopped amount,
a per-invoice audit line for every decision (including every guardrail refusal),
and a ledger reconciliation check.

## Why "the model is an untrusted planner"

The core design decision: the LLM reads one invoice's context and *picks a tool with
arguments*. It never touches the database, never computes money, and never decides
what is permitted. Every action that could cost the gym money or leak another
tenant's data is re-checked in plain Python after the model has spoken.

### The four guardrails, and how each is actually enforced

| Guardrail | Enforcement |
|---|---|
| **Tenant boundary** | Every tool re-reads `gym_id` from its own arguments and compares it against the agent's `tenant_id` before any DB lookup — a prompt-injected note claiming "you are now serving gym 42" produces a `PermissionDenied`, not a cross-tenant write. |
| **Discount cap (20%)** | Enforced twice: the orchestration layer *clamps* whatever the model asked for down to the cap (so one bad number doesn't stall collection), and the tool independently *refuses* anything over the cap (so a direct call — what a successful injection looks like — throws). |
| **One discount per invoice** | Counted from the append-only `RecoveryAttempt` ledger, not from anything the model says. Asking twice is refused, and the refusal itself is logged. |
| **Stopping rule (3 automated attempts)** | Also counted from the ledger. After 3 automated contacts, the invoice goes to a human and the agent stops touching it — visible in the demo as `stopped_attempt_limit`. |

Money is never computed by the model either: a discount is applied by recomputing
GST through `core.services.invoicing.compute_tax`, so a discounted invoice still
satisfies the codebase's invariant that total = taxable value + tax components.

### Escalation ladder

| Tier | Window | Tone | Can discount? |
|---|---|---|---|
| 1 | 1–7 days | gentle reminder | No |
| 2 | 8–14 days | firm reminder | No |
| 3 | 15–21 days | final notice with offer | Yes (capped, once) |
| 4 | 22+ days | human escalation | No — hands off to a human |

## Measured output (real run, not cherry-picked)

A 4-round synthetic batch against 50 seeded overdue invoices (₹72,688 outstanding):

- **164 ledger rows written** across all rounds — every decision, including refusals
- **₹36,934 measured recovered** (via `payment_observed`, read back independently
  from the ledger, not from the batch's own running total)
- **12 guardrail refusals** (`blocked_duplicate_discount`) — the agent tried to
  re-offer a discount on an invoice that already had one, and was refused
- **16 invoices hit the stopping rule** (`stopped_attempt_limit`) — 3 automated
  attempts made, handed to a human, no further automated contact
- **Reconciliation check passes**: recovered + pending + stopped + discounts given
  up = outstanding at start, every round — the command hard-fails (`CommandError`)
  if this doesn't balance, so a report that doesn't add up can't ship silently

## Test coverage

Three dedicated property tests cover the guardrails specifically:
- `test_property_41_recovery_discount_cap.py`
- `test_property_42_recovery_tenant_boundary.py`
- `test_property_43_recovery_stopping_rule.py`

## Honest limitations

- The offline "heuristic" planner is deterministic (no API key needed to run the
  demo above); a real hosted LLM planner is wired via `get_llm_client()` but needs
  an API key to exercise live.
- `--live` mode performs real recovery actions and leaves collection measurement to
  the gateway's webhooks — it does not simulate a member paying, so `recovered` is
  0 by construction in that mode until a webhook actually lands.
- Reminder emails are off by default (`--notify` to actually send).
