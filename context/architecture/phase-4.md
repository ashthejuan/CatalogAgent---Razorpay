# Phase 4 — Merchant Agent (Architecture Note)

Companion to `CatalogAgent-PRD.md` / `architecture.md` / `build-plan.md` Steps 4.1–4.4.

**Division of labor:** the LLM proposes; Python validates (Gate 2) and `policy.check` decides (Gate 3). No Razorpay in Phase 4 — orders are created in Phase 6 only after PASS.

---

## 4.1 — LLM client (`app/llm_client.py`)

**Transport only.** Parsing, `CounterOffer.model_validate`, and `policy.check` live in the merchant agent loop — not here.

| Export | Purpose |
|--------|---------|
| `LLMClient.chat_with_tools(system_prompt, messages, tools)` | POST `{base}/chat/completions`; return first tool call `{name, arguments}` (args JSON-parsed) or `None` if text-only |
| `counter_offer_tools()` | Single OpenAI function tool `counter_offer`; JSON Schema from `CounterOffer.model_json_schema()` so fields cannot drift from `schemas.py` |

**Config (env only):** `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` via `app.config` / `os.environ`. No branching on provider name.

**Retry:** one retry + 0.5s backoff on timeout / 5xx; no retry on 4xx (fail loud).

**Layout:** lives at `app/llm_client.py` (not under `agents/`) so merchant and buyer (Phase 5) share transport without importing each other.

**Tests:** `tests/test_llm_client.py` — mocked httpx; no live provider calls.

---

## 4.2 — Merchant agent loop (`app/agents/merchant.py`)

One merchant turn per call. LLM proposes via `counter_offer`; Python decides counter / accept / escalate. Never imports `payments`.

### Entry points

```python
run_merchant_turn(
    negotiation_id, product_id, turn, turn_count,
    buyer_offer, history=None, last_valid_buyer_offer=None,
    max_turns=4, llm=None,
) -> MerchantTurnResult

run_turn(negotiation, buyer_offer, llm=None)  # wrapper over persisted Negotiation
```

| Field | Meaning |
|-------|---------|
| `turn` | Audit turn index (1-based in HTTP layer) |
| `turn_count` | `PolicySession.turn_count` — increments each `/negotiate` cycle |
| `last_valid_buyer_offer` | Last buyer package that passed policy; used at max turns |

### Return actions

| `action` | When |
|----------|------|
| `counter_offer` | LLM proposal PASS, or margin-FAIL with `best_legal_counter` fallback |
| `accept` | Buyer offer already PASSes `policy.check`, or `turn_count >= max_turns` with a stored valid buyer offer |
| `escalate` | structural FAIL (sub-MOQ, stock, turn-limit), malformed ×2, or max turns with no valid buyer offer |

### Gates

1. **Turn limit** — at `turn_count >= max_turns`, skip LLM; accept or escalate.
2. **Buyer already legal** — if `policy.check(buyer_offer)` PASSes, accept immediately (no LLM).
3. **Gate 2** — `CounterOffer.model_validate(tool_args)`; text-only / wrong tool → `malformed_proposal` audit (FAIL), one re-prompt, then escalate.
4. **Gate 3** — `policy.check(offer, PolicySession(...))`; two-row audit (proposal + `guardrail_check` verdict).

### Severity routing (FAIL)

- **Structural** (`below minimum tier MOQ`, `> stock`, `>= max_turns`) → `escalate_to_human`, no counter.
- **Margin / soft** (floor, composite margin, terms, rush) → `best_legal_counter(session)`; FAIL verdict + fallback proposal both audited.

### LLM context

System prompt teaches verified engine semantics (floor per tier, recurring concession, terms/rush margin cost, MOQ rejection). User messages JSON: product (with internal floors), buyer last offer, turn counts, prior turns. Only `counter_offer` tool is exposed to the LLM; accept and escalate are server-side.

### Import constraint

`merchant.py` imports `policy`, `db`, `schemas`, `llm_client` only — never `payments`. Asserted by tests.

**Tests:** `tests/test_merchant.py` — FakeLLM: PASS, margin fallback, structural escalate, malformed retry/escalate, max-turn accept/escalate.

---

## 4.3 — Negotiation endpoints

### Tables (`db.init_db`)

**negotiations** — `id`, `buyer_id`, `product_id`, `initial_volume`, `turn_count`, `history` (JSON), `last_valid_buyer_offer` (JSON, nullable), `status` (`OPEN` | `CLOSED_WON` | `ESCALATED`).

**orders** — `id`, `negotiation_id`, `terms`, `razorpay_order_id`, `invoice_path` — schema only until Phase 6.

### Auth

`app/auth.py` — `require_buyer` reads `X-Buyer-Key`, hashes via `create_buyer.hash_buyer_key`, looks up `buyers`. Missing/invalid → **401**.

Ownership: negotiation `buyer_id` must match key's `buyer_id` → else **403**. Applies to `/negotiate` and `/audit/{id}`.

### Routes

**`POST /quote`** — body `{product_id, buyer_id, initial_volume?}`. Creates OPEN negotiation, audits `system: negotiation_opened` (turn 0), returns `{negotiation_id}`.

**`POST /negotiate`** — body `{negotiation_id, buyer_offer: CounterOffer}`. One turn:

1. Audit buyer proposal (`buyer_agent`, no verdict).
2. Append to `history`.
3. `policy.check` on buyer offer **for tracking only** — if PASS, store `last_valid_buyer_offer` (buyer is never blocked).
4. `merchant.run_turn(negotiation, buyer_offer)` → accept / counter / escalate.
5. Status: `CLOSED_WON` on accept, `ESCALATED` on escalate, else `OPEN`.
6. Increment `turn_count`, return `{status, merchant_move, audit_excerpt, final_terms?}`.

No Razorpay order on accept (Phase 6).

**`GET /audit/{id}`** — buyer key + ownership required.

### Files

- `app/db.py` — negotiations + orders tables, CRUD, `audit_excerpt`
- `app/auth.py` — Gate 1
- `app/schemas.py` — `QuoteBody`, `NegotiateBody`, response models
- `app/main.py` — `/quote`, `/negotiate`, gated `/audit`
- `app/agents/merchant.py` — `run_turn` wrapper

**Tests:** `tests/test_negotiate_routes.py` — 401/403/422, adversarial buyer routed, turn increment, accept/escalate, audit ownership.

---

## 4.4 — Demo harness stub

`demo/run_demo.py` — hardcoded (non-LLM) buyer over `TestClient`:

1. Seed + provision `demo_buyer`
2. Stub merchant `LLMClient` to return a below-floor `counter_offer` (Gate 3 FAILs)
3. `POST /quote` → `POST /negotiate` with buyer lowball (25% under floor)
4. Assert merchant move is `best_legal_counter` that itself `policy.check` PASSes
5. Print audit trail: proposal → FAIL → fallback

```bash
cd backend && python -m demo.run_demo
```

### Phase 4 suite (`tests/test_phase4.py`)

| Test | Asserts |
|------|---------|
| `test_quote_creates_negotiation` | 200 + `negotiation_opened` |
| `test_negotiate_bad_key_401` | 401 |
| `test_foreign_negotiation_403` | 403 |
| `test_malformed_body_422` | 422 |
| `test_merchant_lowball_gets_legal_counter` | fallback passes `policy.check` |
| `test_merchant_accepts_valid_offer` | `CLOSED_WON` |
| `test_audit_two_rows_per_turn` | buyer + merchant proposal + policy verdict |
| `test_no_payments_import` | AST: no `payments` in merchant/main |
| `test_merchant_malformed_toolcall_escalates` | text×2 → `ESCALATED` |

### Hard rules

- LLM proposes; `policy.check` decides PASS/FAIL
- Merchant proposals always get proposal row + verdict row
- `merchant.py` / `main.py` never import `payments`
- No Razorpay in Phase 4
