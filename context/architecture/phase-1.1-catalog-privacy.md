# Phase 1.1 — Public catalog privacy + .env auto-load

## Changes

| File | Change |
|---|---|
| `backend/app/schemas.py` | `CatalogTier`, `CatalogProduct` (no `floor_price`); `CatalogProduct.from_product()` |
| `backend/app/db.py` | `get_public_catalog()` strips floors; imports `app.config` |
| `backend/app/config.py` | **New** — `load_dotenv()` for gitignored `backend/.env` |
| `backend/app/main.py` | `/catalog` returns `list[CatalogProduct]` |
| `backend/tests/test_phase1.py` | `test_catalog_hides_floor_price` |

## Rationale

Public `/catalog` exposes negotiable list prices, stock, and lead times. `floor_price` is a merchant guardrail bound used only by the policy engine (Phase 2) — exposing it would let buyer agents anchor directly on the floor.
