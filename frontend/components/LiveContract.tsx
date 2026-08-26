"use client";

import { useState } from "react";
import type {
  CatalogProduct,
  CounterOffer,
  MerchantMove,
  NegStatus,
  OrderInfo,
} from "@/lib/types";
import { formatINR, formatOffer } from "@/lib/format";
import { downloadInvoiceBlob } from "@/lib/api";
import Spinner from "@/components/Spinner";

export default function LiveContract({
  product,
  buyerKey,
  move,
  status,
  finalTerms,
  order,
}: {
  product: CatalogProduct | null;
  buyerKey: string;
  move: MerchantMove | null;
  status: NegStatus;
  finalTerms: CounterOffer | null;
  order: OrderInfo | null;
}) {
  const [downloading, setDownloading] = useState(false);
  const [dlError, setDlError] = useState<string | null>(null);

  const handleDownload = async () => {
    if (!order || !order.order_id) return;
    setDownloading(true);
    setDlError(null);
    try {
      const blob = await downloadInvoiceBlob(buyerKey, order.order_id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${order.order_id}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e: any) {
      setDlError("Download failed: " + (e?.message || "unknown error"));
    } finally {
      setDownloading(false);
    }
  };

  if (order) {
    return (
      <div className="live">
        <div className="banner banner--win">
          ✓ Deal closed — Razorpay order created
        </div>
        <div className="kv">
          <span className="muted">Order ID</span>
          <span className="mono">{order.order_id}</span>
          {order.razorpay_order_id && (
            <>
              <span className="muted">Razorpay order</span>
              <span className="mono">{order.razorpay_order_id}</span>
            </>
          )}
          {order.amount_paise != null && (
            <>
              <span className="muted">Amount</span>
              <span className="mono">{formatINR(order.amount_paise)}</span>
            </>
          )}
        </div>
        <button
          className="link link--btn"
          onClick={handleDownload}
          disabled={downloading || !order.order_id}
        >
          {downloading ? (
            <>
              <Spinner /> Preparing PDF…
            </>
          ) : (
            "⬇ Download invoice PDF"
          )}
        </button>
        {dlError && <div className="reason reason--error">{dlError}</div>}
      </div>
    );
  }

  // Before any offer is sent, show the merchant's STANDING listed price for the
  // selected component (its lowest tier) so the box always matches the item.
  if (!move && product) {
    const listed: CounterOffer = {
      unit_price: product.base_unit_price,
      min_volume: product.volume_tiers[0]?.min_qty ?? 1,
      payment_terms_days: 0,
      delivery_days: product.lead_time_max_days,
      recurring: false,
    };
    return (
      <div className="live">
        <div className="field__label">
          Live contract (merchant&rsquo;s current move)
        </div>
        <div className="kv">
          <span className="muted">Action</span>
          <span>listed price</span>
          <span className="muted">Offer</span>
          <span>{formatOffer(listed)}</span>
        </div>
        <div className="reason">
          Current standing offer for {product.name}. Send an offer to negotiate.
        </div>
      </div>
    );
  }

  const terms = move?.offer || finalTerms;
  return (
    <div className="live">
      <div className="field__label">
        Live contract (merchant&rsquo;s current move)
      </div>
      {!move ? (
        <div className="muted small">Waiting for first move&hellip;</div>
      ) : (
        <>
          <div className="kv">
            <span className="muted">Action</span>
            <span>{move.action}</span>
            {terms && (
              <>
                <span className="muted">Offer</span>
                <span>{formatOffer(terms)}</span>
              </>
            )}
          </div>
          {move.reason && <div className="reason">{move.reason}</div>}
        </>
      )}
    </div>
  );
}
