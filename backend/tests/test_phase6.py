import ast
import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import invoicing, payments
from app.schemas import OrderTerms


def test_create_order_payload(monkeypatch):
    seen = {}

    def fake_post(payload):
        seen.update(payload)
        return {"id": "order_test_123", "amount": payload["amount"], "currency": "INR"}

    monkeypatch.setattr(payments, "_post_order", fake_post)
    terms = OrderTerms(
        unit_price=11.50,
        min_volume=1000,
        payment_terms_days=0,
        delivery_days=21,
        recurring=False,
        product_id="p1",
        negotiation_id="neg1",
    )
    result = payments.create_order(terms)
    assert result["id"] == "order_test_123"
    assert seen["amount"] == 1_150_000
    assert seen["receipt"] == "neg1"
    assert seen["notes"] == terms.model_dump()


def test_agents_have_no_money_action_references():
    for name in ("merchant.py", "buyer.py"):
        text = (Path(__file__).parents[1] / "app" / "agents" / name).read_text()
        assert not any(term in text for term in ("payments", "razorpay", "create_order"))


@pytest.fixture
def phase6_stack(tmp_path, monkeypatch):
    monkeypatch.setenv("CATALOGAGENT_DB_PATH", str(tmp_path / "phase6.db"))
    import app.db as db
    import app.seed as seed
    import app.create_buyer as create_buyer
    import app.agents.buyer as buyer
    import app.agents.merchant as merchant
    import app.main as main

    for module in (db, seed, create_buyer, buyer, merchant, main):
        importlib.reload(module)
    db.init_db()
    seed.seed_products()
    return db, create_buyer, buyer, merchant, main


def test_invoice_uses_stored_terms(phase6_stack, tmp_path):
    db, create_buyer, _, _, _ = phase6_stack
    buyer_id = "invoice_buyer"
    create_buyer.create_buyer(buyer_id, 50_000)
    neg_id = db.create_negotiation(buyer_id, "elec-conn-001")
    terms = OrderTerms(
        unit_price=11.5, min_volume=1000, payment_terms_days=0,
        delivery_days=21, recurring=False, product_id="elec-conn-001",
        negotiation_id=neg_id,
    )
    row = {"id": "order_invoice_test", "negotiation_id": neg_id,
           "terms": json.dumps(terms.model_dump()), "razorpay_order_id": "rp_test",
           "invoice_path": None}
    db.insert_order(row)
    path = invoicing.save_invoice(row)
    db.update_order_invoice(row["id"], path)
    data = invoicing.get_invoice_bytes(row["id"])
    assert Path(path).exists() and data
    try:
        from pypdf import PdfReader
    except ImportError:
        return
    text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    for key in ("11.5", "1000", "0", "21", "False"):
        assert key in text


class _FakeLLM:
    def chat_with_tools(self, system_prompt, messages, tools):
        return {"name": "counter_offer", "arguments": {
            "unit_price": 11.5, "min_volume": 1000, "payment_terms_days": 0,
            "delivery_days": 21, "recurring": False,
        }}


def test_e2e_order_invoice_and_ownership(phase6_stack, monkeypatch):
    db, create_buyer, buyer, merchant, main = phase6_stack
    monkeypatch.setattr(buyer, "LLMClient", lambda: _FakeLLM())
    monkeypatch.setattr(merchant, "LLMClient", lambda: _FakeLLM())
    monkeypatch.setattr(payments, "_post_order", lambda payload: {
        "id": "order_e2e", "amount": payload["amount"], "currency": "INR"
    })
    key = create_buyer.create_buyer("e2e_buyer", 50_000)
    foreign_key = create_buyer.create_buyer("foreign_buyer", 50_000)
    with TestClient(main.app) as client:
        quote = client.post("/quote", json={"product_id": "elec-conn-001", "buyer_id": "e2e_buyer"},
                            headers={"X-Buyer-Key": key})
        neg_id = quote.json()["negotiation_id"]
        response = client.post("/negotiate", json={"negotiation_id": neg_id, "buyer_offer": {
            "unit_price": 11.5, "min_volume": 1000, "payment_terms_days": 0,
            "delivery_days": 21, "recurring": False}}, headers={"X-Buyer-Key": key})
        assert response.json()["status"] == "CLOSED_WON"
        order = db.get_order_by_negotiation(neg_id)
        assert order and order["razorpay_order_id"] == "order_e2e"
        assert Path(order["invoice_path"]).exists()
        assert client.get(f"/invoices/{order['id']}", headers={"X-Buyer-Key": key}).status_code == 200
        assert client.get(f"/invoices/{order['id']}", headers={"X-Buyer-Key": foreign_key}).status_code == 403
