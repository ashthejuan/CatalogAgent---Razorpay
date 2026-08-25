"""Deterministic bounds enforcement. ZERO LLM calls."""

from dataclasses import dataclass

from app import db
from app.schemas import CounterOffer, Verdict


@dataclass
class PolicySession:
    product_id: str
    turn_count: int
    max_turns: int = 4


def _product_and_tier(offer: CounterOffer, session: PolicySession):
    product = db.get_product(session.product_id)
    if product is None:
        raise ValueError(f"unknown product_id: {session.product_id}")
    tiers = sorted(product.volume_tiers, key=lambda tier: tier.min_qty)
    tier = max(
        (candidate for candidate in tiers if candidate.min_qty <= offer.min_volume),
        default=tiers[-1],
        key=lambda candidate: candidate.min_qty,
    )
    return product, tier


def check(offer: CounterOffer, session: PolicySession) -> Verdict:
    product, tier = _product_and_tier(offer, session)

    if offer.unit_price < tier.floor_price:
        return Verdict(
            passed=False,
            reason=(
                f"unit_price {offer.unit_price:.2f} < floor "
                f"{tier.floor_price:.2f} for tier {tier.min_qty}+"
            ),
        )
    if offer.payment_terms_days > 45:
        return Verdict(passed=False, reason=f"payment_terms_days {offer.payment_terms_days} > max 45")
    if offer.delivery_days < product.lead_time_min_days:
        return Verdict(
            passed=False,
            reason=f"delivery_days {offer.delivery_days} < lead_time_min {product.lead_time_min_days}",
        )
    if offer.min_volume > product.stock:
        return Verdict(passed=False, reason=f"min_volume {offer.min_volume} > stock {product.stock}")
    if session.turn_count >= session.max_turns:
        return Verdict(passed=False, reason=f"turn_count {session.turn_count} >= max_turns {session.max_turns}")

    margin = offer.unit_price - tier.floor_price
    margin -= offer.payment_terms_days * 0.0005 * offer.unit_price
    if offer.delivery_days < product.lead_time_min_days:
        margin -= (
            product.lead_time_min_days - offer.delivery_days
        ) * 0.01 * offer.unit_price
    if margin < 0:
        return Verdict(passed=False, reason=f"composite margin negative: {margin:.2f} after terms cost")
    return Verdict(passed=True, reason="all bounds satisfied")


def best_legal_counter(session: PolicySession) -> CounterOffer:
    product = db.get_product(session.product_id)
    if product is None:
        raise ValueError(f"unknown product_id: {session.product_id}")
    tier = max(product.volume_tiers, key=lambda candidate: candidate.min_qty)
    return CounterOffer(
        # Net-30 costs money; gross up the floor so this fallback is actually legal.
        unit_price=tier.floor_price / (1 - 30 * 0.0005),
        min_volume=tier.min_qty,
        payment_terms_days=30,
        delivery_days=product.lead_time_max_days,
        recurring=False,
    )
