"""Merchant negotiation agent — one turn per call; LLM proposes, policy decides."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from app import db
from app.llm_client import LLMClient, counter_offer_tools
from app.policy import (
    MAX_PAYMENT_TERMS_DAYS,
    PolicySession,
    best_legal_counter,
    check,
)
from app.schemas import CounterOffer, Verdict

MoveAction = Literal["counter_offer", "accept", "escalate"]

_SYSTEM_PROMPT = """You are the merchant-side B2B negotiation agent.
Propose exactly ONE next move by calling the counter_offer tool with all five fields.
Never reply with plain text — always use the tool.

Verified policy semantics (your proposal will be checked mechanically):
- unit_price has a HARD floor per volume tier (tier chosen from min_volume).
- recurring=True earns a margin concession — it can flip a tight package to PASS.
  Prefer recurring commitment before dropping price when the buyer is close.
- payment_terms_days erodes margin (net-45 is expensive); max is {max_terms} days.
- delivery_days faster than the product's standard max lead time costs margin (rush).
- min_volume below the lowest tier MOQ is auto-rejected (structural fail).
- min_volume above stock is auto-rejected.

You do not accept deals or escalate — only propose counter_offers. The server handles the rest.
"""


@dataclass
class MerchantTurnResult:
    action: MoveAction
    offer: CounterOffer | None = None
    reason: str | None = None
    verdict: Verdict | None = None


def _is_structural_fail(reason: str) -> bool:
    """Hard bounds that warrant human escalation instead of a graceful counter."""
    needles = ("below minimum tier MOQ", "> stock", ">= max_turns")
    return any(n in reason for n in needles)


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
        "volume_tiers": [t.model_dump() for t in product.volume_tiers],
    }


def _build_messages(
    *,
    buyer_offer: CounterOffer,
    turn_count: int,
    max_turns: int,
    history: list[dict[str, Any]] | None,
) -> list[dict[str, str]]:
    remaining = max(0, max_turns - turn_count)
    payload = {
        "buyer_last_offer": buyer_offer.model_dump(),
        "turn_count": turn_count,
        "remaining_turns": remaining,
        "prior_turns": history or [],
    }
    return [{"role": "user", "content": json.dumps(payload, indent=2)}]


def _system_prompt() -> str:
    return _SYSTEM_PROMPT.format(max_terms=MAX_PAYMENT_TERMS_DAYS)


def _audit_malformed(negotiation_id: str, turn: int, reason: str) -> None:
    db.append_audit(
        negotiation_id,
        turn,
        "merchant_llm",
        "malformed_proposal",
        {"error": reason},
        verdict="FAIL",
        reason=reason,
    )


def _audit_proposal(negotiation_id: str, turn: int, offer: CounterOffer) -> None:
    db.append_audit(
        negotiation_id,
        turn,
        "merchant_llm",
        "counter_offer",
        offer.model_dump(),
    )


def _audit_verdict(negotiation_id: str, turn: int, offer: CounterOffer, verdict: Verdict) -> None:
    tag = "PASS" if verdict.passed else "FAIL"
    db.append_audit(
        negotiation_id,
        turn,
        "policy_engine",
        "guardrail_check",
        offer.model_dump(),
        verdict=tag,
        reason=verdict.reason,
    )


def _audit_escalate(negotiation_id: str, turn: int, reason: str) -> None:
    db.append_audit(
        negotiation_id,
        turn,
        "merchant_llm",
        "escalate_to_human",
        {"reason": reason},
    )


def _audit_accept(negotiation_id: str, turn: int, offer: CounterOffer, reason: str | None = None) -> None:
    db.append_audit(
        negotiation_id,
        turn,
        "merchant_llm",
        "accept",
        offer.model_dump(),
        verdict="PASS",
        reason=reason or "all bounds satisfied; accepting buyer offer",
    )


def _validate_tool_call(tool_call: dict[str, Any] | None) -> CounterOffer:
    if tool_call is None:
        raise ValueError("model returned text instead of counter_offer tool call")
    if tool_call.get("name") != "counter_offer":
        raise ValueError(f"expected counter_offer tool, got {tool_call.get('name')!r}")
    try:
        return CounterOffer.model_validate(tool_call["arguments"])
    except ValidationError:
        raise


def _llm_proposal(
    llm: LLMClient,
    product_id: str,
    buyer_offer: CounterOffer,
    turn_count: int,
    max_turns: int,
    history: list[dict[str, Any]] | None,
    reprompt_error: str | None = None,
) -> CounterOffer:
    messages = _build_messages(
        buyer_offer=buyer_offer,
        turn_count=turn_count,
        max_turns=max_turns,
        history=history,
    )
    if reprompt_error:
        messages.append(
            {
                "role": "user",
                "content": (
                    "Your previous proposal was malformed and rejected by schema validation:\n"
                    f"{reprompt_error}\n"
                    "Call counter_offer again with all five fields and correct types."
                ),
            }
        )

    context_note = {
        "role": "user",
        "content": json.dumps({"product": _product_context(product_id)}, indent=2),
    }
    messages.insert(0, context_note)

    tool_call = llm.chat_with_tools(_system_prompt(), messages, counter_offer_tools())
    return _validate_tool_call(tool_call)


def _finalize_proposal(
    *,
    negotiation_id: str,
    turn: int,
    session: PolicySession,
    offer: CounterOffer,
) -> MerchantTurnResult:
    """LLM proposes; code disposes. Legal counters are presented without a PASS row.

    Soft-illegal LLM proposals are discarded quietly (buyer FAIL already audited)
    and replaced with one ``merchant_llm`` fallback — no extra FAIL row on the
    merchant turn. Structural fails still audit FAIL + escalate.
    """
    verdict = check(offer, session)

    if verdict.passed:
        _audit_proposal(negotiation_id, turn, offer)
        return MerchantTurnResult(action="counter_offer", offer=offer, verdict=verdict)

    if _is_structural_fail(verdict.reason):
        _audit_verdict(negotiation_id, turn, offer, verdict)
        _audit_escalate(negotiation_id, turn, verdict.reason)
        return MerchantTurnResult(action="escalate", reason=verdict.reason, verdict=verdict)

    fallback = best_legal_counter(session)
    _audit_proposal(negotiation_id, turn, fallback)
    return MerchantTurnResult(
        action="counter_offer",
        offer=fallback,
        verdict=verdict,
    )


def run_merchant_turn(
    *,
    negotiation_id: str,
    product_id: str,
    turn: int,
    turn_count: int,
    buyer_offer: CounterOffer,
    history: list[dict[str, Any]] | None = None,
    last_valid_buyer_offer: CounterOffer | None = None,
    max_turns: int = 4,
    llm: LLMClient | None = None,
    merchant_turn: int | None = None,
) -> MerchantTurnResult:
    """Run one merchant response after a buyer offer.

    Audit turns alternate per PRD §6.8: buyer half on ``turn`` (odd), merchant
    half on ``merchant_turn`` (even). ``turn_count`` is still the buyer-offer
    cycle index used by PolicySession / max_turns.
    """
    buyer_turn = turn
    m_turn = merchant_turn if merchant_turn is not None else turn + 1
    session = PolicySession(product_id, turn_count, max_turns=max_turns)

    if turn_count >= max_turns:
        if last_valid_buyer_offer is not None:
            _audit_accept(negotiation_id, m_turn, last_valid_buyer_offer)
            return MerchantTurnResult(action="accept", offer=last_valid_buyer_offer)
        reason = f"turn_count {turn_count} >= max_turns {max_turns}; no valid buyer offer to accept"
        _audit_escalate(negotiation_id, m_turn, reason)
        return MerchantTurnResult(action="escalate", reason=reason)

    # Buyer already proposed a legal package — accept without another LLM proposal.
    buyer_verdict = check(buyer_offer, session)
    if buyer_verdict.passed:
        _audit_accept(
            negotiation_id,
            buyer_turn,
            buyer_offer,
            reason=buyer_verdict.reason,
        )
        return MerchantTurnResult(action="accept", offer=buyer_offer, verdict=buyer_verdict)

    # Buyer reject on the buyer turn; merchant counter starts the next turn.
    _audit_verdict(negotiation_id, buyer_turn, buyer_offer, buyer_verdict)

    client = llm or LLMClient()
    last_error: str | None = None

    for attempt in range(2):
        try:
            offer = _llm_proposal(
                client,
                product_id,
                buyer_offer,
                turn_count,
                max_turns,
                history,
                reprompt_error=last_error,
            )
        except (ValidationError, ValueError) as exc:
            last_error = str(exc)
            _audit_malformed(negotiation_id, m_turn, last_error)
            if attempt == 0:
                continue
            _audit_escalate(negotiation_id, m_turn, f"malformed_proposal after retry: {last_error}")
            return MerchantTurnResult(action="escalate", reason=last_error)
        else:
            return _finalize_proposal(
                negotiation_id=negotiation_id,
                turn=m_turn,
                session=session,
                offer=offer,
            )

    raise RuntimeError("merchant turn retry loop exhausted")


def run_turn(
    negotiation: db.Negotiation,
    buyer_offer: CounterOffer,
    llm: LLMClient | None = None,
) -> MerchantTurnResult:
    """One merchant response using persisted negotiation state.

    Buyer audit turn = ``2 * turn_count + 1``; merchant = buyer + 1.
    """
    buyer_turn = negotiation.turn_count * 2 + 1
    return run_merchant_turn(
        negotiation_id=negotiation.id,
        product_id=negotiation.product_id,
        turn=buyer_turn,
        merchant_turn=buyer_turn + 1,
        turn_count=negotiation.turn_count,
        buyer_offer=buyer_offer,
        history=negotiation.history,
        last_valid_buyer_offer=negotiation.last_valid_buyer_offer,
        llm=llm,
    )
