import type {
  AuditResponse,
  CatalogProduct,
  CounterOffer,
  NegotiateResponse,
  UISession,
} from "@/lib/types";

const BASE = "/api";

async function jsonFetch<T = any>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(String(detail));
  }
  return res.json();
}

export async function createSession(productId?: string): Promise<UISession> {
  const qs = productId ? `?product_id=${encodeURIComponent(productId)}` : "";
  return jsonFetch<UISession>(`/ui/session${qs}`, { method: "POST" });
}

export async function getCatalog(): Promise<CatalogProduct[]> {
  return jsonFetch<CatalogProduct[]>("/catalog");
}

export async function negotiate(
  buyerKey: string,
  negotiationId: string,
  offer: CounterOffer,
): Promise<NegotiateResponse> {
  return jsonFetch<NegotiateResponse>("/negotiate", {
    method: "POST",
    headers: { "X-Buyer-Key": buyerKey },
    body: JSON.stringify({ negotiation_id: negotiationId, buyer_offer: offer }),
  });
}

export async function getAudit(
  buyerKey: string,
  negotiationId: string,
): Promise<AuditResponse> {
  return jsonFetch<AuditResponse>(`/audit/${negotiationId}`, {
    headers: { "X-Buyer-Key": buyerKey },
  });
}

/**
 * Download the invoice PDF. Must be a programmatic fetch (not a plain <a href>)
 * because the /invoices endpoint requires the X-Buyer-Key header — a raw link
 * click can't send it and returns 401.
 */
export async function downloadInvoiceBlob(
  buyerKey: string,
  orderId: string,
): Promise<Blob> {
  const res = await fetch(`${BASE}/invoices/${encodeURIComponent(orderId)}`, {
    headers: { "X-Buyer-Key": buyerKey },
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.blob();
}

/**
 * Deterministic stand-in for the buyer LLM agent (mirrors backend personas).
 * Keeps the demo latency-free and free of a second live LLM call; the merchant
 * side stays the real LLM + guardrail. Offers escalate toward a legal close.
 */
export function autoBuyerOffer(persona: string, step: number): CounterOffer {
  const base: CounterOffer = {
    unit_price: 9.5,
    min_volume: 5000,
    payment_terms_days: 30,
    delivery_days: 14,
    recurring: false,
  };
  if (persona === "aggressive") {
    return {
      ...base,
      unit_price: 8.4,
      min_volume: 1000,
      payment_terms_days: 30,
      delivery_days: 21,
      recurring: false,
    };
  }
  if (persona === "creative") {
    return {
      ...base,
      unit_price: 11.0,
      min_volume: 5000,
      payment_terms_days: 0,
      delivery_days: 21,
      recurring: true,
    };
  }
  // reasonable: converges to a legal close
  const prices = [10.0, 10.8, 11.2, 11.5];
  return {
    ...base,
    unit_price: prices[Math.min(step, prices.length - 1)],
    min_volume: 1000,
    payment_terms_days: 0,
    delivery_days: 21,
    recurring: false,
  };
}
