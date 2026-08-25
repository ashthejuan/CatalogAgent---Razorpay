# Security model (Phase 1):
#   GET /catalog is PUBLIC by design — any AI agent may discover the merchant catalog.
#   Write endpoints (POST /quote, POST /negotiate, etc.) are buyer-key protected in Phase 4.

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import get_catalog, init_db


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="CatalogAgent", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "service": "catalogagent"}


@app.get("/catalog")
def catalog():
    return get_catalog()
