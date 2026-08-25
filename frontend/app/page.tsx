"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  AuditEntry,
  CatalogProduct,
  CounterOffer,
  MerchantMove,
  NegStatus,
  OrderInfo,
  UISession,
} from "@/lib/types";
import {
  autoBuyerOffer,
  createSession,
  getAudit,
  getCatalog,
  negotiate,
} from "@/lib/api";
import Spinner from "@/components/Spinner";
import LiveContract from "@/components/LiveContract";
import AuditTrail from "@/components/AuditTrail";

type Mode = "auto" | "manual";
type Persona = "reasonable" | "aggressive" | "creative";

const MAX_AUTO_STEPS = 8;

function statusPillClass(s: NegStatus): string {
  return `pill pill--${s.toLowerCase()}`;
}

export default function Home() {
  const [session, setSession] = useState<UISession | null>(null);
  const [catalog, setCatalog] = useState<CatalogProduct[]>([]);
  const [mode, setMode] = useState<Mode>("auto");
  const [persona, setPersona] = useState<Persona>("reasonable");

  const [selectedProductId, setSelectedProductId] = useState<string>("");
  const selectedProduct = useMemo(
    () => catalog.find((p) => p.id === selectedProductId) || null,
    [catalog, selectedProductId],
  );
  const [form, setForm] = useState<CounterOffer>({
    unit_price: 9.5,
    min_volume: 5000,
    payment_terms_days: 30,
    delivery_days: 14,
    recurring: false,
  });

  const [status, setStatus] = useState<NegStatus>("OPEN");
  const [liveMove, setLiveMove] = useState<MerchantMove | null>(null);
  const [finalTerms, setFinalTerms] = useState<CounterOffer | null>(null);
  const [order, setOrder] = useState<OrderInfo | null>(null);
  const [audit, setAudit] = useState<AuditEntry[]>([]);

  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const runningRef = useRef(false);
  const [error, setError] = useState<string | null>(null);

  const refreshAudit = useCallback(
    async (sess: UISession) => {
      try {
        const a = await getAudit(sess.buyer_key, sess.negotiation_id);
        setAudit(a.trail);
      } catch (e: any) {
        setError(e?.message || "audit refresh failed");
      }
    },
    [],
  );

  // Boot: catalog + initial session for the first product.
  useEffect(() => {
    (async () => {
      try {
        const c = await getCatalog();
        setCatalog(c);
        const firstId = c.length ? c[0].id : "";
        setSelectedProductId(firstId);
        const s = await createSession(firstId || undefined);
        setSession(s);
        await refreshAudit(s);
      } catch (e: any) {
        setError(
          "Failed to start session: " +
            (e?.message || "backend unreachable on :8000"),
        );
      }
    })();
  }, [refreshAudit]);

  // Switching the catalog item opens a fresh negotiation for THAT product, so
  // the policy engine + merchant LLM grade against its own tiers/stock/lead
  // times. The displayed contract resets to the item's listed price.
  const onSelectProduct = useCallback(
    async (productId: string) => {
      setSelectedProductId(productId);
      setLiveMove(null);
      setFinalTerms(null);
      setOrder(null);
      setAudit([]);
      setStatus("OPEN");
      try {
        const s = await createSession(productId || undefined);
        setSession(s);
        await refreshAudit(s);
      } catch (e: any) {
        setError(e?.message || "failed to open session for product");
      }
    },
    [refreshAudit],
  );

  useEffect(() => {
    if (selectedProduct) {
      setForm((f) => ({ ...f, unit_price: selectedProduct.base_unit_price }));
    }
  }, [selectedProduct]);

  const doNegotiate = useCallback(
    async (offer: CounterOffer) => {
      if (!session) return null;
      setLoading(true);
      setError(null);
      try {
        const res = await negotiate(
          session.buyer_key,
          session.negotiation_id,
          offer,
        );
        setStatus(res.status);
        setLiveMove(res.merchant_move);
        if (res.final_terms) setFinalTerms(res.final_terms);
        if (res.order_id) {
          const amount = res.final_terms
            ? Math.round(res.final_terms.unit_price * 100) * res.final_terms.min_volume
            : null;
          setOrder({
            order_id: res.order_id,
            razorpay_order_id: res.merchant_move.reason || "",
            amount_paise: amount,
          });
        }
        await refreshAudit(session);
        return res;
      } catch (e: any) {
        setError("Offer rejected: " + (e?.message || "unknown error"));
        return null;
      } finally {
        setLoading(false);
      }
    },
    [session, refreshAudit],
  );

  const runAuto = useCallback(async () => {
    if (!session || runningRef.current) return;
    runningRef.current = true;
    setRunning(true);
    setError(null);
    for (let step = 0; step < MAX_AUTO_STEPS; step++) {
      if (!runningRef.current) break;
      const offer = autoBuyerOffer(persona, step);
      const res = await doNegotiate(offer);
      if (!res || res.status !== "OPEN") break;
      await new Promise((r) => setTimeout(r, 800));
    }
    runningRef.current = false;
    setRunning(false);
  }, [session, persona, doNegotiate]);

  const stopAuto = useCallback(() => {
    runningRef.current = false;
    setRunning(false);
  }, []);

  const manualSubmit = useCallback(async () => {
    await doNegotiate(form);
  }, [doNegotiate, form]);

  return (
    <main className="container">
      <header className="header">
        <h1 className="title">CatalogAgent</h1>
        <p className="subtitle">
          Bounded autonomous B2B procurement negotiation — an LLM proposes, a
          deterministic guardrail disposes.
        </p>
        <div className="pills">
          <span className={statusPillClass(status)}>status: {status}</span>
          {session && (
            <span className="pill">product: {session.product_id}</span>
          )}
          {session && (
            <span className="pill">
              neg: {session.negotiation_id.slice(0, 12)}…
            </span>
          )}
          <span className="pill">mode: {mode}</span>
        </div>
      </header>

      {error && <div className="banner banner--error">{error}</div>}

      <div className="grid">
        {/* ----------------------------- BUYER ----------------------------- */}
        <section className="card">
          <h2 className="card__title">Buyer</h2>

          <div className="toggle">
            <button
              className={mode === "auto" ? "active" : ""}
              onClick={() => setMode("auto")}
            >
              Auto (agent)
            </button>
            <button
              className={mode === "manual" ? "active" : ""}
              onClick={() => setMode("manual")}
            >
              Manual (you)
            </button>
          </div>

          {mode === "auto" ? (
            <>
              <label className="field__label">Buyer persona</label>
              <select
                className="select"
                value={persona}
                onChange={(e) => setPersona(e.target.value as Persona)}
              >
                <option value="reasonable">
                  Reasonable — closes a fair deal
                </option>
                <option value="aggressive">
                  Aggressive lowballer — tests the guardrail wall
                </option>
                <option value="creative">
                  Holds price, concedes terms/volume
                </option>
              </select>

              {!running ? (
                <button
                  className="btn btn--primary"
                  style={{ marginTop: 16, width: "100%" }}
                  onClick={runAuto}
                  disabled={!session || loading}
                >
                  ▶ Start auto negotiation
                </button>
              ) : (
                <button
                  className="btn btn--ghost"
                  style={{ marginTop: 16, width: "100%" }}
                  onClick={stopAuto}
                >
                  ■ Stop
                </button>
              )}
            </>
          ) : (
            <>
              <label className="field__label">Catalog item</label>
              <select
                className="select"
                value={selectedProductId}
                onChange={(e) => onSelectProduct(e.target.value)}
              >
                {catalog.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} ({p.id})
                  </option>
                ))}
              </select>

              {selectedProduct && (
                <div className="pricecard">
                  <div>
                    <span className="muted">Base unit price&nbsp;</span>
                    <strong>₹{selectedProduct.base_unit_price.toFixed(2)}</strong>
                  </div>
                  <div className="muted small">
                    Stock {selectedProduct.stock.toLocaleString("en-IN")} · Lead{" "}
                    {selectedProduct.lead_time_min_days}–
                    {selectedProduct.lead_time_max_days} days
                  </div>
                  <div className="tiers">
                    {selectedProduct.volume_tiers.map((t) => (
                      <span key={t.min_qty} className="tier">
                        ≥{t.min_qty.toLocaleString("en-IN")}: ₹
                        {t.unit_price.toFixed(2)}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              <div className="formgrid">
                <div className="field">
                  <label className="field__label">Unit price (₹)</label>
                  <input
                    className="input"
                    type="number"
                    step="0.01"
                    value={form.unit_price}
                    onChange={(e) =>
                      setForm((f) => ({
                        ...f,
                        unit_price: parseFloat(e.target.value) || 0,
                      }))
                    }
                  />
                </div>
                <div className="field">
                  <label className="field__label">Volume (MOQ)</label>
                  <input
                    className="input"
                    type="number"
                    value={form.min_volume}
                    onChange={(e) =>
                      setForm((f) => ({
                        ...f,
                        min_volume: parseInt(e.target.value, 10) || 0,
                      }))
                    }
                  />
                </div>
                <div className="field">
                  <label className="field__label">Payment terms (days)</label>
                  <input
                    className="input"
                    type="number"
                    value={form.payment_terms_days}
                    onChange={(e) =>
                      setForm((f) => ({
                        ...f,
                        payment_terms_days: parseInt(e.target.value, 10) || 0,
                      }))
                    }
                  />
                </div>
                <div className="field">
                  <label className="field__label">Delivery (days)</label>
                  <input
                    className="input"
                    type="number"
                    value={form.delivery_days}
                    onChange={(e) =>
                      setForm((f) => ({
                        ...f,
                        delivery_days: parseInt(e.target.value, 10) || 0,
                      }))
                    }
                  />
                </div>
              </div>
              <div className="field">
                <label className="field__label">Recurring</label>
                <select
                  className="select"
                  value={form.recurring ? "true" : "false"}
                  onChange={(e) =>
                    setForm((f) => ({
                      ...f,
                      recurring: e.target.value === "true",
                    }))
                  }
                >
                  <option value="false">No</option>
                  <option value="true">Yes</option>
                </select>
              </div>

              <button
                className="btn btn--primary"
                style={{ marginTop: 16, width: "100%" }}
                onClick={manualSubmit}
                disabled={!session || loading}
              >
                {loading ? (
                  <>
                    <Spinner /> Sending…
                  </>
                ) : (
                  "Send buyer offer →"
                )}
              </button>
            </>
          )}

          <LiveContract
            product={selectedProduct}
            buyerKey={session?.buyer_key ?? ""}
            move={liveMove}
            status={status}
            finalTerms={finalTerms}
            order={order}
          />
        </section>

        {/* -------------------------- AUDIT TRAIL -------------------------- */}
        <section className="card">
          <h2 className="card__title">
            Audit trail
            <span
              className={`status-dot status-dot--${status.toLowerCase()}`}
            />
            {status}
          </h2>
          <AuditTrail trail={audit} />
        </section>
      </div>
    </main>
  );
}
