from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)

    def __call__(self):
        return self

    def chat_with_tools(self, system_prompt, messages, tools):
        return self.responses.pop(0) if self.responses else None


@pytest.fixture
def stack(tmp_path, monkeypatch):
    monkeypatch.setenv("CATALOGAGENT_DB_PATH", str(tmp_path / "phase5.db"))
    import app.db as db
    import app.seed as seed
    import app.create_buyer as create_buyer
    import app.agents.buyer as buyer
    import app.agents.merchant as merchant
    import app.main as main

    importlib.reload(db)
    importlib.reload(seed)
    importlib.reload(create_buyer)
    importlib.reload(buyer)
    importlib.reload(merchant)
    importlib.reload(main)
    db.init_db()
    seed.seed_products()
    return db, create_buyer, buyer, merchant, main


def _run(stack, monkeypatch, buyer_responses, merchant_responses):
    db, create_buyer, buyer_module, merchant_module, main = stack
    buyer_id = "phase5_buyer"
    key = create_buyer.create_buyer(buyer_id, 50_000)
    buyer_llm = FakeLLM(buyer_responses)
    monkeypatch.setattr(merchant_module, "LLMClient", lambda: FakeLLM(merchant_responses))
    with TestClient(main.app) as client:
        quote = client.post(
            "/quote",
            json={"product_id": "elec-conn-001", "buyer_id": buyer_id},
            headers={"X-Buyer-Key": key},
        )
        neg_id = quote.json()["negotiation_id"]
        for _ in range(4):
            negotiation = db.get_negotiation(neg_id)
            buyer = db.get_buyer_by_id(buyer_id)
            offer = buyer_module.propose_buyer_offer(
                negotiation_id=neg_id,
                product_id="elec-conn-001",
                turn_count=negotiation.turn_count,
                history=negotiation.history,
                budget_cap=buyer.budget_cap,
                llm=buyer_llm,
            )
            response = client.post(
                "/negotiate",
                json={"negotiation_id": neg_id, "buyer_offer": offer.model_dump()},
                headers={"X-Buyer-Key": key},
            )
            body = response.json()
            if body["status"] in {"CLOSED_WON", "ESCALATED"}:
                break
    return db, neg_id, db.get_negotiation(neg_id)


def _call(offer):
    return {"name": "counter_offer", "arguments": offer}


def test_reasonable_closes(stack, monkeypatch):
    good = {"unit_price": 11.5, "min_volume": 1000, "payment_terms_days": 0, "delivery_days": 21, "recurring": False}
    db, _, negotiation = _run(stack, monkeypatch, [_call(good)], [])
    assert negotiation.status == "CLOSED_WON"


def test_aggressive_has_fail_then_fallback(stack, monkeypatch):
    bad = {"unit_price": 8.4, "min_volume": 1000, "payment_terms_days": 30, "delivery_days": 21, "recurring": False}
    merchant_bad = {"unit_price": 8.4, "min_volume": 1000, "payment_terms_days": 30, "delivery_days": 21, "recurring": False}
    db, neg_id, negotiation = _run(stack, monkeypatch, [_call(bad)] * 4, [_call(merchant_bad)] * 4)
    assert negotiation.status in {"OPEN", "ESCALATED"}
    trail = db.get_audit_trail(neg_id)
    fail = next(i for i, row in enumerate(trail) if row["actor"] == "policy_engine" and row["verdict"] == "FAIL")
    assert any(row["actor"] == "merchant_llm" and row["action"] == "counter_offer" for row in trail[fail + 1 :])


def test_creative_reaches_valid_state(stack, monkeypatch):
    creative = {"unit_price": 10.8, "min_volume": 5000, "payment_terms_days": 0, "delivery_days": 21, "recurring": True}
    db, _, negotiation = _run(stack, monkeypatch, [_call(creative)], [])
    assert negotiation.status in {"CLOSED_WON", "ESCALATED", "OPEN"}


def test_buyer_ast_is_merchant_free():
    path = Path(__file__).resolve().parent.parent / "app" / "agents" / "buyer.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assert not any(
        isinstance(node, ast.ImportFrom) and node.module == "app.agents.merchant"
        for node in ast.walk(tree)
    )
