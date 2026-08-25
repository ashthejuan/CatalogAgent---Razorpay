import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.main import app
from app.schemas import CounterOffer


@pytest.fixture
def policy_ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("CATALOGAGENT_DB_PATH", str(tmp_path / "test.db"))
    import importlib

    import app.seed as seed_module

    importlib.reload(db_module)
    importlib.reload(seed_module)
    db_module.init_db()
    seed_module.seed_products()
    yield db_module, seed_module
    importlib.reload(db_module)


def test_append_audit_returns_id_and_persists(policy_ctx):
    db, _ = policy_ctx
    rid = db.append_audit(
        "neg_1", 1, "merchant_llm", "counter_offer", {"unit_price": 10.5, "min_volume": 5000}
    )
    assert isinstance(rid, int) and rid >= 1
    trail = db.get_audit_trail("neg_1")
    assert len(trail) == 1
    assert trail[0]["actor"] == "merchant_llm"
    assert trail[0]["action"] == "counter_offer"
    assert trail[0]["payload"]["unit_price"] == 10.5


def test_audit_tracks_both_proposal_and_verdict(policy_ctx):
    db, _ = policy_ctx
    # simulate one negotiation turn: proposal row + guardrail verdict row
    db.append_audit("neg_2", 1, "merchant_llm", "counter_offer", {"unit_price": 9.0, "min_volume": 5000})
    db.append_audit(
        "neg_2", 1, "policy_engine", "guardrail_check", {"unit_price": 9.0}, verdict="FAIL", reason="unit_price 9.00 < floor 10.30 for tier 5000+"
    )
    trail = db.get_audit_trail("neg_2")
    assert len(trail) == 2
    assert trail[0]["verdict"] is None  # proposal has no verdict
    assert trail[1]["verdict"] == "FAIL"
    assert "floor" in trail[1]["reason"]


def test_audit_ordering_is_chronological(policy_ctx):
    db, _ = policy_ctx
    for t in range(3):
        db.append_audit(f"neg_3", t, "buyer_agent", "counter_offer", {"turn": t})
    trail = db.get_audit_trail("neg_3")
    turns = [e["turn"] for e in trail]
    assert turns == [0, 1, 2]


def test_audit_is_append_only_no_update_delete_in_source():
    # The codebase must never issue UPDATE/DELETE against audit_log.
    import pathlib

    src = pathlib.Path(__file__).resolve().parent.parent / "app" / "db.py"
    text = src.read_text()
    assert "UPDATE audit_log" not in text
    assert "DELETE FROM audit_log" not in text
    # insert path exists
    assert "INSERT INTO audit_log" in text


def test_audit_empty_returns_empty(policy_ctx):
    db, _ = policy_ctx
    assert db.get_audit_trail("nonexistent") == []
    text = db.format_audit_trail("nonexistent")
    assert "no audit trail" in text


def test_audit_payload_roundtrips_json(policy_ctx):
    db, _ = policy_ctx
    offer = CounterOffer(unit_price=11.0, min_volume=2000, payment_terms_days=30, delivery_days=14, recurring=False)
    db.append_audit("neg_4", 1, "merchant_llm", "counter_offer", offer.model_dump())
    trail = db.get_audit_trail("neg_4")
    assert trail[0]["payload"]["payment_terms_days"] == 30
    assert trail[0]["payload"]["recurring"] is False


def test_audit_formatted_multiturn_snapshot(policy_ctx):
    db, _ = policy_ctx
    db.append_audit("neg_5", 1, "buyer_agent", "counter_offer", {"unit_price": 9.0, "min_volume": 5000})
    db.append_audit("neg_5", 1, "policy_engine", "guardrail_check", {"unit_price": 9.0}, verdict="FAIL", reason="unit_price 9.00 < floor 10.30 for tier 5000+")
    db.append_audit("neg_5", 2, "merchant_llm", "counter_offer", {"unit_price": 10.30, "min_volume": 5000})
    db.append_audit("neg_5", 2, "policy_engine", "guardrail_check", {"unit_price": 10.30}, verdict="PASS", reason="all bounds satisfied; effective margin 0.0%")
    text = db.format_audit_trail("neg_5")
    # both sides of two turns present, in order, with verdicts
    assert text.count("FAIL") == 1
    assert text.count("PASS") == 1
    assert "buyer_agent" in text and "merchant_llm" in text and "policy_engine" in text
    lines = text.splitlines()
    assert lines[1].startswith("-")  # header rule
    assert "turn 1 " in text and "turn 2 " in text


def test_audit_endpoint_http(policy_ctx):
    db, _ = policy_ctx
    db.append_audit("neg_6", 1, "merchant_llm", "counter_offer", {"unit_price": 10.5})
    with TestClient(app) as client:
        r = client.get("/audit/neg_6")
    assert r.status_code == 200
    body = r.json()
    assert body["negotiation_id"] == "neg_6"
    assert len(body["trail"]) == 1
    assert body["trail"][0]["actor"] == "merchant_llm"
    assert "text" in body and "neg_6" in body["text"]


def test_audit_endpoint_empty_http(policy_ctx):
    with TestClient(app) as client:
        r = client.get("/audit/does_not_exist")
    assert r.status_code == 200
    assert r.json()["trail"] == []
    assert "no audit trail" in r.json()["text"]
