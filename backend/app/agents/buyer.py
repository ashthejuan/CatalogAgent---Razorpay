"""Adversarial buyer proposal agent (deliberately isolated from merchant)."""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from app import db
from app.llm_client import LLMClient, counter_offer_tools
from app.schemas import CounterOffer


_SYSTEM_PROMPT = """You are the buyer-side B2B procurement negotiator.
Propose exactly ONE next move by calling the counter_offer tool with all five fields.
Never reply with plain text. You are adversarial but commercially plausible:
probe cheaper volume tiers, repeat low offers, and use walk-away pressure in the
conversation context. Keep every proposal structured. You have a hard budget cap
of {budget_cap}; preserve cash and seek the cheapest acceptable package.

Buyer persona for this negotiation: {persona}
"""


def _product_context(product_id: str) -> dict[str, Any]:
    product = db.get_product(product_id)
    if product is None:
        raise ValueError(f"unknown product_id: {product_id}")
    return {
        "id": product.id,
        "name": product.name,
        "category": product.category,
        "stock": product.stock,
        "lead_time_min_days": product.lead_time_min_days,
        "lead_time_max_days": product.lead_time_max_days,
        "volume_tiers": [tier.model_dump() for tier in product.volume_tiers],
    }


def _fallback(product_id: str) -> CounterOffer:
    product = db.get_product(product_id)
    if product is None:
        raise ValueError(f"unknown product_id: {product_id}")
    tier = min(product.volume_tiers, key=lambda item: item.min_qty)
    return CounterOffer(
        unit_price=round(tier.floor_price * 0.9, 2),
        min_volume=tier.min_qty,
        payment_terms_days=30,
        delivery_days=product.lead_time_max_days,
        recurring=False,
    )


def _messages(
    *,
    product_id: str,
    turn_count: int,
    history: list[dict[str, Any]] | None,
    budget_cap: float,
) -> list[dict[str, str]]:
    payload = {
        "product": _product_context(product_id),
        "turn_count": turn_count,
        "remaining_budget": budget_cap,
        "prior_turns": history or [],
        "buyer_tactics": [
            "probe volume tiers for the cheapest floor",
            "repeat a lowball if the merchant resists",
            "mention that procurement may walk away if value does not improve",
        ],
    }
    return [{"role": "user", "content": json.dumps(payload, indent=2)}]


def propose_buyer_offer(
    *,
    negotiation_id: str,
    product_id: str,
    turn_count: int,
    history: list[dict[str, Any]] | None,
    budget_cap: float,
    llm: LLMClient | None = None,
    persona: str = "adversarial cost minimizer",
) -> CounterOffer:
    """Generate only the buyer's next structured offer; the server decides legality."""
    del negotiation_id  # retained in the API for traceability and future prompts
    messages = _messages(
        product_id=product_id,
        turn_count=turn_count,
        history=history,
        budget_cap=budget_cap,
    )
    client = llm or LLMClient()
    error: str | None = None
    for attempt in range(2):
        try:
            prompt = _SYSTEM_PROMPT.format(budget_cap=budget_cap, persona=persona)
            result = client.chat_with_tools(prompt, messages if not error else [
                *messages,
                {"role": "user", "content": f"Previous call rejected: {error}. Retry with the counter_offer tool."},
            ], counter_offer_tools())
            if result is None or result.get("name") != "counter_offer":
                raise ValueError("model returned no counter_offer tool call")
            return CounterOffer.model_validate(result["arguments"])
        except (ValidationError, ValueError, KeyError, TypeError) as exc:
            error = str(exc)
            if attempt == 1:
                return _fallback(product_id)
    return _fallback(product_id)
