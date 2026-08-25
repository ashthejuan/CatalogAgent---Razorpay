# CatalogAgent — Build Plan

Step-by-step build flow, companion to `CatalogAgent-PRD.md` and `architecture.md`.
Rule of thumb per step: **done = runs + tested + committed**, not "code exists."

---

## Phase 0 — Setup (half day)

### Step 0.1 — Project scaffold

- [x] Create repo layout under `backend/` with the module layout from architecture.md §7
- [x] `pyproject.toml` / requirements: fastapi, uvicorn, pydantic, httpx, reportlab, pytest
- [x] `.gitignore`: `.env`, `invoices/`, `*.db`, `__pycache__`
- [x] `.env.example` committed; real `.env` local only (`RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `LLM_API_KEY`, `LLM_BASE_URL`, optional `KEY_PEPPER`)
- [x] Empty test dir wired to pytest; first trivial test passes
- [x] Pushed to existing GitHub remote `CatalogAgent---Razorpay` (Phase 0.1 commit)



### Step 0.2 — External accounts (do now, they block later steps)

- [x] Razorpay account → grab **test-mode** key_id/key_secret from dashboard
- [x] LLM provider decided + API key in `.env` (structured output/tool-calling required)
- [x] Verify both with throwaway scripts: one curl to `/v1/orders` test endpoint, one completion call

**Exit criteria:** `uvicorn app.main:app` serves an empty `/health`; both external creds verified.

---



## Phase 1 — Catalog & identity (Day 1)



### Step 1.1 — Schema + DB foundation

- [x] `schemas.py`: Pydantic models — `Product`, `VolumeTier`, `CounterOffer` (all 5 fields), `QuoteRequest`, `OrderTerms`, `Verdict`
- [x] `db.py`: sqlite init creating `products` + `buyers` (negotiations/audit/orders deferred to later phases)
- [x] Seed script: ~10 products across 3 categories, each with 3–4 volume tiers incl. floor prices



### Step 1.2 — Key provisioning CLI

- [x] `create_buyer.py`: takes name + budget cap → generates `bk_...` key → prints ONCE → stores SHA-256 hash (+pepper if set) in `buyers`
- [x] Test: provision 3 buyers (`acme`, `globex`, `initech`) for demos; keys into `.env`



### Step 1.3 — `GET /catalog`

- [x] Route returns **catalog-safe** JSON — `CatalogProduct` view strips `floor_price` (internal negotiation floor) from public response
- [x] README curl example works against running server
- [x] Test: response validates against `CatalogProduct`; `test_catalog_hides_floor_price` asserts no `floor_price` leaks

**Exit criteria:** `curl localhost:8000/catalog | jq` shows clean JSON with tiers exposing only `min_qty`/`unit_price`; buyers table has hashed rows only.

---



## Phase 2 — Policy engine (Days 1–2) ⚠️ BEFORE any LLM code



### Step 2.1 — Core checks

- [x] `policy.py`: `check(offer, session) -> Verdict`
- [x] Per-field bounds: price ≥ tier floor (tier resolved from qty), payment_terms_days ≤ max, delivery_days ≥ lead-time min, qty ≤ stock, turn < max_turns
- [x] Composite margin: effective margin = unit_price − COGS_floor − payment-terms cost − rush-delivery cost; FAIL below threshold
- [x] Every FAIL carries a structured Python reason string
- [x] `best_legal_counter(session)` helper (used by graceful fallback later)



### Step 2.2 — Test suite (the moat's proof)

- [x] PASS case at exact floor (standard-lead delivery isolates the price boundary)
- [x] FAIL cases: each field violated individually; reason keyword asserted
- [x] Composite-margin trap: floor price + net-45 terms → FAIL
- [x] **Sub-MOQ guard**: volume below lowest tier MOQ → FAIL (not graded against top tier)
- [x] **Rush-delivery margin**: delivery beating standard *max* lead erodes margin
- [x] **Recurring lever**: package that FAILS one-off PASSES when `recurring=True` (correct sign)
- [x] Turn-limit exhaustion
- [x] Property test: fuzz 500 random offers, assert no illegal offer ever gets PASS
**Exit criteria:** `pytest` green (26 tests); policy.py readable top-to-bottom by a non-author. Do not proceed until this is true.

---



## Phase 3 — Audit trail (Day 2, half day)



### Step 3.1 — Logger + exposure

- [x] `append_audit()` helper (plan's `audit.log()`): single INSERT path; no UPDATE/DELETE on `audit_log` anywhere in codebase (asserted by test)
- [x] Log convention: proposal row (actor=`merchant_llm`/`buyer_agent`) + verdict row (actor=`policy_engine`, verdict, reason) for every check
- [x] `GET /audit/{negotiation_id}` returns ordered trail (`trail` JSON + `text` table); HTTP test covers both present and empty
- [x] `format_audit_trail()`: aligned table; multi-turn snapshot test asserts side-by-side PASS/FAIL rows
- [x] Architecture note: `context/architecture/phase-3-audit.md`

**Exit criteria:** hand-crafted negotiation session produces a correct readable table via the printer; `/audit/{id}` returns it over HTTP. (Ownership/auth gating of `/audit` deferred to Phase 4 with the `negotiations` table.)

---



## Phase 4 — Merchant agent (Days 2–3)



### Step 4.1 — LLM client wrapper

- [ ] `llm_client.py`: thin OpenAI-compatible client; tool-schema support; retry on transient errors; provider swappable via env vars



### Step 4.2 — Agent loop

- [ ] `agents/merchant.py`: system prompt (margins, floors context, five variables, buyer behavior so far); tools: `counter_offer(...)`, `accept_offer()`, `escalate_to_human(reason)`
- [ ] Loop: build prompt → call LLM → parse tool call through `CounterOffer.model_validate()` (**Gate 2**) → malformed ⇒ audited FAIL `malformed_proposal` + re-prompt once, then escalate
- [ ] Wire Gate 3 after every parsed proposal; PASS/FAIL both audited
- [ ] On FAIL: return `best_legal_counter` fallback or escalation per severity



### Step 4.3 — Negotiation state endpoints

- [ ] `POST /quote` (Gate 1 protected): creates OPEN negotiation
- [ ] `POST /negotiate` (Gate 1 + ownership): runs ONE turn per architecture.md §3 lifecycle; explicit turn-per-call design
- [ ] Route tests: bad key → 401; foreign negotiation → 403; malformed body → clean rejection

**Exit criteria:** scripted buyer (no LLM, hardcoded offers) can drive a multi-turn negotiation via HTTP; audit table renders correctly at each step; a hardcoded out-of-bounds offer is blocked gracefully.

---



## Phase 5 — Buyer adversary + full negotiation (Days 3–4)



### Step 5.1 — Adversarial buyer agent

- [ ] `agents/buyer.py`: separate module, separate system prompt, own budget cap, aggressive tactics (repeated lowballs, abandonment threats, probing volume tiers)
- [ ] Same tool-call discipline: structured proposals only
- [ ] NO shared context with merchant agent (verify: no imports between them beyond schemas/db)



### Step 5.2 — Multi-turn orchestration

- [ ] Demo harness `run_demo.py`: scenario runner driving N turns over HTTP until accept/block/escalate/max-turns
- [ ] Scenarios: (a) reasonable buyer → deal closes; (b) aggressive lowballer → guardrail wall → graceful counters → escalation; (c) creative reroute — merchant holds price but concedes terms/volume instead
- [ ] Each scenario ends printing the audit table

**Exit criteria:** all three scenarios run green end-to-end repeatedly (not cherry-picked); scenario (b)'s audit table shows proposal→FAIL→fallback rows side by side. Record nothing yet.

---



## Phase 6 — Money action + invoice (Day 4–5)



### Step 6.1 — Razorpay order creation

- [ ] `payments.py`: `create_order(terms)` → POST `/v1/orders` (amount paise, receipt=negotiation_id, notes=terms); store `razorpay_order_id` on orders row; final audit row links them
- [ ] **Structural gating check:** confirm no import path lets agents/routes call payments before policy PASS (grep review)
- [ ] Verify order appears in Razorpay **test dashboard**; screenshot for video



### Step 6.2 — Invoice PDF

- [ ] `invoicing.py`: reportlab template — all five agreed terms + product + parties + razorpay_order_id; values ONLY from the orders row
- [ ] `save_invoice(order) -> url` wrapper; `GET /invoices/{id}` serves via FileResponse with ownership check
- [ ] Cross-check test: every number on the PDF equals its source DB value (parse-back assertion)
- [ ] Traceability demo: pick one invoice line → matching audit row → show 1:1

**Exit criteria:** closed deal yields order in Razorpay dashboard + downloadable PDF whose numbers provably match the audit trail.

---



## Phase 7 — Polish & deliverables (Day 5)



### Step 7.1 — README (graded artifact #1)

- [ ] Top: bar language verbatim — bounded / explainable / audit trail / failure handled gracefully
- [ ] Approved-vs-blocked audit rows side by side near the top
- [ ] Architecture diagram (architecture.md §1), security summary (security.md pitch paragraph), AWS production-path table
- [ ] Quickstart: clone → `.env` → seed → create_buyer → run_demo → curl examples



### Step 7.2 — Pitch video (graded artifact #2, ≤5 min)

Shot list:

1. Problem framing: B2B procurement is agentic-commerce's beachhead (30s)
2. `GET /catalog` live in terminal (20s)
3. Scenario (a) run: turns scrolling, deal closes (45s)
4. Scenario (b): lowball → FAIL verdict → graceful fallback — slow down here (60s)
5. Scenario (c): creative reroute — LLM moves terms not price (45s)
6. Invoice download + same order visible in Razorpay dashboard (30s)
7. Closing frame: full audit table + the two-sentence security summary (40s)



### Step 7.3 — Final sweep

- [ ] `grep -r "bk_" . --exclude=.env*` → no raw keys anywhere in repo
- [ ] Fresh-clone smoke test: someone could run it from README alone
- [ ] ngrok command ready in README ("live demo" section) for panel stage
- [ ] Push final tag; submit via the Google Form

---



## Dependency map (what blocks what)

```
Phase 0 ─▶ Phase 1 ─▶ Phase 2 ─▶ Phase 3 ─▶ Phase 4 ─▶ Phase 5 ─▶ Phase 6 ─▶ Phase 7
 (setup)   (catalog)  (policy!)  (audit)    (merchant)  (adversary)  (money+PDF)  (ship)
                └──────────────────────────┬─────────────┘
                                    Phase 2 gates everything:
                                    no LLM code until tests are green
```



## Time-box warnings


| Trap                     | Budget rule                                      |
| ------------------------ | ------------------------------------------------ |
| LLM provider fiddling    | Max 2h total; wrapper makes swaps cheap          |
| Invoice styling          | Half day hard cap — content correctness > beauty |
| Extra scenarios/features | Nothing new after Phase 6 starts                 |
| Auth/tokens revisit      | Closed by PRD decision record — don't reopen     |


