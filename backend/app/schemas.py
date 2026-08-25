"""Pydantic models: CounterOffer, QuoteRequest, OrderTerms, Verdict."""

from pydantic import BaseModel, ConfigDict


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


class CounterOffer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_price: float
    min_volume: int
    payment_terms_days: int
    delivery_days: int
    recurring: bool


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
