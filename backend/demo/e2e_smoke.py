"""Live e2e smoke test: real NIM LLM through /quote + /negotiate over HTTP.

Run from backend/ with .env loaded. Creates a real acme buyer, opens a
negotiation, and drives up to a few turns until accept/escalate.
"""
from __future__ import annotations

import os
import sys

import app.config  # load .env
from app.create_buyer import create_buyer
from fastapi.testclient import TestClient

from app.db import init_db
from app.seed import seed_products
from app.main import app


def main() -> int:
    init_db()
    seed_products()
    import time

    buyer_name = f"e2e_{int(time.time())}"
    key = create_buyer(buyer_name, 50000.0)
    print(f"[setup] buyer {buyer_name} key: {key}")

    with TestClient(app) as client:
        headers = {"X-Buyer-Key": key}

        # Reasonable buyer opening: legal-ish but leaves room.
        r = client.post(
            "/quote",
            json={"product_id": "elec-conn-001", "buyer_id": buyer_name, "initial_volume": 5000},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        neg_id = r.json()["negotiation_id"]
        print(f"[quote] {neg_id}")

        # Buyer opens lowball to provoke the guardrail wall + fallback.
        buyer_offers = [
            {"unit_price": 9.5, "min_volume": 5000, "payment_terms_days": 30, "delivery_days": 14, "recurring": False},
            {"unit_price": 10.8, "min_volume": 5000, "payment_terms_days": 30, "delivery_days": 14, "recurring": True},
            {"unit_price": 11.5, "min_volume": 5000, "payment_terms_days": 0, "delivery_days": 21, "recurring": False},
        ]

        for i, offer in enumerate(buyer_offers):
            r = client.post(
                "/negotiate",
                json={"negotiation_id": neg_id, "buyer_offer": offer},
                headers=headers,
            )
            print(f"\n--- turn {i+1} ---")
            print(f"buyer offer: {offer}")
            if r.status_code != 200:
                print(f"HTTP {r.status_code}: {r.text}")
                return 1
            body = r.json()
            mv = body["merchant_move"]
            print(f"status: {body['status']} | merchant action: {mv['action']}")
            if mv["offer"]:
                print(f"merchant offer: {mv['offer']}")
            if mv["reason"]:
                print(f"reason: {mv['reason']}")
            print("audit excerpt:\n" + body["audit_excerpt"])
            if body["status"] in ("CLOSED_WON", "ESCALATED"):
                break

        # Pull full audit trail to inspect.
        r = client.get(f"/audit/{neg_id}", headers=headers)
        print("\n=== full audit trail ===")
        print(r.json()["text"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
