# CatalogAgent — Frontend (Next.js + TypeScript)

A clean, legible UI for the CatalogAgent bounded-autonomy negotiation demo.
Built with Next.js 14 (App Router) + TypeScript, styled in a restrained
Scandinavian system (black/white foundation, sans-serif, generous spacing).

## What it does

- **Auto mode** — pick a buyer persona (reasonable / aggressive / creative) and
  watch the negotiation play out. The **live contract** (merchant's current
  move) is displayed below the controls and updates on every counter-offer.
- **Manual mode** — choose a catalog item from the dropdown (fetched live from
  the backend DB); its base unit price and volume-tier pricing are shown. You
  type an offer and send it; the merchant (real LLM) responds.
- **Audit trail** — every turn is clearly demarcated ("Turn 1", "Turn 2", …),
  each entry color-coded by actor (buyer / merchant / policy engine / payments)
  with green **PASS** / red **FAIL** guardrail badges. Offers render as readable
  sentences, never raw JSON.
- **Loading state** — the Send / Start button shows a spinner and disables
  while a request is in flight, so you never wonder if it's working.
- On a closed deal, a Razorpay order id + downloadable invoice PDF appear.

## Run it

### 1. Start the backend (FastAPI on :8000)
From `backend/`:
```
cd ..\backend
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```
Leave it running. It auto-seeds the catalog and exposes `/ui/session`,
`/negotiate`, `/catalog`, `/audit/{id}`, `/invoices/{id}`.

### 2. Start the frontend (Next.js on :3000)
From `frontend/`:
```
npm install
npm run dev
```
Open http://localhost:3000

The Next dev server proxies `/api/*` → `http://localhost:8000` (see
`next.config.mjs`), so the browser talks same-origin — no CORS, and the
`X-Buyer-Key` header is allowed. `experimental.proxyTimeout` is set to 120s so
slow LLM negotiate turns are not cut off by Next’s default 30s rewrite limit.
To point at a different backend: `BACKEND_URL=http://host:port npm run dev`.

## Project layout

```
frontend/
  app/
    layout.tsx        # root layout + metadata
    globals.css       # Scandinavian design tokens + component styles
    page.tsx          # the single client component that orchestrates everything
  components/
    Spinner.tsx       # loading indicator
    LiveContract.tsx  # live contract panel + order/invoice box
    AuditTrail.tsx    # turn-grouped, color-coded audit log
  lib/
    types.ts          # types mirroring the backend contract
    api.ts            # typed fetch client + auto-buyer persona logic
    format.ts         # offer -> readable sentence, paise -> ₹
  next.config.mjs     # /api proxy to the backend
```

## Notes

- The **buyer** side in auto mode is a small deterministic TypeScript stand-in
  (`autoBuyerOffer` in `lib/api.ts`) that mimics the three personas, so the demo
  runs latency-free without a second live LLM call. The **merchant** side is the
  real LLM + deterministic guardrail (the interesting part). Manual mode sends
  your exact offer through the same real path.
- All source lives under `frontend/`. The only backend change required is the
  `order_id` field added to the `order_created` audit row (so the invoice
  download link resolves to the correct internal order id).
