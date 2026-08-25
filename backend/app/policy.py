"""Deterministic bounds enforcement. ZERO LLM calls."""

from dataclasses import dataclass

from app import db
from app.schemas import CounterOffer, Verdict

# Working-capital cost of deferred payment: fraction of unit price charged
# per day of credit beyond immediate (0.05%/day).
TERMS_COST_PER_DAY = 0.0005
# Rush penalty: margin given up per day the delivery beats the STANDARD max
# lead time (lead_time_max_days). Being early vs the max is the real cost to
# the merchant, not the hard minimum gate.
RUSH_COST_PER_DAY = 0.01
# Recurring-commitment concession: recurring business justifies a margin
# give-up vs one-off (predictable revenue is worth a price concession).
RECURRING_CONCESSION = 0.02
MAX_PAYMENT_TERMS_DAYS = 45


@dataclass
class PolicySession:
    product_id: str
    turn_count: int
    max_turns: int = 4


def _resolve_tier(product, min_volume):
    """Return the applicable volume tier for the offered volume.

    Fails (returns None) when the offered volume is below the product's
    minimum order quantity — an adversarial buyer cannot claim a tiny volume
    and be graded against the most lenient (highest) tier.
    """
    tiers = sorted(product.volume_tiers, key=lambda t: t.min_qty)
    qualifying = [t for t in tiers if t.min_qty <= min_volume]
    if not qualifying:
        return None, tiers[0]  # None signals sub-MOQ; tiers[0] = lowest (min MOQ)
    return max(qualifying, key=lambda t: t.min_qty), tiers[0]


def check(offer: CounterOffer, session: PolicySession) -> Verdict:
    product = db.get_product(session.product_id)
    if product is None:
        raise ValueError(f"unknown product_id: {session.product_id}")

    if session.turn_count >= session.max_turns:
        return Verdict(passed=False, reason=f"turn_count {session.turn_count} >= max_turns {session.max_turns}")

    tier, lowest_tier = _resolve_tier(product, offer.min_volume)
    if tier is None:
        return Verdict(
            passed=False,
            reason=f"min_volume {offer.min_volume} below minimum tier MOQ {lowest_tier.min_qty}",
        )

    if offer.unit_price < tier.floor_price:
        return Verdict(
            passed=False,
            reason=(
                f"unit_price {offer.unit_price:.2f} < floor "
                f"{tier.floor_price:.2f} for tier {tier.min_qty}+"
            ),
        )

    if offer.payment_terms_days > MAX_PAYMENT_TERMS_DAYS:
        return Verdict(
            passed=False,
            reason=f"payment_terms_days {offer.payment_terms_days} > max {MAX_PAYMENT_TERMS_DAYS}",
        )

    if offer.delivery_days < product.lead_time_min_days:
        return Verdict(
            passed=False,
            reason=f"delivery_days {offer.delivery_days} < lead_time_min {product.lead_time_min_days}",
        )

    if offer.min_volume > product.stock:
        return Verdict(passed=False, reason=f"min_volume {offer.min_volume} > stock {product.stock}")

    # Composite margin: every concession the buyer extracts erodes effective
    # margin. A price at/above floor can still fail when terms + rush + recurring
    # push effective margin negative.
    margin = offer.unit_price - tier.floor_price
    margin -= offer.payment_terms_days * TERMS_COST_PER_DAY * offer.unit_price
    if offer.delivery_days < product.lead_time_max_days:
        margin -= (product.lead_time_max_days - offer.delivery_days) * RUSH_COST_PER_DAY * offer.unit_price
    if offer.recurring:
        # Recurring commitment is value to the merchant (predictable revenue),
        # so it earns a margin CONCESSION — it HELPS a tight package pass.
        margin += RECURRING_CONCESSION * offer.unit_price
    if margin < 0:
        return Verdict(passed=False, reason=f"composite margin negative: {margin:.2f} after terms/rush/recurring cost")

    margin_pct = (margin / offer.unit_price) * 100
    return Verdict(passed=True, reason=f"all bounds satisfied; effective margin {margin_pct:.1f}%")


def best_legal_counter(session: PolicySession) -> CounterOffer:
    """Graceful fallback: most lenient legal offer given current product.

    Uses the largest tier's floor, grossed up for net-30 working-capital cost
    so the fallback itself passes the composite check. Delivery at the standard
    max lead time (no rush), one-off (no recurring concession).
    """
    product = db.get_product(session.product_id)
    if product is None:
        raise ValueError(f"unknown product_id: {session.product_id}")
    tier = max(product.volume_tiers, key=lambda t: t.min_qty)
    grossed_unit_price = tier.floor_price / (1 - 30 * TERMS_COST_PER_DAY)
    return CounterOffer(
        unit_price=grossed_unit_price,
        min_volume=tier.min_qty,
        payment_terms_days=30,
        delivery_days=product.lead_time_max_days,
        recurring=False,
    )
