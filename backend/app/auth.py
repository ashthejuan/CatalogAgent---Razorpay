"""Buyer API key authentication (Gate 1)."""

from typing import Annotated

from fastapi import Header, HTTPException

from app.create_buyer import hash_buyer_key
from app.db import Buyer, get_buyer_by_hash


def require_buyer(
    x_buyer_key: Annotated[str | None, Header(alias="X-Buyer-Key")] = None,
) -> Buyer:
    if not x_buyer_key:
        raise HTTPException(status_code=401, detail="missing X-Buyer-Key")
    key_hash = hash_buyer_key(x_buyer_key)
    buyer = get_buyer_by_hash(key_hash)
    if buyer is None:
        raise HTTPException(status_code=401, detail="invalid buyer key")
    return buyer
