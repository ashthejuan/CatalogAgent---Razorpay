# Security model (Phase 1):
#   GET /catalog is PUBLIC by design — any AI agent may discover the merchant catalog.
#   Public response strips internal guardrails (floor_price); full Product stays server-side
#   for the policy engine. Write endpoints are buyer-key protected in Phase 4.

import app.config  # noqa: F401 — load .env at startup

from contextlib import asynccontextmanager
import json
import importlib
import secrets
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, Response

from app.agents.merchant import run_turn
from app.auth import require_buyer
from app.create_buyer import create_buyer
from app import invoicing
from app.db import (
    Buyer,
    Negotiation,
    append_audit,
    audit_excerpt,
    create_negotiation,
    format_audit_trail,
    get_audit_trail,
    get_negotiation,
    get_order,
    get_product,
    get_public_catalog,
    init_db,
    insert_order,
    update_order_invoice,
    save_negotiation,
)
from app.seed import ensure_seeded
from app.policy import PolicySession, check
from app.schemas import (
    CatalogProduct,
    CounterOffer,
    MerchantMoveOut,
    NegotiateBody,
    NegotiateResponse,
    OrderTerms,
    QuoteBody,
    QuoteResponse,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="CatalogAgent", lifespan=lifespan)


def _require_negotiation_owner(negotiation_id: str, buyer: Buyer) -> Negotiation:
    negotiation = get_negotiation(negotiation_id)
    if negotiation is None:
        raise HTTPException(status_code=404, detail="negotiation not found")
    if negotiation.buyer_id != buyer.buyer_id:
        raise HTTPException(status_code=403, detail="forbidden")
    return negotiation


def _merchant_move_out(result) -> MerchantMoveOut:
    return MerchantMoveOut(
        action=result.action,
        offer=result.offer,
        reason=result.reason,
    )


@app.get("/health")
def health():
    return {"status": "ok", "service": "catalogagent"}


@app.on_event("startup")
def _seed_on_startup():
    ensure_seeded()


@app.get("/")
def ui_root():
    """Serve the single-file demo UI."""
    index_path = Path(__file__).resolve().parent.parent / "static" / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="UI not found")
    return FileResponse(index_path)


@app.post("/ui/session")
def ui_session(product_id: str | None = None):
    """Auto-provision a demo buyer key + negotiation so the UI needs zero setup.

    Pass ?product_id= to open the negotiation on a specific catalog item. The UI
    opens a fresh negotiation per selected product so the policy engine grades
    against *that* item's own volume-tier floors / stock / lead times, and the
    merchant LLM counter-offers within its bounds. Defaults to the demo product.
    """
    pid = product_id or "elec-conn-001"
    if get_product(pid) is None:
        raise HTTPException(status_code=404, detail="product not found")
    buyer_name = f"demo_{secrets.token_hex(4)}"
    key = create_buyer(buyer_name, budget_cap=1_000_000.0)
    negotiation_id = create_negotiation(
        buyer_id=buyer_name,
        product_id=pid,
        initial_volume=1000,
    )
    append_audit(
        negotiation_id, 0, "system", "negotiation_opened",
        {"buyer_id": buyer_name, "product_id": pid, "initial_volume": 1000},
    )
    return {
        "buyer_key": key,
        "buyer_id": buyer_name,
        "negotiation_id": negotiation_id,
        "product_id": pid,
    }


@app.get("/catalog", response_model=list[CatalogProduct])
def catalog():
    return get_public_catalog()


@app.post("/quote", response_model=QuoteResponse)
def quote(body: QuoteBody, buyer: Annotated[Buyer, Depends(require_buyer)]):
    if body.buyer_id != buyer.buyer_id:
        raise HTTPException(status_code=403, detail="forbidden")
    if get_product(body.product_id) is None:
        raise HTTPException(status_code=404, detail="product not found")

    negotiation_id = create_negotiation(
        buyer_id=body.buyer_id,
        product_id=body.product_id,
        initial_volume=body.initial_volume,
    )
    append_audit(
        negotiation_id,
        0,
        "system",
        "negotiation_opened",
        {
            "buyer_id": body.buyer_id,
            "product_id": body.product_id,
            "initial_volume": body.initial_volume,
        },
    )
    return QuoteResponse(negotiation_id=negotiation_id)


@app.post("/negotiate", response_model=NegotiateResponse)
def negotiate(body: NegotiateBody, buyer: Annotated[Buyer, Depends(require_buyer)]):
    negotiation = _require_negotiation_owner(body.negotiation_id, buyer)
    if negotiation.status != "OPEN":
        raise HTTPException(status_code=409, detail=f"negotiation is {negotiation.status}")

    # PRD §6.8: buyer half on odd turns, merchant half on the following even turn.
    # turn_count = completed buyer-offer cycles (policy / max_turns); audit turns = 2n+1 / 2n+2.
    buyer_turn = negotiation.turn_count * 2 + 1
    merchant_turn = buyer_turn + 1
    append_audit(
        negotiation.id,
        buyer_turn,
        "buyer_agent",
        "counter_offer",
        body.buyer_offer.model_dump(),
    )
    negotiation.history.append(
        {
            "turn": buyer_turn,
            "actor": "buyer_agent",
            "offer": body.buyer_offer.model_dump(),
        }
    )

    session = PolicySession(negotiation.product_id, negotiation.turn_count)
    if check(body.buyer_offer, session).passed:
        negotiation.last_valid_buyer_offer = body.buyer_offer

    result = run_turn(negotiation, body.buyer_offer)
    move = _merchant_move_out(result)

    final_terms: CounterOffer | None = None
    order_id: str | None = None
    if result.action == "accept":
        negotiation.status = "CLOSED_WON"
        final_terms = result.offer
        # Legal buyer accept is audited on the buyer turn; max-turns accept on merchant turn.
        accept_turn = (
            buyer_turn
            if result.verdict is not None and result.verdict.passed
            else merchant_turn
        )
        try:
            payments = importlib.import_module("app.payments")
            order_terms = OrderTerms(
                **final_terms.model_dump(),
                product_id=negotiation.product_id,
                negotiation_id=negotiation.id,
            )
            razorpay_order = payments.create_order(order_terms)
            order_row = {
                "id": f"order_{secrets.token_hex(8)}",
                "negotiation_id": negotiation.id,
                "terms": json.dumps(order_terms.model_dump()),
                "razorpay_order_id": razorpay_order["id"],
                "invoice_path": None,
            }
            insert_order(order_row)
            append_audit(
                negotiation.id, accept_turn, "payments", "order_created",
                {"razorpay_order_id": razorpay_order["id"], "order_id": order_row["id"]},
            )
            invoice_path = invoicing.save_invoice(order_row)
            update_order_invoice(order_row["id"], invoice_path)
            append_audit(
                negotiation.id, accept_turn, "payments", "invoice_generated",
                {"invoice_path": invoice_path},
            )
            order_id = order_row["id"]
        except Exception as e:
            append_audit(
                negotiation.id, accept_turn, "payments", "order_failed", {"error": str(e)}
            )
    elif result.action == "escalate":
        negotiation.status = "ESCALATED"
    elif result.offer is not None:
        negotiation.history.append(
            {
                "turn": merchant_turn,
                "actor": "merchant_llm",
                "action": result.action,
                "offer": result.offer.model_dump(),
                "reason": result.reason,
            }
        )

    negotiation.turn_count += 1
    save_negotiation(negotiation)

    return NegotiateResponse(
        status=negotiation.status,
        merchant_move=move,
        audit_excerpt=audit_excerpt(negotiation.id),
        final_terms=final_terms,
        order_id=order_id,
    )


@app.get("/audit/{negotiation_id}")
def audit(negotiation_id: str, buyer: Annotated[Buyer, Depends(require_buyer)]):
    """Read-only audit trail — buyer key + negotiation ownership required."""
    _require_negotiation_owner(negotiation_id, buyer)
    return {
        "negotiation_id": negotiation_id,
        "trail": get_audit_trail(negotiation_id),
        "text": format_audit_trail(negotiation_id),
    }


@app.get("/invoices/{order_id}")
def invoice(order_id: str, buyer: Annotated[Buyer, Depends(require_buyer)]):
    order = get_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="invoice not found")
    _require_negotiation_owner(order["negotiation_id"], buyer)
    try:
        content = invoicing.get_invoice_bytes(order_id)
    except (FileNotFoundError, OSError):
        raise HTTPException(status_code=404, detail="invoice not found")
    return Response(content, media_type="application/pdf", headers={
        "Content-Disposition": f'inline; filename="{order_id}.pdf"'
    })
