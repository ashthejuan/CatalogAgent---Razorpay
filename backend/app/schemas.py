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
