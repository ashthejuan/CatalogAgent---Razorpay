# Security model (Phase 1):
#   GET /catalog is PUBLIC by design — any AI agent may discover the merchant catalog.
#   Public response strips internal guardrails (floor_price); full Product stays server-side
#   for the policy engine. Write endpoints are buyer-key protected in Phase 4.

import app.config  # noqa: F401 — load .env at startup

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import get_public_catalog, init_db, format_audit_trail, get_audit_trail
from app.schemas import CatalogProduct


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="CatalogAgent", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "service": "catalogagent"}


@app.get("/catalog", response_model=list[CatalogProduct])
def catalog():
    return get_public_catalog()


@app.get("/audit/{negotiation_id}")
def audit(negotiation_id: str):
    """Read-only audit trail for a negotiation (append-only table behind this)."""
    return {
        "negotiation_id": negotiation_id,
        "trail": get_audit_trail(negotiation_id),
        "text": format_audit_trail(negotiation_id),
    }
