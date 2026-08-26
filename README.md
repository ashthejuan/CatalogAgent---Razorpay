# CatalogAgent

## Overview

CatalogAgent is a B2B agentic-procurement negotiation system built for the
Razorpay AI Buildathon (Track 01 — AI Growth & Agentic Commerce).

An AI buyer agent (acting for a procurement team) queries a merchant's
agent-readable product catalog, then negotiates a multi-variable supply
contract — unit price, volume/MOQ, payment terms, delivery lead time, and
recurring commitment — with an AI merchant agent.

The differentiator is bounded autonomy: every merchant agent move is checked
by a deterministic policy engine before it can reach Razorpay. The merchant
LLM reasons over the five negotiable variables — it can hold price and win on
commitment, terms, or volume instead — but it never gets to move money on its
own. Every proposal and every guardrail verdict is written to an append-only
audit trail. That's the moat, not the LLM, which is the smallest and least
defensible part of the system.

**The bar (verbatim from the track):** *Every money action explainable, bounded
and gated. Show the audit trail and one failure handled gracefully.*

## Architecture

```mermaid
flowchart LR
    Buyer["Buyer / Procurement Team<br/>AI Buyer Agent"]
    UI["Next.js Demo UI<br/>localhost:3000"]
    API["FastAPI Backend<br/>:8000"]

    Buyer -->|"HTTP / negotiate"| UI
    UI -->|"/api/*"| API

    G1{"Gate 1<br/>Buyer Key<br/>SHA-256 / HMAC<br/>Constant-time compare"}

    API --> G1

    G1 -->|Valid| Merchant
    G1 -->|Invalid| RejectAuth["Reject Request"]

    Merchant["Merchant Agent<br/>LLM + Tool Calling"]

    Merchant -->|"counter_offer()"| G2

    G2{"Gate 2<br/>Schema Validation<br/>Pydantic → CounterOffer"}

    G2 -->|Valid| Policy
    G2 -->|Malformed| SchemaReject["Reject + Audit"]

    Policy["Gate 3 — Deterministic Policy Engine<br/><br/>Hard Bounds<br/>• MOQ<br/>• Price Floor<br/>• Payment Terms ≤ 45 days<br/>• Delivery Floor<br/>• Stock Cap<br/><br/>Composite Margin"]

    Policy -->|PASS| Accept{"Merchant<br/>Accepts?"}
    Policy -->|FAIL| Legal["Best Legal Counter<br/>or Escalate"]

    Legal --> Audit
    SchemaReject --> Audit
    RejectAuth --> Audit

    Accept -->|Yes| Razorpay["Razorpay Test Mode<br/>Order + Invoice PDF"]
    Accept -->|No| Legal

    Catalog["Product Catalog<br/>Products + Volume Tiers<br/><br/>Public API strips floor_price"]
    DB["SQLite<br/>catalog + audit_log"]

    Catalog --> API
    API --> DB
    Policy --> DB

    Audit["Append-only Audit Trail<br/><br/>Every Proposal<br/>Every Gate Verdict<br/>Structured Reasons"]

    Merchant --> Audit
    G2 --> Audit
    Policy --> Audit
    Razorpay --> Audit

    Audit --> DB

    Endpoints["HTTP API Surface<br/><br/>GET /health<br/>GET /catalog<br/>POST /quote<br/>POST /negotiate<br/>GET /audit/{negotiation_id}<br/>GET /invoices/{order_id}<br/>POST /ui/session"]

    API --- Endpoints

    Security["Security Boundary<br/><br/>LLM proposes<br/>Code disposes<br/><br/>Agents cannot import payments<br/>Money action only after Gate 3"]

    Merchant --- Security
    Razorpay --- Security
```



Three gates: identity (hashed buyer API key, constant-time compare), schema
(Pydantic validates the LLM's structured tool call — malformed calls are
audited and rejected), and action permission (the deterministic policy engine
enforces merchant bounds). The money action is reachable only after Gate 3
passes and the merchant accepts. No agent module imports `payments`.



### HTTP surface


| Method | Path                      | Auth                                              |
| ------ | ------------------------- | ------------------------------------------------- |
| `GET`  | `/health`                 | public                                            |
| `GET`  | `/catalog`                | public (floors stripped)                          |
| `POST` | `/quote`                  | `X-Buyer-Key`                                     |
| `POST` | `/negotiate`              | key + negotiation ownership                       |
| `GET`  | `/audit/{negotiation_id}` | key + ownership                                   |
| `GET`  | `/invoices/{order_id}`    | key + ownership (PDF)                             |
| `POST` | `/ui/session`             | public demo helper (provisions a throwaway buyer) |




## Policy Engine

The deterministic policy engine is Gate 3 and the core moat. It makes **zero**
**LLM calls** — `app/policy.py` takes a structured `CounterOffer`, applies hard
bounds, then computes a composite margin and passes/fails the offer. Every
verdict reason is a structured Python string (never LLM text) so the audit
trail stays reproducible. Source of truth for the formulas below:
[backend/app/policy.py](backend/app/policy.py).

```mermaid
flowchart TD
    A["CounterOffer<br/><br/>unit_price<br/>min_volume<br/>payment_terms_days<br/>delivery_days<br/>recurring"]

    A --> B["Layer A — Hard Gates"]

    B --> G1{"min_volume ≥ tier.min_qty?"}
    G1 -->|NO| R["REJECT"]
    G1 -->|YES| G2{"unit_price ≥ tier.floor_price?"}

    G2 -->|NO| R
    G2 -->|YES| G3{"payment_terms_days ≤ 45?"}

    G3 -->|NO| R
    G3 -->|YES| G4{"delivery_days ≥ lead_time_min_days?"}

    G4 -->|NO| R
    G4 -->|YES| G5{"min_volume ≤ stock?"}

    G5 -->|NO| R
    G5 -->|YES| M["Layer B — Composite Margin"]

    M --> RD["Calculate rush_days<br/><br/>rush_days = max(0,<br/>lead_time_max_days − delivery_days)"]

    RD --> MC["Calculate Margin<br/><br/>margin = unit_price<br/>− tier.floor_price<br/>− payment_terms_days × 0.0005 × unit_price<br/>− rush_days × 0.01 × unit_price<br/>+ recurring × 0.02 × unit_price"]

    MC --> C{"margin ≥ 0?"}

    C -->|NO| R2["REJECT<br/><br/>Policy violation"]
    C -->|YES| P["PASS<br/><br/>Offer is legally<br/>within merchant bounds"]
```





### Constants


| Constant                 | Symbol     | Value                | Meaning                                                             |
| ------------------------ | ---------- | -------------------- | ------------------------------------------------------------------- |
| `TERMS_COST_PER_DAY`     | terms cost | `0.0005` / day       | working-capital cost of granting credit (0.05%/day)                 |
| `RUSH_COST_PER_DAY`      | rush cost  | `0.01` / day         | margin given up per day delivery beats the standard max lead time   |
| `RECURRING_CONCESSION`   | recurring  | `+0.02` × unit_price | concession granted for a recurring commitment (predictable revenue) |
| `MAX_PAYMENT_TERMS_DAYS` | max terms  | `45` days            | hard cap on payment terms                                           |




### Layer A — Hard gates (fail any one → reject)

1. **Minimum order quantity:** `min_volume >= tier.min_qty` (else an adversarial buyer can't claim a tiny volume to reach the most lenient tier).
2. **Price floor:** `unit_price >= tier.floor_price`.
3. **Payment terms:** `payment_terms_days <= 45`.
4. **Delivery floor:** `delivery_days >= lead_time_min_days`.
5. **Volume cap:** `min_volume <= stock`.



### Layer B — Composite margin (reject if `< 0`)

```
margin = unit_price
       - tier.floor_price
       - payment_terms_days * 0.0005 * unit_price
       - rush_days * 0.01 * unit_price
       + recurring * 0.02 * unit_price

where rush_days = max(0, lead_time_max_days - delivery_days)
      (only when delivery_days < lead_time_max_days)
```

A price at or above the floor can still fail when terms + rush + recurring push
effective margin negative. Recurring is the one concession that *helps* a tight
package pass.

### Worked example — `elec-conn-001`

`elec-conn-001` tiers (seed data): 1000+ floor `11.20`, `lead_time_min_days=7`,
`lead_time_max_days=21`, `stock=20000`.

**Case 1 — floor-price offer at net-30, standard delivery (FAILS):**

- `unit_price = 11.20` (= floor), `min_volume=1000`, `payment_terms_days=30`, `delivery_days=21` (= max, so `rush_days=0`), `recurring=False`.
- `margin = 11.20 - 11.20 - (30 * 0.0005 * 11.20) - 0 + 0`
- `= 0 - 0.168 = -0.168` → **negative → REJECT.** The 30-day terms cost alone eats the entire margin even at the floor.

**Case 2 — raise price above floor (PASSES):**

- `unit_price = 11.40` (next tier list price), same terms/delivery, `rush_days=0`, `recurring=False`.
- `margin = 11.40 - 11.20 - (30 * 0.0005 * 11.40) = 0.20 - 0.171 = +0.029` → **PASSES** (≈0.25% margin).

**Case 3 — net-0 instead of net-30 (PASSES):**

- `unit_price = 11.20`, `payment_terms_days=0` → `terms_cost = 0`.
- `margin = 11.20 - 11.20 - 0 = 0` → not `< 0` → **PASSES** (zero margin, but legal).

Net: at the floor price, the buyer must either pay immediately (net-0) or accept
a slightly higher unit price to absorb the working-capital cost of net-30.

## Security

- LLM proposes, code disposes. The guardrail is a control-flow property, not
a convention the LLM is "supposed" to respect.
- Buyer keys are stored as SHA-256 (+ optional HMAC pepper), never plaintext.
- Public `/catalog` exposes list prices and tiers but strips `floor_price`, so
buyers have to probe for it.
- Verdict reasons are machine-generated (structured Python strings), never
LLM text — the audit trail stays reproducible.
- Honestly out of scope (documented, not faked): agent-identity verification
(AP2), dynamic token issuance, rate limiting, cryptographic audit
immutability.



## Quickstart

Requires Python 3.11+. Secrets live in `backend/.env` (never committed; copy
from `.env.example`). Run from `backend/` unless noted.

```bash
# 1. install + env
cd backend
python -m venv .venv
# Windows:
.venv\Scripts\pip install -e .
copy .env.example .env   # then fill RAZORPAY_*, LLM_*, optional KEY_PEPPER

# 2. start the API (auto-seeds catalog on startup)
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000

# 3. public catalog
curl -s http://127.0.0.1:8000/catalog
```

Expected: JSON with ~10 products; each volume tier exposes only `min_qty` and
`unit_price` (`floor_price` is stripped).

Optional buyer key for write paths:

```bash
.venv\Scripts\python.exe -m app.create_buyer acme --budget 50000
# prints bk_... once — use as X-Buyer-Key
```



### Demo UI (Next.js)

```bash
# terminal A — backend on :8000 (as above)
# terminal B
cd frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). The Next dev server
proxies `/api/*` to `:8000` (see `frontend/next.config.mjs`). Details in
[frontend/README.md](frontend/README.md).

A minimal single-file UI is also served at `GET /` from
`backend/static/index.html`.

### Multi-turn demo harness

With the API configured (LLM + optional Razorpay):

```bash
cd backend
.venv\Scripts\python.exe -m demo.run_demo
```

Runs reasonable / aggressive / creative buyer scenarios over HTTP and prints
audit tables.

## Audit trail

```bash
curl -s http://127.0.0.1:8000/audit/<negotiation_id> \
  -H "X-Buyer-Key: bk_..."
```

Returns ordered `trail` JSON plus a readable table under `text`. Or open
`backend/catalogagent.db` in DB Browser for SQLite and look at `audit_log`.

## Testing

```bash
cd backend
.venv\Scripts\python.exe -m pytest tests/ -q
```



## Layout

```
backend/app/          FastAPI routes, policy, db, payments, invoicing, llm_client
backend/app/agents/   merchant.py + buyer.py (isolated; no payments imports)
backend/demo/         run_demo + smoke/repro scripts
backend/tests/        pytest suite
frontend/             Next.js negotiation UI
context/architecture/ design + phase notes
context/security/     security model
```

