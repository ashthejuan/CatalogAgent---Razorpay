import os, sys, traceback
# isolate DB so we don't touch the user's running instance
os.environ["CATALOGAGENT_DB_PATH"] = "C:/Users/ashth/OneDrive/Documents/Coding Work/razorpay/backend/repro.db"
if os.path.exists("repro.db"):
    os.remove("repro.db")

from fastapi.testclient import TestClient
import app.config  # load .env
from app.db import init_db
from app.seed import ensure_seeded
init_db()
ensure_seeded()
from app.main import app

client = TestClient(app, raise_server_exceptions=True)

for pid in ["elec-conn-001", "elec-mcu-003", "ind-brkt-001", "pkg-box-001"]:
    print("\n===== product:", pid, "=====")
    try:
        s = client.post(f"/ui/session?product_id={pid}")
        print("session", s.status_code, s.json())
        neg = s.json()["negotiation_id"]
        # reasonable offer: at/above floor, sane terms
        offer = {"unit_price": 12.0, "min_volume": 1000, "payment_terms_days": 0,
                 "delivery_days": 21, "recurring": False}
        r = client.post("/negotiate",
                        headers={"X-Buyer-Key": s.json()["buyer_key"]},
                        json={"negotiation_id": neg, "buyer_offer": offer})
        print("negotiate", r.status_code, r.text[:500])
    except Exception as e:
        print("!!! EXCEPTION in", pid)
        traceback.print_exc()
