# Phase 3 — Audit Trail (Architecture Note)

Companion to `CatalogAgent-PRD.md` / `architecture.md`. Documents the append-only audit layer added in Phase 3 and the policy-engine hardening that landed alongside it.

## Audit trail (Phase 3)

### Storage
- `audit_log` table in SQLite (`db.init_db`). Columns: `id` (autoincrement PK), `negotiation_id`, `turn`, `actor`, `action`, `payload` (JSON), `verdict` (PASS/FAIL/NULL), `reason`, `created_at`.
- **Append-only by construction**: the only writer is `db.append_audit()`, a single `INSERT`. No `UPDATE`/`DELETE` on `audit_log` exists anywhere in the codebase — asserted by `test_audit_is_append_only_no_update_delete_in_source`.

### Actors (who logs)
- `buyer_agent` — adversarial counterparty proposals
- `merchant_llm` — negotiation-agent proposals / tool calls
- `policy_engine` — guardrail verdicts (one row per check: `verdict` + machine-generated `reason`)
- `system` — reserved for future (order creation, escalation)

### Convention (the demo artifact)
Every negotiation turn writes **two rows**: a proposal row (`verdict=NULL`) from the proposing actor, then a `policy_engine` verdict row (`PASS`/`FAIL` + structured reason). Side-by-side they prove the system is bounded. The pretty-printer `format_audit_trail()` renders an aligned table used by `GET /audit/{id}` and the demo harness.

### Exposure
- `GET /audit/{negotiation_id}` returns `{negotiation_id, trail (JSON), text (table)}`.
- **Auth status**: endpoint is currently public. Prod intent (PRD §6.7) is buyer-key + ownership gating — deferred to Phase 4 where the `negotiations` table and ownership model land. Tracked, not forgotten.

## Policy engine hardening (Phase 2 follow-up)

Three bugs from review were fixed in `app/policy.py`:

1. **Sub-MOQ tier resolution** — offered `min_volume` below the lowest tier MOQ previously fell through to `default=tiers[-1]` (the *highest* tier, most lenient floor), letting an adversarial buyer claim a tiny volume and be graded against the cheapest floor. Now: no qualifying tier → `FAIL` "min_volume X below minimum tier MOQ Y". (`test_sub_moq_fails`.)
2. **Rush-delivery cost was dead code** — the margin penalty duplicated the hard `delivery_days < lead_time_min_days` gate and was unreachable. Re-pointed the rush penalty at `lead_time_max_days` (the *standard* lead time): any delivery beating the standard max erodes margin. Delivery now has real economic weight, not just a hard floor. (`test_rush_delivery_margin_trap`.)
3. **`recurring` inverted sign** — shipped as `margin -= RECURRING_CONCESSION`, so recurring *hurt* a package (punishing the merchant-friendly outcome). PRD/architecture intent: recurring commitment is *value to the merchant* (predictable revenue), so it should *help* a tight package pass. Fixed to `margin += RECURRING_CONCESSION`. The test now proves the real lever: a package that FAILS one-off (floor price + a 1-day rush nick) PASSES when `recurring=True` — i.e. the LLM can hold price and win on commitment. (`test_recurring_is_considered`.)

Plus: `PASS` reason now reports effective margin % (demo-readable), and the 500-case fuzz recomputes against the updated formulas (incl. rush-off-max and recurring) so it stays a true regression guard.

Constants live at module top (`TERMS_COST_PER_DAY`, `RUSH_COST_PER_DAY`, `RECURRING_CONCESSION`, `MAX_PAYMENT_TERMS_DAYS`) so tuning is a one-line change.
