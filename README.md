# CatalogAgent

## Overview

**CatalogAgent** is a B2B agentic-procurement negotiation system built for the
Razorpay AI Buildathon (Track 01 — AI Growth & Agentic Commerce).

An AI **buyer agent** (acting for a procurement team) queries a merchant's
agent-readable product catalog, then negotiates a multi-variable supply
contract — unit price, volume/MOQ, payment terms, delivery lead time, and
recurring commitment — with an AI **merchant agent**.

The differentiator is **bounded autonomy**: every merchant agent move is checked
by a deterministic policy engine *before* it can reach Razorpay. The merchant LLM
reasons over the five negotiable variables (it can hold price and win on
commitment, terms, or volume instead); it never gets to move money on its own.
Every proposal and every guardrail verdict is written to an **append-only audit
trail**. This is the moat — not the LLM, which is the smallest and least
defensible piece of the system.

**The bar (verbatim from the track):** *Every money action explainable, bounded
and gated. Show the audit trail and one failure handled gracefully.*

## Architecture

FastAPI + SQLite backend; optional Next.js demo UI. No LangChain, no Docker, no
RAG. Full design in
[`context/architecture/architecture.md`](context/architecture/architecture.md);
security model in
[`context/security/security.md`](context/security/security.md);
phase notes under [`context/architecture/`](context/architecture/).

```
buyer agent ──POST /negotiate──▶ [Gate 1: buyer key] ──▶ merchant LLM (tool-calling)
                                                       │  proposes counter_offer()
                                                       ▼
                                              [Gate 2: parse → CounterOffer]
                                                       ▼
                                              [Gate 3: policy.check()]  ──FAIL──▶ best_legal_counter / escalate
                                                       │ PASS                                  (both audited)
                                                       ▼
                                              accept → Razorpay test-mode order + invoice PDF
```

Three gates: (1) identity — hashed buyer API key, constant-time compare;
(2) schema — Pydantic validates the LLM's structured tool call, malformed ⇒
audited rejection; (3) action permission — the deterministic policy engine
enforces merchant bounds. The money action is reachable **only** after Gate 3
passes and the merchant accepts; no agent module imports `payments`.

### HTTP surface

| Method | Path | Auth |
|--------|------|------|
| `GET` | `/health` | public |
| `GET` | `/catalog` | public (floors stripped) |
| `POST` | `/quote` | `X-Buyer-Key` |
| `POST` | `/negotiate` | key + negotiation ownership |
| `GET` | `/audit/{negotiation_id}` | key + ownership |
| `GET` | `/invoices/{order_id}` | key + ownership (PDF) |
| `POST` | `/ui/session` | public demo helper (provisions a throwaway buyer) |

## Security

Detailed model in [`context/security/security.md`](context/security/security.md). Summary:

- **LLM proposes, code disposes.** The guardrail is a control-flow property, not
  a convention the LLM is "supposed" to respect.
- Buyer keys stored as **SHA-256 (+ optional HMAC pepper)**, never plaintext.
- Public `/catalog` exposes list prices and tiers but **strips `floor_price`**
  so buyers must probe for it.
- **Verdict reasons are machine-generated** (structured Python strings), never
  LLM text — the audit trail is reproducible.
- Honestly out of scope (documented, not faked): agent-identity verification
  (AP2), dynamic token issuance, rate limiting, cryptographic audit
  immutability.

## Current status

| Phase | Status |
|-------|--------|
| 0 — Setup | ✅ scaffold, external creds via `.env` |
| 1 — Catalog & identity | ✅ schemas, seed, key-CLI, public `/catalog` (floors stripped) |
| 2 — Policy engine | ✅ 5-variable bounds + composite margin |
| 3 — Audit trail | ✅ append-only `audit_log` + owned `GET /audit/{id}` |
| 4 — Merchant agent | ✅ LLM tool-calling + Gate 2/3 + `/quote` `/negotiate` |
| 5 — Buyer adversary | ✅ isolated buyer agent + multi-scenario `demo/run_demo.py` |
| 6 — Money + invoice | ✅ Razorpay test-mode order + reportlab PDF on accept |
| 7 — Polish & ship | ⬜ demo video / final submit sweep |

## Quickstart

Requires Python 3.11+. Secrets live in `backend/.env` (never committed; copy from
`.env.example`). Run from `backend/` unless noted.

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

Open http://localhost:3000. The Next dev server proxies `/api/*` → `:8000`
(see `frontend/next.config.mjs`). Details in
[`frontend/README.md`](frontend/README.md).

A minimal single-file UI is also served at `GET /` from `backend/static/index.html`.

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
`backend/catalogagent.db` in DB Browser for SQLite → `audit_log`.

## Testing

```bash
cd backend
.venv\Scripts\python.exe -m pytest tests/ -q
```

Phases 1–6 are covered (policy, audit, merchant, buyer, negotiate routes,
Razorpay/invoice gating). LLM/Razorpay calls in unit tests are mocked.

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

## AWS Production Path

Demo runs fully local (FastAPI + SQLite). The PRD's production path maps each
concern to free-tier AWS: catalog/API → API Gateway, policy orchestration →
Lambda, audit/catalog store → DynamoDB, documents → S3 pre-signed URLs, logs →
CloudWatch. LLM stays outside AWS; Razorpay test-mode is the only external
money action. Full table in
[`context/architecture/CatalogAgent-PRD.md`](context/architecture/CatalogAgent-PRD.md).

## Demo Video

Placeholder — demo video link arrives in Phase 7.
