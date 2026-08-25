"""Phase 4 demo stub: hardcoded (non-LLM) buyer over HTTP.

Drives /quote + /negotiate once: buyer lowballs → merchant LLM proposes an
illegal package (stubbed) → policy FAIL → best_legal_counter fallback.
Prints the audit trail (proposal → FAIL → fallback).

Usage (from backend/):
    python -m demo.run_demo
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

# Isolate demo DB before app imports bind the path.
_DEMO_DB = Path(tempfile.gettempdir()) / "catalogagent_phase4_demo.db"
os.environ["CATALOGAGENT_DB_PATH"] = str(_DEMO_DB)
if _DEMO_DB.exists():
    _DEMO_DB.unlink()

from app.create_buyer import create_buyer  # noqa: E402
from app.db import format_audit_trail, get_audit_trail, get_product, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.policy import PolicySession, check  # noqa: E402
from app.schemas import CounterOffer  # noqa: E402
from app.seed import seed_products  # noqa: E402


class _StubMerchantLLM:
    """Returns a below-floor counter so Gate 3 FAILs and fallback fires."""

    def chat_with_tools(self, system_prompt, messages, tools):
        product = get_product("elec-conn-001")
        assert product is not None
        tier = product.volume_tiers[0]
        lowball = CounterOffer(
            unit_price=round(tier.floor_price * 0.75, 2),
            min_volume=tier.min_qty,
            payment_terms_days=0,
            delivery_days=product.lead_time_max_days,
            recurring=False,
        )
        return {"name": "counter_offer", "arguments": lowball.model_dump()}


def main() -> int:
    init_db()
    seed_products()
    key = create_buyer("demo_buyer", 50000.0)
    headers = {"X-Buyer-Key": key}

    import app.agents.merchant as merchant_module

    merchant_module.LLMClient = _StubMerchantLLM  # type: ignore[misc, assignment]

    with TestClient(app) as client:
        quote = client.post(
            "/quote",
            json={
                "product_id": "elec-conn-001",
                "buyer_id": "demo_buyer",
                "initial_volume": 1000,
            },
            headers=headers,
        )
        quote.raise_for_status()
        negotiation_id = quote.json()["negotiation_id"]

        product = get_product("elec-conn-001")
        assert product is not None
        floor = product.volume_tiers[0].floor_price
        buyer_lowball = {
            "unit_price": round(floor * 0.75, 2),
            "min_volume": 1000,
            "payment_terms_days": 30,
            "delivery_days": 14,
            "recurring": False,
        }

        nego = client.post(
            "/negotiate",
            json={"negotiation_id": negotiation_id, "buyer_offer": buyer_lowball},
            headers=headers,
        )
        nego.raise_for_status()
        body = nego.json()

    print("=== Phase 4 demo: lowball -> FAIL -> legal fallback ===")
    print(f"negotiation_id: {negotiation_id}")
    print(f"status:         {body['status']}")
    print(f"merchant_move:  {body['merchant_move']}")
    print()
    print(format_audit_trail(negotiation_id))
    print()

    move = body["merchant_move"]
    assert move["action"] == "counter_offer"
    assert move["offer"] is not None
    fallback = CounterOffer.model_validate(move["offer"])
    assert check(fallback, PolicySession("elec-conn-001", 0)).passed

    trail = get_audit_trail(negotiation_id)
    actors_actions = [(e["actor"], e["action"], e["verdict"]) for e in trail]
    assert ("buyer_agent", "counter_offer", None) in actors_actions
    assert ("merchant_llm", "counter_offer", None) in actors_actions
    assert any(e["actor"] == "policy_engine" and e["verdict"] == "FAIL" for e in trail)
    # fallback proposal after FAIL
    merchant_proposals = [e for e in trail if e["actor"] == "merchant_llm" and e["action"] == "counter_offer"]
    assert len(merchant_proposals) >= 2, "expected illegal proposal + fallback proposal"

    print("OK - proposal -> FAIL -> fallback verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
