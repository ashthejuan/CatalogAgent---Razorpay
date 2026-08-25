"""Invoice PDF generation from persisted order data."""

import json
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app import db

_INVOICE_DIR = Path(__file__).resolve().parent.parent / "invoices"


def save_invoice(order_row: dict) -> str:
    order = db.get_order(order_row["id"])
    if order is None:
        raise ValueError("order not found")
    terms = json.loads(order["terms"])
    negotiation = db.get_negotiation(order["negotiation_id"])
    if negotiation is None:
        raise ValueError("negotiation not found")

    _INVOICE_DIR.mkdir(exist_ok=True)
    path = _INVOICE_DIR / f"{order['id']}.pdf"
    lines = [
        f"Invoice: {order['id']}",
        f"Product ID: {terms['product_id']}",
        f"Buyer: {negotiation.buyer_id}",
        "Merchant: merchant",
        f"Razorpay Order ID: {order['razorpay_order_id']}",
        f"Unit Price: {terms['unit_price']}",
        f"Minimum Volume: {terms['min_volume']}",
        f"Payment Terms (days): {terms['payment_terms_days']}",
        f"Delivery (days): {terms['delivery_days']}",
        f"Recurring: {terms['recurring']}",
    ]
    pdf = canvas.Canvas(str(path), pagesize=A4)
    for y, line in zip(range(800, 800 - 20 * len(lines), -20), lines):
        pdf.drawString(50, y, line)
    pdf.save()
    return str(path)


def get_invoice_bytes(order_id: str) -> bytes:
    order = db.get_order(order_id)
    if order is None or not order.get("invoice_path"):
        raise FileNotFoundError(order_id)
    return Path(order["invoice_path"]).read_bytes()
