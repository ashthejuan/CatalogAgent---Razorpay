"""Merchant agent loop — mocked LLM, real policy + audit."""

from __future__ import annotations

import importlib
import pathlib

import pytest
from pydantic import ValidationError

from app.agents import merchant as merchant_module
from app.agents.merchant import run_merchant_turn
from app.llm_client import LLMClient
from app.policy import PolicySession, best_legal_counter, check
from app.schemas import CounterOffer


def _reload_db_stack(tmp_path, monkeypatch):
    monkeypatch.setenv("CATALOGAGENT_DB_PATH", str(tmp_path / "test.db"))
    import app.db as db_module
    import app.seed as seed_module

    importlib.reload(db_module)
    importlib.reload(seed_module)
    db_module.init_db()
    seed_module.seed_products()
    importlib.reload(merchant_module)
    return db_module


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    db = _reload_db_stack(tmp_path, monkeypatch)
    yield db
    _reload_db_stack(tmp_path, monkeypatch)


def _buyer_offer(**kw) -> CounterOffer:
    base = dict(
        unit_price=9.0,
        min_volume=5000,
        payment_terms_days=30,
        delivery_days=14,
        recurring=False,
    )
    base.update(kw)
    return CounterOffer(**base)


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
    def __init__(self, responses: list[dict | None]):
        self.responses = list(responses)
        self.calls = 0

    def chat_with_tools(self, system_prompt, messages, tools):
        self.calls += 1
        if not self.responses:
            return None
        return self.responses.pop(0)


def test_pass_proposal_audits_proposal_and_verdict(ctx):
    product = ctx.get_product("elec-conn-001")
    offer = _passing_offer(product)
    llm = FakeLLM([{"name": "counter_offer", "arguments": offer.model_dump()}])

    result = run_merchant_turn(
        negotiation_id="neg_pass",
        product_id=product.id,
        turn=1,
        turn_count=0,
        buyer_offer=_buyer_offer(),
        llm=llm,  # type: ignore[arg-type]
    )

    assert result.action == "counter_offer"
    assert result.offer == offer
    assert result.verdict is not None and result.verdict.passed
    trail = ctx.get_audit_trail("neg_pass")
    assert len(trail) == 2
    assert trail[0]["actor"] == "merchant_llm" and trail[0]["verdict"] is None
    assert trail[1]["actor"] == "policy_engine" and trail[1]["verdict"] == "PASS"


def test_margin_fail_returns_fallback_and_audits_fail_plus_fallback(ctx):
    product = ctx.get_product("elec-conn-001")
    tier = max(product.volume_tiers, key=lambda t: t.min_qty)
    bad = CounterOffer(
        unit_price=tier.floor_price,
        min_volume=tier.min_qty,
        payment_terms_days=45,
        delivery_days=product.lead_time_max_days,
        recurring=False,
    )
    llm = FakeLLM([{"name": "counter_offer", "arguments": bad.model_dump()}])

    result = run_merchant_turn(
        negotiation_id="neg_margin",
        product_id=product.id,
        turn=1,
        turn_count=0,
        buyer_offer=_buyer_offer(),
        llm=llm,  # type: ignore[arg-type]
    )

    assert result.action == "counter_offer"
    assert result.verdict is not None and not result.verdict.passed
    assert result.reason is not None
    assert result.offer == best_legal_counter(PolicySession(product.id, 0))
    trail = ctx.get_audit_trail("neg_margin")
    assert len(trail) == 3
    assert trail[1]["verdict"] == "FAIL"
    assert trail[2]["actor"] == "merchant_llm" and trail[2]["action"] == "counter_offer"


def test_structural_sub_moq_escalates(ctx):
    product = ctx.get_product("elec-conn-001")
    lowest = min(t.min_qty for t in product.volume_tiers)
    bad = CounterOffer(
        unit_price=20.0,
        min_volume=lowest - 1,
        payment_terms_days=0,
        delivery_days=product.lead_time_max_days,
        recurring=False,
    )
    llm = FakeLLM([{"name": "counter_offer", "arguments": bad.model_dump()}])

    result = run_merchant_turn(
        negotiation_id="neg_struct",
        product_id=product.id,
        turn=1,
        turn_count=0,
        buyer_offer=_buyer_offer(),
        llm=llm,  # type: ignore[arg-type]
    )

    assert result.action == "escalate"
    assert "MOQ" in (result.reason or "")
    trail = ctx.get_audit_trail("neg_struct")
    assert trail[-1]["action"] == "escalate_to_human"


def test_malformed_reprompt_then_success(ctx):
    product = ctx.get_product("elec-conn-001")
    good = _passing_offer(product)
    llm = FakeLLM(
        [
            None,
            {"name": "counter_offer", "arguments": good.model_dump()},
        ]
    )

    result = run_merchant_turn(
        negotiation_id="neg_repair",
        product_id=product.id,
        turn=1,
        turn_count=0,
        buyer_offer=_buyer_offer(),
        llm=llm,  # type: ignore[arg-type]
    )

    assert result.action == "counter_offer"
    assert llm.calls == 2
    trail = ctx.get_audit_trail("neg_repair")
    assert trail[0]["action"] == "malformed_proposal" and trail[0]["verdict"] == "FAIL"
    assert trail[-1]["verdict"] == "PASS"


def test_malformed_twice_escalates(ctx):
    llm = FakeLLM([None, None])

    result = run_merchant_turn(
        negotiation_id="neg_bad",
        product_id="elec-conn-001",
        turn=1,
        turn_count=0,
        buyer_offer=_buyer_offer(),
        llm=llm,  # type: ignore[arg-type]
    )

    assert result.action == "escalate"
    trail = ctx.get_audit_trail("neg_bad")
    assert trail[0]["action"] == "malformed_proposal"
    assert trail[1]["action"] == "malformed_proposal"
    assert trail[-1]["action"] == "escalate_to_human"


def test_max_turns_accepts_last_valid_buyer_offer(ctx):
    product = ctx.get_product("elec-conn-001")
    valid = _passing_offer(product)
    assert check(valid, PolicySession(product.id, 3)).passed

    result = run_merchant_turn(
        negotiation_id="neg_accept",
        product_id=product.id,
        turn=4,
        turn_count=4,
        buyer_offer=_buyer_offer(),
        last_valid_buyer_offer=valid,
        llm=FakeLLM([]),  # type: ignore[arg-type]
    )

    assert result.action == "accept"
    assert result.offer == valid
    trail = ctx.get_audit_trail("neg_accept")
    assert trail[0]["action"] == "accept"


def test_max_turns_without_valid_offer_escalates(ctx):
    result = run_merchant_turn(
        negotiation_id="neg_dead",
        product_id="elec-conn-001",
        turn=4,
        turn_count=4,
        buyer_offer=_buyer_offer(),
        llm=FakeLLM([]),  # type: ignore[arg-type]
    )

    assert result.action == "escalate"
    assert "max_turns" in (result.reason or "")


def test_merchant_module_never_imports_payments():
    src = pathlib.Path(__file__).resolve().parent.parent / "app" / "agents" / "merchant.py"
    text = src.read_text()
    assert "import app.payments" not in text
    assert "from app.payments" not in text
    assert "import payments" not in text


def test_validate_tool_call_rejects_wrong_tool():
    with pytest.raises(ValueError, match="counter_offer"):
        merchant_module._validate_tool_call({"name": "accept_offer", "arguments": {}})
