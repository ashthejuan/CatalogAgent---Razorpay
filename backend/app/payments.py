"""Razorpay integration, isolated from all agent modules."""

import os

import httpx

import app.config  # noqa: F401
from app.schemas import OrderTerms


def _post_order(payload: dict) -> dict:
    response = httpx.post(
        "https://api.razorpay.com/v1/orders",
        auth=(os.environ.get("RAZORPAY_KEY_ID", ""), os.environ.get("RAZORPAY_KEY_SECRET", "")),
        json=payload,
        timeout=10.0,
    )
    if response.is_error:
        raise RuntimeError(f"Razorpay order failed ({response.status_code}): {response.text}")
    return response.json()


def create_order(terms: OrderTerms) -> dict:
    amount_paise = round(terms.unit_price * 100) * terms.min_volume
    return _post_order(
        {
            "amount": amount_paise,
            "currency": "INR",
            "receipt": terms.negotiation_id,
            "notes": terms.model_dump(),
        }
    )
