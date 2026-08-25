"""Phase 4 end-to-end: quote/negotiate, merchant gates, structural no-payments."""

from __future__ import annotations

import ast
import importlib
import pathlib

import pytest
from fastapi.testclient import TestClient

from app.agents.merchant import MerchantTurnResult, run_merchant_turn
from app.policy import PolicySession, check
from app.schemas import CounterOffer


def _reload_stack(tmp_path, monkeypatch):
    monkeypatch.setenv("CATALOGAGENT_DB_PATH", str(tmp_path / "phase4.db"))
    import app.agents.merchant as merchant_module
    import app.create_buyer as create_buyer_module
    import app.db as db_module
    import app.main as main_module
    import app.policy as policy_module
    import app.seed as seed_module

    importlib.reload(db_module)
    importlib.reload(create_buyer_module)
    importlib.reload(seed_module)
    importlib.reload(policy_module)
    importlib.reload(merchant_module)
    importlib.reload(main_module)
    db_module.init_db()
    seed_module.seed_products()
    return db_module, create_buyer_module, main_module, merchant_module


@pytest.fixture
def stack(tmp_path, monkeypatch):
    ctx = _reload_stack(tmp_path, monkeypatch)
    yield ctx
    _reload_stack(tmp_path, monkeypatch)


@pytest.fixture
def client(stack):
    _, _, main_module, _ = stack
    with TestClient(main_module.app) as test_client:
        yield test_client


@pytest.fixture
def acme(stack):
    _, create_buyer_module, _, _ = stack
    return create_buyer_module.create_buyer("acme", 50000.0)


@pytest.fixture
def globex(stack):
    _, create_buyer_module, _, _ = stack
    return create_buyer_module.create_buyer("globex", 40000.0)


def _headers(key: str) -> dict[str, str]:
    return {"X-Buyer-Key": key}


def _passing_offer(product) -> CounterOffer:
    tier = max(product.volume_tiers, key=lambda t: t.min_qty)
    return CounterOffer(
        unit_price=tier.floor_price + 0.5,
        min_volume=tier.min_qty,
        payment_terms_days=0,
        delivery_days=product.lead_time_max_days,
        recurring=False,
    )


class FakeLLM:
    def __init__(self, responses: list[dict | None] | None = None, *, response=None):
        if responses is not None:
            self.responses = list(responses)
        elif response is not None:
            self.responses = [response]
        else:
            self.responses = []
        self.calls = 0

    def __call__(self):
        """Allow ``LLMClient = FakeLLM`` then ``LLMClient()``."""
        return self

    def chat_with_tools(self, system_prompt, messages, tools):
        self.calls += 1
        if not self.responses:
            return None
        return self.responses.pop(0)


def test_quote_creates_negotiation(client, acme, stack):
    db, _, _, _ = stack
    r = client.post(
        "/quote",
        json={"product_id": "elec-conn-001", "buyer_id": "acme", "initial_volume": 1000},
        headers=_headers(acme),
    )
    assert r.status_code == 200
    negotiation_id = r.json()["negotiation_id"]
    assert negotiation_id.startswith("neg_")

    trail = db.get_audit_trail(negotiation_id)
    assert trail[0]["actor"] == "system"
    assert trail[0]["action"] == "negotiation_opened"


def test_negotiate_bad_key_401(client, acme):
    negotiation_id = client.post(
        "/quote",
        json={"product_id": "elec-conn-001", "buyer_id": "acme"},
        headers=_headers(acme),
    ).json()["negotiation_id"]

    r = client.post(
        "/negotiate",
        json={
            "negotiation_id": negotiation_id,
            "buyer_offer": {
                "unit_price": 9.0,
                "min_volume": 1000,
                "payment_terms_days": 30,
                "delivery_days": 14,
                "recurring": False,
            },
        },
        headers={"X-Buyer-Key": "bk_not_real"},
    )
    assert r.status_code == 401


def test_foreign_negotiation_403(client, acme, globex):
    negotiation_id = client.post(
        "/quote",
        json={"product_id": "elec-conn-001", "buyer_id": "acme"},
        headers=_headers(acme),
    ).json()["negotiation_id"]

    r = client.post(
        "/negotiate",
        json={
            "negotiation_id": negotiation_id,
            "buyer_offer": {
                "unit_price": 9.0,
                "min_volume": 1000,
                "payment_terms_days": 30,
                "delivery_days": 14,
                "recurring": False,
            },
        },
        headers=_headers(globex),
    )
    assert r.status_code == 403


def test_malformed_body_422(client, acme):
    negotiation_id = client.post(
        "/quote",
        json={"product_id": "elec-conn-001", "buyer_id": "acme"},
        headers=_headers(acme),
    ).json()["negotiation_id"]

    r = client.post(
        "/negotiate",
        json={
            "negotiation_id": negotiation_id,
            "buyer_offer": {
                "unit_price": 9.0,
                "min_volume": 1000,
                "payment_terms_days": 30,
                "delivery_days": 14,
                "recurring": False,
                "bogus": True,
            },
        },
        headers=_headers(acme),
    )
    assert r.status_code == 422


def test_merchant_lowball_gets_legal_counter(client, acme, stack, monkeypatch):
    db, _, main_module, merchant_module = stack
    product = db.get_product("elec-conn-001")
    floor = product.volume_tiers[0].floor_price
    illegal = CounterOffer(
        unit_price=round(floor * 0.75, 2),
        min_volume=1000,
        payment_terms_days=0,
        delivery_days=product.lead_time_max_days,
        recurring=False,
    )
    fake = FakeLLM([{"name": "counter_offer", "arguments": illegal.model_dump()}])
    monkeypatch.setattr(merchant_module, "LLMClient", fake)

    negotiation_id = client.post(
        "/quote",
        json={"product_id": "elec-conn-001", "buyer_id": "acme"},
        headers=_headers(acme),
    ).json()["negotiation_id"]

    r = client.post(
        "/negotiate",
        json={
            "negotiation_id": negotiation_id,
            "buyer_offer": {
                "unit_price": round(floor * 0.75, 2),
                "min_volume": 1000,
                "payment_terms_days": 30,
                "delivery_days": 14,
                "recurring": False,
            },
        },
        headers=_headers(acme),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "OPEN"
    assert body["merchant_move"]["action"] == "counter_offer"
    counter = CounterOffer.model_validate(body["merchant_move"]["offer"])
    assert check(counter, PolicySession(product.id, 0)).passed


def test_merchant_accepts_valid_offer(client, acme, stack):
    db, _, _, _ = stack
    product = db.get_product("elec-conn-001")
    offer = _passing_offer(product)

    negotiation_id = client.post(
        "/quote",
        json={"product_id": "elec-conn-001", "buyer_id": "acme"},
        headers=_headers(acme),
    ).json()["negotiation_id"]

    r = client.post(
        "/negotiate",
        json={"negotiation_id": negotiation_id, "buyer_offer": offer.model_dump()},
        headers=_headers(acme),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "CLOSED_WON"
    assert body["merchant_move"]["action"] == "accept"
    assert body["final_terms"] == offer.model_dump()


def test_audit_two_rows_per_turn(client, acme, stack, monkeypatch):
    db, _, _, merchant_module = stack
    product = db.get_product("elec-conn-001")
    good = _passing_offer(product)
    # Force LLM path: buyer lowballs; merchant proposes a legal package.
    fake = FakeLLM([{"name": "counter_offer", "arguments": good.model_dump()}])
    monkeypatch.setattr(merchant_module, "LLMClient", fake)

    negotiation_id = client.post(
        "/quote",
        json={"product_id": "elec-conn-001", "buyer_id": "acme"},
        headers=_headers(acme),
    ).json()["negotiation_id"]

    floor = product.volume_tiers[0].floor_price
    client.post(
        "/negotiate",
        json={
            "negotiation_id": negotiation_id,
            "buyer_offer": {
                "unit_price": round(floor * 0.75, 2),
                "min_volume": 1000,
                "payment_terms_days": 30,
                "delivery_days": 14,
                "recurring": False,
            },
        },
        headers=_headers(acme),
    )

    trail = db.get_audit_trail(negotiation_id)
    turn_rows = [e for e in trail if e["turn"] == 1]
    actors = [e["actor"] for e in turn_rows]
    assert "buyer_agent" in actors
    assert "merchant_llm" in actors
    assert "policy_engine" in actors
    assert any(e["actor"] == "merchant_llm" and e["verdict"] is None for e in turn_rows)
    assert any(e["actor"] == "policy_engine" and e["verdict"] in ("PASS", "FAIL") for e in turn_rows)


def test_no_payments_import():
    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    targets = [
        root / "agents" / "merchant.py",
        root / "main.py",
    ]
    for path in targets:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "payments" not in alias.name, f"{path} imports {alias.name}"
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert "payments" not in mod, f"{path} imports from {mod}"
                for alias in node.names:
                    assert alias.name != "payments", f"{path} imports payments"


def test_merchant_malformed_toolcall_escalates(client, acme, stack, monkeypatch):
    _, _, _, merchant_module = stack
    fake = FakeLLM([None, None])  # text-only twice → escalate after re-prompt
    monkeypatch.setattr(merchant_module, "LLMClient", fake)

    negotiation_id = client.post(
        "/quote",
        json={"product_id": "elec-conn-001", "buyer_id": "acme"},
        headers=_headers(acme),
    ).json()["negotiation_id"]

    product = stack[0].get_product("elec-conn-001")
    floor = product.volume_tiers[0].floor_price
    r = client.post(
        "/negotiate",
        json={
            "negotiation_id": negotiation_id,
            "buyer_offer": {
                "unit_price": round(floor * 0.75, 2),
                "min_volume": 1000,
                "payment_terms_days": 30,
                "delivery_days": 14,
                "recurring": False,
            },
        },
        headers=_headers(acme),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ESCALATED"
    assert body["merchant_move"]["action"] == "escalate"
    assert fake.calls == 2

    trail = stack[0].get_audit_trail(negotiation_id)
    assert any(e["action"] == "malformed_proposal" for e in trail)
    assert any(e["action"] == "escalate_to_human" for e in trail)
