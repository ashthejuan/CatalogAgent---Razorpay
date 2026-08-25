import importlib
import random

import pytest

from app.schemas import CounterOffer


def _reload_db_stack():
    import app.db as db_module
    import app.seed as seed_module

    importlib.reload(db_module)
    importlib.reload(seed_module)
    return db_module, seed_module


@pytest.fixture
def policy_context(tmp_path, monkeypatch):
    monkeypatch.setenv("CATALOGAGENT_DB_PATH", str(tmp_path / "test.db"))
    db_module, seed_module = _reload_db_stack()
    db_module.init_db()
    seed_module.seed_products()
    import app.policy as policy_module

    importlib.reload(policy_module)
    yield db_module, policy_module
    _reload_db_stack()


def _offer(**changes):
    values = dict(
        unit_price=11.20,
        min_volume=1000,
        payment_terms_days=0,
        delivery_days=7,
        recurring=False,
    )
    values.update(changes)
    return CounterOffer(**values)


def test_pass_at_tier_boundary(policy_context):
    db, policy = policy_context
    product = db.get_product("elec-conn-001")
    tier = product.volume_tiers[0]
    verdict = policy.check(
        _offer(unit_price=tier.floor_price, min_volume=tier.min_qty),
        policy.PolicySession(product.id, 0),
    )
    assert verdict.passed


def test_each_field_violation(policy_context):
    db, policy = policy_context
    product = db.get_product("elec-conn-001")
    session = policy.PolicySession(product.id, 0)
    cases = [
        (_offer(unit_price=9.0), "floor"),
        (_offer(payment_terms_days=46), "payment_terms"),
        (_offer(delivery_days=6), "delivery"),
        (_offer(min_volume=product.stock + 1), "stock"),
        (_offer(), "max_turns"),
    ]
    for offer, keyword in cases:
        current_session = policy.PolicySession(product.id, 4) if keyword == "max_turns" else session
        verdict = policy.check(offer, current_session)
        assert not verdict.passed
        assert keyword in verdict.reason


def test_composite_margin_trap(policy_context):
    db, policy = policy_context
    product = db.get_product("elec-conn-001")
    offer = _offer(unit_price=product.volume_tiers[0].floor_price, payment_terms_days=45)
    verdict = policy.check(offer, policy.PolicySession(product.id, 0))
    assert not verdict.passed
    assert "negative" in verdict.reason or "margin" in verdict.reason


def test_turn_limit(policy_context):
    db, policy = policy_context
    verdict = policy.check(_offer(), policy.PolicySession("elec-conn-001", 4))
    assert not verdict.passed
    assert "max_turns" in verdict.reason


def test_best_legal_counter_is_legal(policy_context):
    db, policy = policy_context
    session = policy.PolicySession("elec-conn-001", 0)
    assert policy.check(policy.best_legal_counter(session), session).passed


def test_fuzz_no_illegal_pass(policy_context):
    db, policy = policy_context
    product = db.get_product("elec-conn-001")
    session = policy.PolicySession(product.id, 0)
    rng = random.Random(0)
    for _ in range(500):
        try:
            offer = CounterOffer(
                unit_price=rng.uniform(-20, 30),
                min_volume=rng.randint(-1000, 30000),
                payment_terms_days=rng.randint(-20, 80),
                delivery_days=rng.randint(-10, 40),
                recurring=bool(rng.randint(0, 1)),
            )
        except ValueError:
            continue
        verdict = policy.check(offer, session)
        if verdict.passed:
            tiers = sorted(product.volume_tiers, key=lambda tier: tier.min_qty)
            tier = max(
                (t for t in tiers if t.min_qty <= offer.min_volume),
                default=tiers[-1],
                key=lambda t: t.min_qty,
            )
            margin = offer.unit_price - tier.floor_price
            margin -= offer.payment_terms_days * 0.0005 * offer.unit_price
            if offer.delivery_days < product.lead_time_min_days:
                margin -= (product.lead_time_min_days - offer.delivery_days) * 0.01 * offer.unit_price
            assert offer.unit_price >= tier.floor_price
            assert offer.payment_terms_days <= 45
            assert offer.delivery_days >= product.lead_time_min_days
            assert offer.min_volume <= product.stock
            assert session.turn_count < session.max_turns
            assert margin >= 0
