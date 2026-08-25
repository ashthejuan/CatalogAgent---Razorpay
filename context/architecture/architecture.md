# CatalogAgent — Architecture

Companion to `CatalogAgent-PRD.md` (same repo, `razorpay/`). This document is the system-level view: components, data flow, request lifecycle, and storage layout.

---

## 1. System overview

Single Python process (FastAPI + uvicorn). The merchant IS the server; buyer agents are external HTTP clients. No message queues, no Docker, no frontend framework.

```
┌─────────────────────────────────────────────────────┐
│                  FastAPI app (one process)           │
│                                                      │
│  GET  /catalog         agent-readable catalog JSON   │
│  POST /quote           buyer opens a negotiation     │
│  POST /negotiate       one negotiation turn          │
│  GET  /orders/{id}     final agreed terms            │
│  GET  /invoices/{id}   invoice PDF download          │
│  GET  /audit/{id}      full audit trail              │
└──────┬──────────────┬───────────────┬────────────────┘
       │              │               │
   SQLite         LLM client     Invoice renderer
  (5 tables)     (structured     (reportlab)
                  tool-calling)        │
                                      ▼
                                 invoices/*.pdf
```

## 2. Component responsibilities

| Component | Responsibility | Constraint |
|---|---|---|
| FastAPI routes | HTTP surface, request parsing | Thin — logic lives below |
| Pydantic schemas (`CounterOffer`, `QuoteRequest`, `OrderTerms`) | Single source of truth for a terms package | Reused for LLM structured output + request validation + guardrail input |
| Policy engine | Deterministic bounds enforcement | Pure Python, ZERO LLM calls; judge-readable in ~2 min |
| Merchant agent | Lever selection across the five contract variables | Only layer allowed to reason with an LLM |
| Buyer agent simulator | Adversarial counterparty | Separate module/prompt/budget — never shares context with merchant agent |
| Audit logger | Append-only record of every proposal/verdict | INSERT/SELECT only, enforced in code |
| Invoice renderer | PDF from finalized order row | Filled from DB values only, never LLM output |
| Key provisioning CLI | Creates buyers, prints key once | Raw key in `.env`/stdout once; DB stores hash only |

## 3. Request lifecycle — one `/negotiate` turn

```
buyer agent
   │  POST /negotiate  {negotiation_id, ...}   header: X-Buyer-Key
   ▼
[GATE 1] require_buyer dependency ── hash lookup in buyers table,
   │        hmac.compare_digest; unknown key → 401
   │        ownership check: negotiation.buyer_id == buyer.buyer_id → else 403
   ▼
[GATE 2] merchant LLM proposes via tools → CounterOffer.model_validate()
   │        malformed/hallucinated → 422-style rejection, audited FAIL
   ▼
[GATE 3] policy_engine.check(offer, session)
   │        ├─ per-field bounds (price floor by tier, terms days,
   │        │   lead time, stock, turn count)
   │        └─ composite margin (payment-terms cost, rush cost)
   ▼
audit_log INSERT ×2  (proposal row + verdict row)
   │
   ├─ PASS ─▶ offer stands; if accept_offer():
   │            orders row written
   │              ▼
   │          [RAZORPAY — the single money-action boundary]
   │          POST https://api.razorpay.com/v1/orders  (test-mode,
   │          key_id:key_secret) with amount = unit_price × min_volume
   │          in paise, receipt = negotiation_id, notes = agreed terms
   │              ▼
   │          razorpay_order_id stored on orders row; audit final row
   │          links negotiation_id → razorpay_order_id
   │              ▼
   │          invoice generated (references that order id)
   └─ FAIL ─▶ graceful fallback: best_legal_counter(session) or
              escalate_to_human(reason); negotiation continues or ESCALATED
```

Blocking never crashes: legitimate policy failures return an explained response so a well-behaved buyer can recover. Transport failures get a bare 401.

**Razorpay integration scope:** order creation ONLY at deal acceptance. No payment capture, no checkout.js, no webhooks — B2B deals settle as contract + invoice (real wholesalers pay on net-15/net-30 terms), and order creation is the track's "test-mode money action." It is also the gating boundary made physical: the Razorpay client is unreachable from any code path that has not passed Gate 3, so untrusted LLM output can never create an order directly. Demo beat: show the created order in the Razorpay test dashboard next to the generated invoice.

## 4. Data model (SQLite)

```sql
products      (id, name, base_unit_price, volume_tiers JSON, stock,
               lead_time_options JSON)            -- tiers carry floor prices
buyers        (buyer_key_hash UNIQUE INDEXED,     -- SHA-256; plaintext NEVER stored
               buyer_id, budget_cap)
negotiations  (id, buyer_id, product_id, qty, turn_count,
               current_offer JSON, status)        -- OPEN/ACCEPTED/BLOCKED/ESCALATED
audit_log     (id, negotiation_id, turn, actor, action,
               payload JSON, verdict, reason)     -- APPEND-ONLY
orders        (id, negotiation_id, terms JSON, razorpay_order_id, invoice_path)
```

Actors in audit_log: `buyer_agent` | `merchant_llm` | `policy_engine` | `system`.
Reason strings are always Python-generated structured text (`"unit_price 9.50 < floor 9.80 for tier 3000+"`) — never LLM output.

## 5. The five-variable contract

Every deal is a package; the guardrail evaluates the whole package:

| Term | Field | Merchant cost when conceded |
|---|---|---|
| Unit price | `unit_price` | Direct margin |
| Volume / MOQ | `min_volume` | Higher volume lowers unit cost → enables price concession |
| Payment terms | `payment_terms_days` | Working-capital cost per day of delay |
| Delivery timeline | `delivery_days` | Rush overtime below standard lead time |
| Order commitment | `recurring` | Recurring revenue justifies better rate |

Division of labor: **LLM chooses which lever(s) to move** (non-table-shaped reasoning over buyer behavior across turns); **Python decides whether the chosen package is legal at all.**

## 6. Storage decisions (demo ↔ production)

| Concern | Demo build | Production path |
|---|---|---|
| Catalog/state/logs | SQLite (5 tables) | DynamoDB |
| Invoice files | local `invoices/` behind `save_invoice(order) -> url` wrapper | S3 + 15-min pre-signed URLs |
| Audit durability | append-only table | DynamoDB + CloudWatch; S3 Object Lock / QLDB ledger archival |
| Orchestration | in-process loop | Step Functions state machine (visible execution graph) |
| Credentials | static hashed API keys | Razorpay-style key/secret pairs → short-lived signed tokens → AP2 mandates |

Isolation rule: every infrastructure touchpoint sits behind a small interface (`save_invoice`, `db.get_buyer_by_hash`, LLM client wrapper) so any demo→prod swap is a ~20-line change.

## 7. Module layout

Code lives under `backend/` (Phase 0.1 scaffold). See `context/architecture/phase-0.1-scaffold.md`.

```
backend/
├── app/
│   ├── main.py            # FastAPI routes (thin); Phase 0.1: GET /health only
│   ├── schemas.py         # Pydantic: CounterOffer, QuoteRequest, OrderTerms, Verdict
│   ├── db.py              # sqlite access; audit INSERT helpers
│   ├── policy.py          # policy engine — pure, zero LLM, heavily tested
│   ├── agents/
│   │   ├── merchant.py    # merchant agent + tool definitions
│   │   ├── buyer.py       # adversarial buyer simulator (isolated context)
│   │   └── llm_client.py  # thin OpenAI-compatible wrapper (provider-swappable)
│   ├── invoicing.py       # reportlab renderer + save_invoice() wrapper
│   ├── payments.py        # ~30 lines: create_order(terms) -> razorpay_order_id
│   │                      # httpx POST /v1/orders, key:secret from .env (test mode).
│   │                      # Callable ONLY after policy-engine PASS — gating boundary.
│   └── create_buyer.py    # CLI: python -m app.create_buyer acme --budget 50000
├── tests/
│   └── test_smoke.py      # Phase 0.1; test_policy.py comes before agents (build-order)
├── invoices/              # generated PDFs
└── demo/
    └── run_demo.py        # E2E harness; prints audit table (video source) — later phase
```

## 8. Deliberate non-decisions

- **No LangChain/LangGraph** — a while-loop with tools is sufficient and debuggable.
- **No vector DB/RAG** — nothing requires retrieval.
- **No login flows/tokens** — see PRD auth decision record; static hashed keys are proportionate.
- **No frontend** — CLI + JSON endpoints; judges read code.
