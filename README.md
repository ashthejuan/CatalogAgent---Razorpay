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

Single FastAPI process + SQLite (demo). No React, no LangChain, no Docker, no
RAG. Full design in [`context/architecture.md`](context/architecture.md);
policy hardening + audit notes in
[`context/architecture/phase-3-audit.md`](context/architecture/phase-3-audit.md).

```
buyer agent ──POST /negotiate──▶ [Gate 1: buyer key] ──▶ merchant LLM (tool-calling)
                                                       │  proposes counter_offer()
                                                       ▼
                                              [Gate 2: parse → CounterOffer]
                                                       ▼
                                              [Gate 3: policy.check()]  ──FAIL──▶ best_legal_counter / escalate
                                                       │ PASS                                  (both audited)
                                                       ▼
                                              Razorpay test-mode order (Phase 6)
```

Three gates: (1) identity — hashed buyer API key, constant-time compare;
(2) schema — Pydantic validates the LLM's structured tool call, malformed ⇒
audited rejection; (3) action permission — the deterministic policy engine
enforces merchant bounds. The money action is reachable **only** after Gate 3
passes; no code path can reach `payments.create_order` otherwise.

## Security

Detailed model in [`context/security.md`](context/security.md). Summary:

- **LLM proposes, code disposes.** The guardrail is a control-flow property, not
  a convention the LLM is "supposed" to respect.
- Buyer keys stored as **SHA-256 (+ optional HMAC pepper)**, never plaintext.
- Public `/catalog` exposes list prices and tiers but **strips `floor_price`**
  (the internal negotiation floor) so buyers must probe for it.
- **Verdict reasons are machine-generated** (structured Python strings), never
  LLM text — the audit trail is tamper-evident and reproducible.
- Honestly out of scope (documented, not faked): agent-identity verification
  (AP2), dynamic token issuance, rate limiting, cryptographic audit
  immutability.

## Current status

| Phase | Status |
|-------|--------|
| 0 — Setup | ✅ scaffold, external creds verified |
| 1 — Catalog & identity | ✅ schemas, seed, key-CLI, public `/catalog` (floors stripped) |
| 2 — Policy engine | ✅ 5-variable bounds + composite margin; hardened (sub-MOQ, rush, recurring sign) |
| 3 — Audit trail | ✅ append-only `audit_log` + `GET /audit/{id}` |
| 4 — Merchant agent | 🚧 in progress |
| 5 — Buyer adversary | ⬜ |
| 6 — Money + invoice | ⬜ |
| 7 — Polish & ship | ⬜ |

## Quickstart

Requires Python 3.11 and the project venv (`.venv/`). Secrets live in `.env`
(never committed). Run from `backend/`.

```bash
# 1. load secrets, then start the server
cd backend
set -a; source .env; set +a
./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000

# 2. in a second terminal — public, agent-readable catalog
curl -s http://127.0.0.1:8000/catalog | python -m json.tool
```

Expected: JSON with 10 products; each volume tier exposes only `min_qty` and
`unit_price` (the internal `floor_price` is deliberately stripped — see
Security).

Notes:
- Run these in **Git Bash** (MINGW64), not PowerShell — PowerShell's `curl` is an
  `Invoke-WebRequest` alias and behaves differently.
- If port 8000 is "already in use", pick another (e.g. 8001) and keep the server
  and curl ports consistent.
- `python -m json.tool` needs `python` on PATH; otherwise use
  `.venv/Scripts/python.exe -m json.tool`.

## Audit trail inspection

```bash
# after a negotiation has written rows (Phase 4+):
curl -s http://127.0.0.1:8000/audit/<negotiation_id>
```

Returns the ordered trail as JSON plus a readable table under `text`. View the
SQLite DB directly with **DB Browser for SQLite** (Electron app): open
`backend/catalogagent.db` → Browse Data → `audit_log`.

## Testing

```bash
cd backend
./.venv/Scripts/python.exe -m pytest tests/ -q
```

## AWS Production Path

Demo runs fully local (FastAPI + SQLite). The PRD's production path maps each
concern to free-tier AWS: catalog/API → API Gateway, policy orchestration →
Lambda, audit/catalog store → DynamoDB, documents → S3 pre-signed URLs, logs →
CloudWatch. LLM stays outside AWS (no Bedrock free tier); Razorpay test-mode is
the only external money action. Full table in `CatalogAgent-PRD.md`.

## Demo Video

Placeholder — demo video link arrives in Phase 7.
