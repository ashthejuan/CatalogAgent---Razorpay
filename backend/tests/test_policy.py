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
    # Price exactly at floor, with standard (non-rush) delivery + immediate terms:
    # isolates the price-floor boundary. Rush/terms erode margin separately.
    verdict = policy.check(
        _offer(
            unit_price=tier.floor_price,
            min_volume=tier.min_qty,
            payment_terms_days=0,
            delivery_days=product.lead_time_max_days,
        ),
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
            qualifying = [t for t in tiers if t.min_qty <= offer.min_volume]
            assert qualifying, "passed but volume below all MOQs"
            tier = max(qualifying, key=lambda t: t.min_qty)
            assert offer.unit_price >= tier.floor_price
            assert offer.payment_terms_days <= 45
            assert offer.delivery_days >= product.lead_time_min_days
            assert offer.min_volume <= product.stock
            assert session.turn_count < session.max_turns
            margin = offer.unit_price - tier.floor_price
            margin -= offer.payment_terms_days * 0.0005 * offer.unit_price
            if offer.delivery_days < product.lead_time_max_days:
                margin -= (product.lead_time_max_days - offer.delivery_days) * 0.01 * offer.unit_price
            if offer.recurring:
                margin += 0.02 * offer.unit_price
            assert margin >= 0


def test_sub_moq_fails(policy_context):
    db, policy = policy_context
    product = db.get_product("elec-conn-001")
    lowest_moq = min(t.min_qty for t in product.volume_tiers)
    # volume below the lowest tier MOQ must FAIL (not be graded against top tier)
    verdict = policy.check(_offer(min_volume=lowest_moq - 1), policy.PolicySession(product.id, 0))
    assert not verdict.passed
    assert "below minimum tier MOQ" in verdict.reason
    # negative volume also fails
    neg = policy.check(_offer(min_volume=-100), policy.PolicySession(product.id, 0))
    assert not neg.passed
    assert "below minimum tier MOQ" in neg.reason


def test_rush_delivery_margin_trap(policy_context):
    db, policy = policy_context
    product = db.get_product("elec-conn-001")
    # price at floor, standard terms, but delivery far below the STANDARD max
    # lead time (not just the hard min) — margin must erode via rush penalty.
    offer = _offer(unit_price=product.volume_tiers[0].floor_price, payment_terms_days=0, delivery_days=product.lead_time_min_days)
    verdict = policy.check(offer, policy.PolicySession(product.id, 0))
    assert not verdict.passed
    assert "composite margin negative" in verdict.reason or "rush" in verdict.reason


def test_recurring_is_considered(policy_context):
    db, policy = policy_context
    product = db.get_product("elec-conn-001")
    session = policy.PolicySession(product.id, 0)
    # Tight one-off: price at floor, standard terms, delivery 1 day inside the
    # standard max lead (minor rush nick). Margin goes slightly negative -> FAIL.
    one_off = _offer(
        unit_price=product.volume_tiers[0].floor_price,
        payment_terms_days=0,
        delivery_days=product.lead_time_max_days - 1,
        recurring=False,
    )
    assert not policy.check(one_off, session).passed
    # Same package but recurring=True earns a margin concession -> now PASSES.
    recurring = _offer(
        unit_price=product.volume_tiers[0].floor_price,
        payment_terms_days=0,
        delivery_days=product.lead_time_max_days - 1,
        recurring=True,
    )
    verdict = policy.check(recurring, session)
    assert verdict.passed
    # recurring leverage is reflected in the higher effective margin vs one-off
    assert "effective margin" in verdict.reason
    # And it must NOT pass when recurring is the only lever with a deep rush.
    deep_rush = _offer(
        unit_price=product.volume_tiers[0].floor_price,
        payment_terms_days=0,
        delivery_days=product.lead_time_min_days,
        recurring=True,
    )
    assert not policy.check(deep_rush, session).passed


def test_pass_reason_includes_margin_pct(policy_context):
    db, policy = policy_context
    product = db.get_product("elec-conn-001")
    verdict = policy.check(
        _offer(unit_price=product.volume_tiers[0].floor_price, payment_terms_days=0, delivery_days=product.lead_time_max_days, recurring=False),
        policy.PolicySession(product.id, 0),
    )
    assert verdict.passed
    assert "effective margin" in verdict.reason


def test_counter_offer_rounds_unit_price_and_coerces_ints():
    from app.schemas import CounterOffer

    offer = CounterOffer.model_validate(
        {
            "unit_price": "10.050761421319798",
            "min_volume": "6000",
            "payment_terms_days": "30",
            "delivery_days": 21.0,
            "recurring": "true",
        }
    )
    assert offer.unit_price == 10.05
    assert offer.min_volume == 6000
    assert offer.payment_terms_days == 30
    assert offer.delivery_days == 21
    assert offer.recurring is True

