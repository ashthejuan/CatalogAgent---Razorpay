# CatalogAgent — Technical PRD

**Project:** B2B agentic procurement negotiation system with bounded autonomy
**Track:** Razorpay AI Buildathon — 01 · AI Growth & Agentic Commerce
**Author:** Akshit Dasgupta
**Status:** Approved for build
**Last updated:** 2026-08-25

---

## 1. Summary

CatalogAgent is a merchant-side AI procurement agent that negotiates multi-variable deals (price, volume, payment terms, delivery timeline, order commitment) with an autonomous buyer agent over a machine-readable catalog API. The LLM explores the contract space creatively; a deterministic policy engine guarantees no agreed term ever crosses the merchant's bounds. Every proposal, guardrail verdict, and final term is written to an append-only audit trail, and each closed deal produces a deterministic invoice PDF rendered from the audit data.

**One-line pitch:** *In agentic commerce the negotiation isn't over a price, it's over a contract — CatalogAgent lets the LLM explore that contract space while code guarantees it never signs outside the merchant's bounds.*

## 2. Why this exists

NPCI's UAP and global protocols (ACP, AP2, x402) are standardizing agent-to-agent commerce; Razorpay's in-app pilots are already live. In B2B wholesale, humans already negotiate volume pricing, payment terms, and lead times by phone/email — slow and unscalable when buyer side becomes agentic. The merchant needs a counterpart that can negotiate at machine speed **without giving away margin**, which is exactly what unbounded LLM agents would do.

## 3. Track bar mapping (graded criteria → where we satisfy them)

| Track bar | Where satisfied |
|---|---|
| Every money action explainable | Audit log: every proposal + guardrail verdict with structured reason |
| Bounded | Policy engine: per-field bounds + composite margin check, deterministic code |
| Gated | No Razorpay order created unless guardrail PASS on the full terms package |
| Show the audit trail | Append-only SQLite table + `GET /audit/{id}` endpoint + demo harness table render |
| One failure handled gracefully | Adversarial run: guardrail blocks out-of-bounds offer → logged FAIL → graceful counter/escalation returned, never a crash |

## 4. Scope

### In scope
1. Agent-readable catalog endpoint (JSON, tiered volume pricing)
2. Multi-turn negotiation between two independent LLM-driven parties (merchant agent + adversarial buyer simulator)
3. Deterministic policy engine validating full terms packages
4. Append-only audit trail exposed via API
5. Deterministic invoice PDF generation from finalized orders
6. Demo harness script producing a complete recorded run (success + blocked-failure)
7. README with AWS production-path mapping

### Out of scope
- Real frontend UI (CLI/script + JSON endpoints suffice)
- Real payment capture beyond Razorpay test-mode order creation
- Login flows, session auth, dynamic token issuance (see §6.7 — static scoped API keys instead)
- RAG / vector stores (nothing here requires retrieval)

### Auth decision record
Buyer parties authenticate with **static scoped API keys** (hashed at rest) — the same credential model as Razorpay's own test-mode key/secret. No login flows: buyer actors are LLM agents driving HTTP calls, not humans at a form; a dual-login UI would reframe an M2M system as a web app and add zero graded value. Dynamic token issuance (client-credentials `/token` flow) is documented in the production path but not built — with three synthetic buyers it adds demo friction and no security (the registration/bootstrap endpoint would itself become the perimeter). Proportionality is deliberate.

## 5. Architecture

```
┌─────────────────────────────────────────────────────┐
│                  FastAPI app (one process)           │
│                                                      │
│  GET  /catalog         agent-readable catalog JSON   │
│  POST /quote           buyer requests quote          │
│  POST /negotiate       one negotiation turn          │
│  GET  /orders/{id}     final order + terms           │
│  GET  /invoices/{id}   invoice PDF download          │
│  GET  /audit/{id}      full audit trail              │
└──────┬──────────────┬───────────────┬────────────────┘
       │              │               │
   SQLite         LLM client     Invoice renderer
  (4 tables)     (structured     (reportlab)
                  tool-calling)        │
                                      ▼
                                 invoices/*.pdf
```

Single Python process. No frameworks (LangChain/LangGraph), no message queues, no Docker.

## 6. Components

### 6.1 FastAPI application
- `uvicorn` served; Pydantic models define `CounterOffer`, `QuoteRequest`, `OrderTerms` once and reuse them for: LLM structured output schema, request validation, and guardrail input.

**Endpoints**

| Route | Method | Behavior |
|---|---|---|
| `/catalog` | GET | Products with id, name, base unit price, volume tiers (`[{min_qty, unit_price}]`), stock, lead-time options. Machine-parseable, documented with curl example in README. |
| `/quote` | POST | Buyer submits desired product/qty/terms → creates `negotiations` row (status=OPEN) → returns negotiation_id. |
| `/negotiate` | POST | Runs ONE turn: buyer agent proposes → merchant LLM reasons → structured counter via tools → policy engine validates → outcome (counter/accept/escalate/block) persisted + audited. Explicit turn-per-call design makes the demo replayable and inspectable. |
| `/orders/{id}` | GET | Final agreed terms after ACCEPT. |
| `/invoices/{id}` | GET | `FileResponse` of deterministic PDF. |
| `/audit/{id}` | GET | Full ordered audit trail for a negotiation. |

### 6.2 SQLite (4 tables)
- **products** — catalog incl. per-tier floor prices (guardrail input).
- **buyers** — `buyer_key_hash` (SHA-256, indexed; plaintext never stored), `buyer_id`, `budget_cap`. Provisioned via CLI.
- **negotiations** — session state: buyer_id, product_id, qty, turn count, current offer (JSON), status (OPEN/ACCEPTED/BLOCKED/ESCALATED).
- **audit_log** — append-only (INSERT/SELECT only, enforced in code):
```sql
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY,
    negotiation_id TEXT NOT NULL,
    turn INTEGER NOT NULL,
    actor TEXT NOT NULL,            -- 'buyer_agent' | 'merchant_llm' | 'policy_engine' | 'system'
    action TEXT NOT NULL,           -- 'counter_offer' | 'accept' | 'escalate' | 'guardrail_check'
    payload TEXT NOT NULL,          -- JSON: full proposed terms
    verdict TEXT,                   -- 'PASS' | 'FAIL' | NULL
    reason TEXT                     -- structured string from Python, e.g.
                                    -- "unit_price 9.50 < floor 9.80 for tier 3000+"
);
```
- **orders** — final agreed terms package per negotiation.

### 6.3 Policy engine (the moat — pure Python, ZERO LLM calls)
```python
def check(offer: CounterOffer, session: Negotiation) -> Verdict
```
Checks, in order:
1. **Per-field bounds**: unit_price ≥ floor for the applicable volume tier; payment_terms_days ≤ max; delivery_days ≥ min lead time; qty within stock; turn count < max_turns.
2. **Composite margin check**: effective margin computed from unit price minus working-capital cost of payment terms minus rush-delivery cost. A price legal alone can FAIL as a package (e.g. deep discount + net-45).
3. Returns `Verdict(PASS|FAIL, reason)` — reason strings generated by Python only, never by the model.

Design constraints:
- Readable top-to-bottom by a judge in ~2 minutes.
- Unit-tested BEFORE the LLM layer is wired (build order enforces this).

### 6.4 LLM layer
Any OpenAI-compatible structured-output endpoint behind one thin client wrapper. Two agents in separate modules, **no shared prompt context** (keeps negotiation non-choreographed):

- **Merchant agent** — knows margins, floors, catalog tiers. Tools:
  - `counter_offer(unit_price, min_volume, payment_terms_days, delivery_days, recurring)`
  - `accept_offer()`
  - `escalate_to_human(reason)` — graceful exit when bounds block everything reasonable
- **Buyer agent (adversarial simulator)** — aggressive price-prober with its own budget cap and tactics (repeated lowballs, cart-abandonment threats). Exists to prove the guardrail holds against pressure.

The merchant LLM's job is lever selection across the five variables based on buyer behavior across turns ("they pushed price three times but never mentioned delivery") — genuinely non-table-shaped reasoning. Enforcement stays deterministic.

### 6.5 Invoice generator
- reportlab template filled deterministically from the `orders` row (NEVER from LLM output). Renders all five agreed terms; every value maps 1:1 to an audit-log row.
- Saved to local `invoices/`; storage behind one wrapper function `save_invoice(order) -> url` so S3+pre-signed URLs can replace it later in ~20 lines if deployment demands durability.

### 6.7 Security — three-gate model

**Mental model:** the seller is the server, not a client. Only buyer agents (and judges hitting GETs) are external callers. Two questions are answered by two separate layers: *who may talk to us* (auth) vs *what action may ever be agreed* (policy engine).

**Gate 1 — Transport/identity (401):** static scoped API keys via `X-Buyer-Key` header.
- `buyers` table stores **SHA-256 hashes only** (indexed column), never plaintext; comparison via `hmac.compare_digest()` (constant-time). Optional HMAC pepper from `.env` for low-entropy keys.
- Raw keys live in `.env` only (never committed); provisioning via CLI (`python -m app.create_buyer acme --budget 50000`), not seeded constants.
- Endpoint requirements:

| Endpoint | Requirement |
|---|---|
| `GET /catalog` | none — deliberately public (any agent may discover the merchant) |
| `POST /quote` | valid buyer key |
| `POST /negotiate` | valid key + negotiation ownership (`negotiation.buyer_id == buyer.buyer_id`) |
| `GET /orders`, `/invoices`, `/audit` | valid key + ownership (buyer A cannot read B's contract) |

**Gate 2 — Schema validation (422):** LLM tool output parsed through Pydantic `CounterOffer`. Malformed proposals / hallucinated product_ids rejected cleanly, logged as FAIL with reason `malformed_proposal`; never reach money logic.

**Gate 3 — Policy engine:** structurally valid but out-of-bounds terms (price < tier floor, payment terms too long, composite margin negative, turn limit) → FAIL + graceful fallback counter or escalation. Legitimate failures return an explained response so a well-behaved buyer agent can recover — blocking ≠ crashing.

**Agent identity honesty note:** possession of a scoped API key is the identity model; we do not verify the agent's underlying model or operator. Production would evolve toward signed agent mandates (AP2-style) — noted as future work.

**Credential evolution path (README production section):** static hashed keys → client-credentials exchange at `/token` for short-lived signed tokens carrying identity+scopes → cryptographic mandates. Stage one is proportionate here; stages two and three are documented, not built.

### 6.8 Demo harness
Script running full E2E negotiations end-to-end, printing the audit trail as a readable table:

```
turn 1  buyer_agent   counter_offer  ₹9.50 / net-30 / 10d
turn 1  policy        FAIL           unit_price below floor for tier
turn 2  merchant_llm  counter_offer  ₹9.90 / net-15 / 7d / 4000 units
turn 2  policy        PASS           all bounds ok, margin 18.2%
...
```
Required runs for the video: (a) clean successful deal → invoice download; (b) adversarial run hitting the wall → FAIL logged → graceful fallback/escalation. Closing frame = the audit table.

## 7. Negotiable contract — the five variables

Every deal is a package, not a price:

| Term | Field | Merchant cost when conceded |
|---|---|---|
| Unit price | `unit_price` | Direct margin |
| Volume / MOQ | `min_volume` | Higher volume lowers merchant unit cost → enables price concession |
| Payment terms | `payment_terms_days` | Working-capital cost (~% per day of delay) |
| Delivery timeline | `delivery_days` | Rush overtime cost below standard lead time |
| Order commitment | `recurring` | Recurring revenue justifies better rate |

Guardrail evaluates the whole package; LLM chooses which lever(s) to move.

## 8. Tech stack

| Layer | Choice |
|---|---|
| Language | Python 3.11 |
| API | FastAPI + uvicorn |
| Data | SQLite (SQLModel or raw sqlite3) |
| Validation/schemas | Pydantic (shared across LLM output, requests, guardrail) |
| LLM | OpenAI-compatible structured-output endpoint behind thin wrapper (provider TBD — cheapest viable option; architecture is provider-agnostic) |
| Invoice | reportlab |
| Payments | Razorpay test-mode Orders API (order creation only, gated on guardrail PASS) |
| Testing | pytest — policy engine first-class coverage |

## 9. Build order

1. **Day 1:** Catalog + products/buyers tables + `/catalog` endpoint + key-provisioning CLI.
2. **Day 1–2:** Policy engine + pytest suite (BEFORE any LLM wiring).
3. **Day 2–3:** Pydantic schemas; merchant agent loop with tools; audit logging wired into every step.
4. **Day 3–4:** Buyer adversary module; multi-turn `/negotiate` flow; escalation path.
5. **Day 5:** Invoice generator; demo harness; README (incl. AWS production path); record video.

## 10. Risks & mitigations

| Risk | Mitigation |
|---|---|
| "Both agents are you" objection | Separate modules/prompts/budgets; public `/catalog` endpoint judges can curl live (ngrok at panel stage) |
| LLM as decoration | Five-variable lever selection + behavior reading across turns is explicitly non-table-shaped; pitch states the division of labor |
| Guardrail bypass via malformed/hallucinated tool calls | Schema validation rejects cleanly → logged FAIL → fallback offered; treated as first-class failure case in tests |
| Scope creep | Invoice generation capped at half a day; no UI work permitted until core loop done |
| LLM provider cost/outage | Thin client wrapper; any OpenAI-compatible endpoint swappable |

## 11. AWS production path (README section — honest mapping, not deployed)

| Component (demo) | Production equivalent |
|---|---|
| SQLite tables | DynamoDB |
| Single-process app | Lambda / ECS behind API Gateway |
| Local `invoices/` files | S3 objects + time-limited pre-signed URLs (15-min expiry reinforces "bounded" theme) |
| Static hashed API keys | Razorpay-style key/secret pairs (secret shown once at creation, only hash retained); short-lived signed tokens via client-credentials `/token` flow at scale; agent authorization evolving toward signed mandates (AP2) |
| In-process audit INSERTs | DynamoDB + CloudWatch Logs; tamper-proof archival via S3 Object Lock / QLDB-style ledger |
| Inline negotiation loop | Step Functions state machine (visible execution graph for audits) |

## 12. Deliverables checklist

- [ ] Public repo: code + README (bar language verbatim: bounded / explainable / audit trail / failure handled gracefully; approved-vs-blocked audit rows near top)
- [ ] 5-minute pitch video: catalog query → negotiation turns → guardrail block w/ graceful fallback → accepted deal → invoice download → audit table closing frame
- [ ] Architecture diagram (README section 11 diagram suffices)
- [ ] Live `/catalog` endpoint ready via ngrok for panel Q&A
