# Razorpay AI Buildathon - submission notes

**Track:** 03 - AI Revenue Recovery

The submission write-up lives in [README.md](README.md): what the agent does, the
one command that runs it, the measured result, the architecture, the four guardrails
and how each is enforced, and the honest limitations.

This file records how the submission maps onto the track's stated bar.

## The bar, and where it is met

The track asks for more than identifying the problem: measured money recovered
across a batch, compliant escalation, stopping rules, and an audit trail.

| Asked for | Where it is |
|---|---|
| **Measured money recovered across a batch** | `Rs.36,674.40` of `Rs.115,050.00` outstanding across 50 invoices, summed from `payment_observed` rows in the `RecoveryAttempt` ledger rather than accumulated by the agent. `CAMPAIGN TOTAL` block, `run_recovery_batch --synthetic-data --rounds 4`. |
| **Compliant escalation** | Four tiers by overdue age, tone fixed per tier, discounts only at tier 3, tier 4 hands off to a human and sends the member nothing. Tier policy is applied after the model chooses, so the model cannot promote itself a rung. |
| **Stopping rules** | Three automated contacts per invoice, counted from the ledger, then the invoice is a human's. Also: one discount per invoice, ever. Both refuse rather than warn, and 27 of 50 invoices are out of automated contact by round 4. |
| **An audit trail** | `RecoveryAttempt`: one append-only row per decision including every refusal, immutable three ways over (append-only manager, `save()` refuses a second write, `delete()` raises). 166 rows over the demo run. |

## What is worth a reviewer's attention

**The model is an untrusted planner.** It picks a tool and arguments. It never
touches the database, never computes money, never decides what is permitted. The
guardrails run in plain Python downstream of it, and none of them consults the
model's opinion of its own limits.

**Refusals are recorded, not swallowed.** 18 `blocked_duplicate_discount` rows in
the demo run are the agent asking for a second discount on an invoice and being
refused. A guardrail that leaves no evidence is a claim.

**The report is a measurement, not a summary.** Every figure is read back from the
database. Each round asserts
`recovered + pending + stopped + discounts given up == outstanding at start` and the
command exits non-zero if it does not balance, so an unreconcilable report cannot
ship quietly.

**The recovered figure is reproducible.** Synthetic member responses derive from
`--seed` and the invoice sequence number, so a fresh clone reproduces
`Rs.36,674.40`. This was a real bug found while preparing the submission: the roll
had been keyed on the full invoice number, which embeds a per-run random gym slug,
so `--seed` did nothing and the reported figure moved by thousands of rupees between
identical runs. Fixed in `_synthetic_roll`.

## Guardrail tests

```
python -m pytest core/tests/test_property_41_recovery_discount_cap.py core/tests/test_property_42_recovery_tenant_boundary.py core/tests/test_property_43_recovery_stopping_rule.py
```

25 tests covering the discount cap, the tenant boundary under prompt injection, and
the stopping rule. Passing.

## Limitations

Stated plainly in the README's "Honest limitations" section. The two that matter
most to a reviewer: the default planner is deterministic and offline (a hosted
OpenAI tool-calling planner is wired but needs a key), and `--synthetic-data`
simulates whether a member pays rather than observing a real collection. The report
labels the mode on every run.
