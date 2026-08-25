from __future__ import annotations

import ast
import importlib
from pathlib import Path

from app.schemas import CounterOffer


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def chat_with_tools(self, system_prompt, messages, tools):
        self.calls += 1
        return self.responses.pop(0) if self.responses else None


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("CATALOGAGENT_DB_PATH", str(tmp_path / "buyer.db"))
    import app.db as db
    import app.seed as seed

    importlib.reload(db)
    importlib.reload(seed)
    db.init_db()
    seed.seed_products()
    import app.agents.buyer as buyer

    importlib.reload(buyer)
    return db, buyer


def test_buyer_returns_valid_counter_offer(tmp_path, monkeypatch):
    db, buyer = _setup(tmp_path, monkeypatch)
    offer = CounterOffer(
        unit_price=10.25,
        min_volume=5000,
        payment_terms_days=30,
        delivery_days=21,
        recurring=True,
    )
    result = buyer.propose_buyer_offer(
        negotiation_id="neg_test",
        product_id="elec-conn-001",
        turn_count=0,
        history=[],
        budget_cap=50_000,
        llm=FakeLLM([{"name": "counter_offer", "arguments": offer.model_dump()}]),
    )
    assert result == offer
    assert db.get_product("elec-conn-001") is not None


def test_buyer_malformed_tool_call_falls_back(tmp_path, monkeypatch):
    _, buyer = _setup(tmp_path, monkeypatch)
    llm = FakeLLM([None, {"name": "wrong", "arguments": {}}])
    result = buyer.propose_buyer_offer(
        negotiation_id="neg_bad",
        product_id="elec-conn-001",
        turn_count=0,
        history=[],
        budget_cap=50_000,
        llm=llm,
    )
    assert result.min_volume == 1000
    assert result.unit_price == 10.08
    assert llm.calls == 2


def test_buyer_does_not_import_merchant():
    path = Path(__file__).resolve().parent.parent / "app" / "agents" / "buyer.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name != "merchant" and not alias.name.endswith(".merchant") for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module != "app.agents.merchant"
