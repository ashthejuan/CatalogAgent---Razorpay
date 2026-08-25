# Phase 1 — Catalog & Identity

Implemented: schemas, SQLite catalog/buyers layer, seed script, buyer-key CLI, public `GET /catalog`.

## Files changed

| File | Purpose |
|---|---|
| `backend/app/schemas.py` | Pydantic v2 models: `VolumeTier`, `Product`, `CounterOffer`, `QuoteRequest`, `OrderTerms`, `Verdict` |
| `backend/app/db.py` | SQLite (`catalogagent.db`); `products` + `buyers` tables only |
| `backend/app/seed.py` | 10 demo products across 3 categories |
| `backend/app/create_buyer.py` | CLI: `python -m app.create_buyer <name> --budget <cap>` |
| `backend/app/main.py` | Lifespan `init_db()`; `GET /catalog` (public) |
| `backend/tests/test_phase1.py` | Schema strictness, seed/catalog, buyer hashing, public access |

## Security

- `/catalog` is **public by design** (agent discovery). Response uses `CatalogProduct` / `CatalogTier` — **no `floor_price`** (internal guardrail; policy engine only).
- `app/config.py` loads gitignored `.env` via `python-dotenv` at import time.
- Buyer keys: `bk_<name>_<random8>` printed once; DB stores SHA-256 (or HMAC-SHA256 with `KEY_PEPPER`) only.
- Write endpoints remain unprotected until Phase 4 (`require_buyer`).

## DB path override

Tests and tooling may set `CATALOGAGENT_DB_PATH` to avoid clobbering dev data.

## Deferred

- `negotiations`, `audit_log`, `orders` tables (Phase 3+)
- Policy engine, LLM, Razorpay (Phases 2, 4, 6)
