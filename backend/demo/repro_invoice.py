import os, sys, json
os.environ["CATALOGAGENT_DB_PATH"] = "C:/Users/ashth/OneDrive/Documents/Coding Work/razorpay/backend/repro_inv.db"
import app.config
from app.db import init_db
from app.seed import ensure_seeded
init_db(); ensure_seeded()
from app.main import app
from fastapi.testclient import TestClient

c = TestClient(app)
s = c.post("/ui/session?product_id=elec-conn-001")
print("session status", s.status_code, s.json())
key = s.json()["buyer_key"]
neg = s.json()["negotiation_id"]

# Drive to a close: send the known-good comfortable offer (elec-conn-001 tier floor).
offer = {"unit_price": 11.5, "min_volume": 1000, "payment_terms_days": 0, "delivery_days": 21, "recurring": False}
for i in range(3):
    r = c.post("/negotiate", headers={"X-Buyer-Key": key}, json={"negotiation_id": neg, "buyer_offer": offer})
    print(f"negotiate {i} -> {r.status_code} {r.json().get('status')} order_id={r.json().get('order_id')}")
    if r.json().get("status") != "OPEN":
        break

# Find the order id from the audit trail (same way the frontend does).
a = c.get(f"/audit/{neg}", headers={"X-Buyer-Key": key}).json()
print("audit trail actors:", [e["actor"] for e in a["trail"]])
pay = next((e for e in a["trail"] if e["actor"] == "payments" and e.get("payload", {}).get("razorpay_order_id")), None)
print("payments row payload:", pay["payload"] if pay else None)
internal_id = pay["payload"]["order_id"]

# Now download with the SAME key (simulating the frontend's normal path).
inv = c.get(f"/invoices/{internal_id}", headers={"X-Buyer-Key": key})
print("INVOICE (same key) ->", inv.status_code, inv.headers.get("content-type"))

# Now download with a DIFFERENT session's key (simulating stale/cross-session click).
s2 = c.post("/ui/session?product_id=elec-mcu-003").json()
inv2 = c.get(f"/invoices/{internal_id}", headers={"X-Buyer-Key": s2["buyer_key"]})
print("INVOICE (other key) ->", inv2.status_code)
