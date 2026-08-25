import json, urllib.request, urllib.error, sys, time

BASE = "http://127.0.0.1:8000"

def req(method, path, headers=None, data=None):
    url = BASE + path
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(r, timeout=90) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

# 1) UI html
st, html = req("GET", "/")
print(f"[GET /] http={st} bytes={len(html)} title={html[html.find('<title>')+7:html.find('</title>')] if '<title>' in html else '?'}")

# 2) session
st, body = req("POST", "/ui/session")
print(f"[POST /ui/session] http={st}")
sess = json.loads(body)
KEY, NEG = sess["buyer_key"], sess["negotiation_id"]
print(f"  buyer_id={sess['buyer_id']} neg={NEG[:14]}...")

H = {"Content-Type": "application/json", "X-Buyer-Key": KEY}

# 3) drive reasonable offers until CLOSED_WON (merchant = live LLM)
for p in [10.0, 10.8, 11.2, 11.5]:
    offer = {"unit_price": p, "min_volume": 1000, "payment_terms_days": 0, "delivery_days": 21, "recurring": False}
    st, body = req("POST", "/negotiate", headers=H, data={"negotiation_id": NEG, "buyer_offer": offer})
    if st >= 400:
        print(f"[negotiate p={p}] http={st} BODY={body[:500]}")
        break
    rj = json.loads(body)
    print(f"[negotiate p={p}] http={st} status={rj.get('status')} move={rj.get('merchant_move',{}).get('action')}")
    if rj.get("status") == "CLOSED_WON":
        break
    time.sleep(1)

# 4) audit
st, body = req("GET", f"/audit/{NEG}", headers=H)
print(f"[GET /audit] http={st}")
aud = json.loads(body)
print("  audit text (head):")
print("\n".join(aud["text"].splitlines()[:18]))

# 5) find order + invoice
pay = next((e for e in aud["trail"] if e.get("actor") == "payments" and e.get("detail", {}).get("razorpay_order_id")), None)
if pay:
    oid = pay["detail"]["razorpay_order_id"]
    print(f"  RAZORPAY ORDER: {oid}")
    st, pdf = req("GET", f"/invoices/{oid}", headers=H)
    print(f"[GET /invoices/{oid}] http={st} bytes={len(pdf)} (first bytes: {pdf[:8]!r})")
else:
    print("  no payments row found")
