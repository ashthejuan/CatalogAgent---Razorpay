"""Pydantic models: CounterOffer, QuoteRequest, OrderTerms, Verdict."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


class VolumeTier(BaseModel):
    min_qty: int
    unit_price: float
    floor_price: float


class Product(BaseModel):
    id: str
    name: str
    category: str
    base_unit_price: float
    stock: int
    lead_time_min_days: int
    lead_time_max_days: int
    volume_tiers: list[VolumeTier]


class CatalogTier(BaseModel):
    min_qty: int
    unit_price: float


class CatalogProduct(BaseModel):
    id: str
    name: str
    category: str
    base_unit_price: float
    stock: int
    lead_time_min_days: int
    lead_time_max_days: int
    volume_tiers: list[CatalogTier]

    @classmethod
    def from_product(cls, product: Product) -> "CatalogProduct":
        return cls(
            id=product.id,
            name=product.name,
            category=product.category,
            base_unit_price=product.base_unit_price,
            stock=product.stock,
            lead_time_min_days=product.lead_time_min_days,
            lead_time_max_days=product.lead_time_max_days,
            volume_tiers=[
                CatalogTier(min_qty=t.min_qty, unit_price=t.unit_price)
                for t in product.volume_tiers
            ],
        )


class CounterOffer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_price: float
    min_volume: int
    payment_terms_days: int
    delivery_days: int
    recurring: bool

    @model_validator(mode="before")
    @classmethod
    def _shape_contract_values(cls, data):
        """Tighten LLM-supplied values into clean contract form.

        - ``unit_price`` is currency: round to 2dp so garbage precision
          (e.g. ``10.050761421319798``) never reaches the policy engine or
          a downstream Razorpay paise conversion.
        - integer fields are coerced from strings/decimals defensively, so
          the schema stays clean even if the transport-layer coercion (Option A)
          is bypassed.
        """
        if not isinstance(data, dict):
            return data
        up = data.get("unit_price")
        if isinstance(up, (int, float, str)):
            try:
                data["unit_price"] = round(float(up), 2)
            except (TypeError, ValueError):
                pass  # leave for field-level validation to reject
        for key in ("min_volume", "payment_terms_days", "delivery_days"):
            v = data.get(key)
            if isinstance(v, str):
                try:
                    data[key] = int(float(v))
                except (TypeError, ValueError):
                    pass
            elif isinstance(v, float):
                data[key] = int(v)
        return data


class QuoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str
    qty: int
    buyer_id: str


class OrderTerms(CounterOffer):
    product_id: str
    negotiation_id: str


class Verdict(BaseModel):
    passed: bool
    reason: str


class QuoteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str
    buyer_id: str
    initial_volume: int | None = None


class QuoteResponse(BaseModel):
    negotiation_id: str


class NegotiateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    negotiation_id: str
    buyer_offer: CounterOffer


class MerchantMoveOut(BaseModel):
    action: Literal["counter_offer", "accept", "escalate"]
    offer: CounterOffer | None = None
    reason: str | None = None


class NegotiateResponse(BaseModel):
    status: Literal["OPEN", "CLOSED_WON", "ESCALATED"]
    merchant_move: MerchantMoveOut
    audit_excerpt: str
    final_terms: CounterOffer | None = None
    order_id: str | None = None
