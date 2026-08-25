"""Phase 5 multi-turn buyer/merchant negotiation scenarios."""

from __future__ import annotations

import os
import tempfile

if "CATALOGAGENT_DB_PATH" not in os.environ:
    _demo_db = os.path.join(tempfile.gettempdir(), f"catalogagent_phase5_demo_{os.getpid()}.db")
    if os.path.exists(_demo_db):
        os.unlink(_demo_db)
    os.environ["CATALOGAGENT_DB_PATH"] = _demo_db

from fastapi.testclient import TestClient  # noqa: E402

from app.agents.buyer import propose_buyer_offer  # noqa: E402
from app.create_buyer import create_buyer  # noqa: E402
from app.db import format_audit_trail, get_buyer_by_id, get_negotiation, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed_products  # noqa: E402

MAX_TURNS = 4
PRODUCT_ID = "elec-conn-001"


def _run_scenario(name: str, persona: str, budget_cap: float = 50_000.0) -> str:
    buyer_id = f"demo_{name}"
    key = create_buyer(buyer_id, budget_cap)
    headers = {"X-Buyer-Key": key}
    with TestClient(app) as client:
        quote = client.post(
            "/quote",
            json={"product_id": PRODUCT_ID, "buyer_id": buyer_id, "initial_volume": 1000},
            headers=headers,
        )
        quote.raise_for_status()
        negotiation_id = quote.json()["negotiation_id"]
        for _ in range(MAX_TURNS):
            negotiation = get_negotiation(negotiation_id)
            buyer = get_buyer_by_id(buyer_id)
            assert negotiation is not None and buyer is not None
            offer = propose_buyer_offer(
                negotiation_id=negotiation.id,
                product_id=negotiation.product_id,
                turn_count=negotiation.turn_count,
                history=negotiation.history,
                budget_cap=buyer.budget_cap,
                persona=persona,
            )
            response = client.post(
                "/negotiate",
                json={"negotiation_id": negotiation_id, "buyer_offer": offer.model_dump()},
                headers=headers,
            )
            response.raise_for_status()
            body = response.json()
            print(f"[{name}] merchant move: {body['merchant_move']}")
            print(body["audit_excerpt"])
            if body["status"] in {"CLOSED_WON", "ESCALATED"}:
                break

        final = get_negotiation(negotiation_id)
        assert final is not None
        print("\n" + format_audit_trail(negotiation_id) + "\n")
        return final.status


def reasonable_buyer() -> str:
    return _run_scenario(
        "reasonable",
        "Close immediately. Offer exactly unit_price 11.50, min_volume 1000, payment_terms_days 0, delivery_days 21, recurring false; this is a legal package.",
    )


def aggressive_lowballer() -> str:
    return _run_scenario(
        "aggressive",
        "Lowball hard every turn, repeat the pressure, and threaten to walk away; do not concede easily.",
    )


def creative_reroute() -> str:
    return _run_scenario(
        "creative",
        "Hold price as firmly as possible, but concede through volume, payment terms, delivery, or recurring commitment.",
    )


def main() -> int:
    init_db()
    seed_products()
    statuses = {
        "reasonable": reasonable_buyer(),
        "aggressive": aggressive_lowballer(),
        "creative": creative_reroute(),
    }
    assert statuses["reasonable"] == "CLOSED_WON"
    assert statuses["aggressive"] in {"ESCALATED", "OPEN"}
    assert statuses["creative"] in {"CLOSED_WON", "ESCALATED", "OPEN"}
    print(f"scenario statuses: {statuses}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
