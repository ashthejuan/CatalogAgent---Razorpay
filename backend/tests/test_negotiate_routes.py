"""HTTP route tests for /quote and /negotiate (Gate 1 + ownership)."""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from app.agents.merchant import MerchantTurnResult
from app.schemas import CounterOffer


def _reload_stack(tmp_path, monkeypatch):
    monkeypatch.setenv("CATALOGAGENT_DB_PATH", str(tmp_path / "test.db"))
    import app.agents.merchant as merchant_module
    import app.create_buyer as create_buyer_module
    import app.db as db_module
    import app.main as main_module
    import app.seed as seed_module

    importlib.reload(db_module)
    importlib.reload(create_buyer_module)
    importlib.reload(seed_module)
    importlib.reload(merchant_module)
    importlib.reload(main_module)
    db_module.init_db()
    seed_module.seed_products()
    return db_module, create_buyer_module, main_module


@pytest.fixture
def stack(tmp_path, monkeypatch):
    ctx = _reload_stack(tmp_path, monkeypatch)
    yield ctx
    _reload_stack(tmp_path, monkeypatch)


@pytest.fixture
def client(stack):
    _, _, main_module = stack
    with TestClient(main_module.app) as test_client:
        yield test_client


@pytest.fixture
def acme_key(stack):
    _, create_buyer_module, _ = stack
    return create_buyer_module.create_buyer("acme", 50000.0)


@pytest.fixture
def globex_key(stack):
    _, create_buyer_module, _ = stack
    return create_buyer_module.create_buyer("globex", 40000.0)


def _headers(key: str) -> dict[str, str]:
    return {"X-Buyer-Key": key}


def _quote(client, key, **overrides):
    body = {"product_id": "elec-conn-001", "buyer_id": "acme", "initial_volume": 5000}
    body.update(overrides)
    return client.post("/quote", json=body, headers=_headers(key))


def _negotiate(client, key, negotiation_id, **offer_kw):
    offer = {
        "unit_price": 9.0,
        "min_volume": 5000,
        "payment_terms_days": 30,
        "delivery_days": 14,
        "recurring": False,
    }
    offer.update(offer_kw)
    return client.post(
        "/negotiate",
        json={"negotiation_id": negotiation_id, "buyer_offer": offer},
        headers=_headers(key),
    )


def test_bad_key_returns_401(client):
    r = client.post(
        "/quote",
        json={"product_id": "elec-conn-001", "buyer_id": "acme"},
        headers={"X-Buyer-Key": "bk_invalid_key"},
    )
    assert r.status_code == 401


def test_foreign_negotiation_returns_403(client, acme_key, globex_key):
    quote = _quote(client, acme_key)
    assert quote.status_code == 200
    negotiation_id = quote.json()["negotiation_id"]

    r = _negotiate(client, globex_key, negotiation_id)
    assert r.status_code == 403


def test_malformed_counter_offer_returns_422(client, acme_key, monkeypatch, stack):
    _, _, main_module = stack
    quote = _quote(client, acme_key)
    negotiation_id = quote.json()["negotiation_id"]

    r = client.post(
        "/negotiate",
        json={
            "negotiation_id": negotiation_id,
            "buyer_offer": {
                "unit_price": 9.0,
                "min_volume": 5000,
                "payment_terms_days": 30,
                "delivery_days": 14,
                "recurring": False,
                "extra": "nope",
            },
        },
        headers=_headers(acme_key),
    )
    assert r.status_code == 422


def test_adversarial_buyer_offer_is_routed_without_gate3_block(
    client, acme_key, monkeypatch, stack
):
    _, _, main_module = stack
    product = stack[0].get_product("elec-conn-001")
    tier = max(product.volume_tiers, key=lambda t: t.min_qty)

    def fake_run_turn(negotiation, buyer_offer, llm=None):
        return MerchantTurnResult(
            action="counter_offer",
            offer=CounterOffer(
                unit_price=tier.floor_price + 1.0,
                min_volume=tier.min_qty,
                payment_terms_days=0,
                delivery_days=product.lead_time_max_days,
                recurring=False,
            ),
        )

    monkeypatch.setattr(main_module, "run_turn", fake_run_turn)

    quote = _quote(client, acme_key)
    negotiation_id = quote.json()["negotiation_id"]

    r = _negotiate(
        client,
        acme_key,
        negotiation_id,
        unit_price=1.0,
        min_volume=1,
        payment_terms_days=45,
        delivery_days=1,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "OPEN"
    assert body["merchant_move"]["action"] == "counter_offer"
    assert body["merchant_move"]["offer"] is not None


def test_quote_creates_negotiation_and_audit(client, acme_key, stack):
    db, _, _ = stack
    r = _quote(client, acme_key)
    assert r.status_code == 200
    negotiation_id = r.json()["negotiation_id"]
    assert negotiation_id.startswith("neg_")

    neg = db.get_negotiation(negotiation_id)
    assert neg is not None
    assert neg.status == "OPEN"
    assert neg.turn_count == 0

    trail = db.get_audit_trail(negotiation_id)
    assert trail[0]["actor"] == "system"
    assert trail[0]["action"] == "negotiation_opened"


def test_negotiate_increments_turn_and_audits_buyer(client, acme_key, monkeypatch, stack):
    _, _, main_module = stack
    product = stack[0].get_product("elec-conn-001")
    tier = max(product.volume_tiers, key=lambda t: t.min_qty)

    monkeypatch.setattr(
        main_module,
        "run_turn",
        lambda negotiation, buyer_offer, llm=None: MerchantTurnResult(
            action="counter_offer",
            offer=CounterOffer(
                unit_price=tier.floor_price + 0.5,
                min_volume=tier.min_qty,
                payment_terms_days=0,
                delivery_days=product.lead_time_max_days,
                recurring=False,
            ),
        ),
    )

    negotiation_id = _quote(client, acme_key).json()["negotiation_id"]
    r = _negotiate(client, acme_key, negotiation_id)
    assert r.status_code == 200
    assert r.json()["status"] == "OPEN"
    assert "audit_excerpt" in r.json()

    neg = stack[0].get_negotiation(negotiation_id)
    assert neg.turn_count == 1
    trail = stack[0].get_audit_trail(negotiation_id)
    assert any(e["actor"] == "buyer_agent" for e in trail)


def test_merchant_accept_closes_won(client, acme_key, monkeypatch, stack):
    _, _, main_module = stack
    product = stack[0].get_product("elec-conn-001")
    tier = max(product.volume_tiers, key=lambda t: t.min_qty)
    final = CounterOffer(
        unit_price=tier.floor_price + 0.5,
        min_volume=tier.min_qty,
        payment_terms_days=0,
        delivery_days=product.lead_time_max_days,
        recurring=False,
    )

    monkeypatch.setattr(
        main_module,
        "run_turn",
        lambda negotiation, buyer_offer, llm=None: MerchantTurnResult(
            action="accept",
            offer=final,
        ),
    )

    negotiation_id = _quote(client, acme_key).json()["negotiation_id"]
    r = _negotiate(client, acme_key, negotiation_id)
    body = r.json()
    assert body["status"] == "CLOSED_WON"
    assert body["final_terms"] == final.model_dump()
    assert body["merchant_move"]["action"] == "accept"


def test_merchant_escalate_marks_escalated(client, acme_key, monkeypatch, stack):
    _, _, main_module = stack

    monkeypatch.setattr(
        main_module,
        "run_turn",
        lambda negotiation, buyer_offer, llm=None: MerchantTurnResult(
            action="escalate",
            reason="structural fail",
        ),
    )

    negotiation_id = _quote(client, acme_key).json()["negotiation_id"]
    r = _negotiate(client, acme_key, negotiation_id)
    body = r.json()
    assert body["status"] == "ESCALATED"
    assert body["merchant_move"]["action"] == "escalate"


def test_audit_requires_ownership(client, acme_key, globex_key):
    negotiation_id = _quote(client, acme_key).json()["negotiation_id"]
    r = client.get(f"/audit/{negotiation_id}", headers=_headers(globex_key))
    assert r.status_code == 403

    ok = client.get(f"/audit/{negotiation_id}", headers=_headers(acme_key))
    assert ok.status_code == 200
