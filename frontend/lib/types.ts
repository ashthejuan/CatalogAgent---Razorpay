// Shared types mirroring the CatalogAgent backend contract.

export interface CatalogTier {
  min_qty: number;
  unit_price: number;
}

export interface CatalogProduct {
  id: string;
  name: string;
  category: string;
  base_unit_price: number;
  stock: number;
  lead_time_min_days: number;
  lead_time_max_days: number;
  volume_tiers: CatalogTier[];
}

export interface CounterOffer {
  unit_price: number;
  min_volume: number;
  payment_terms_days: number;
  delivery_days: number;
  recurring: boolean;
}

export type MerchantAction = "counter_offer" | "accept" | "escalate";

export interface MerchantMove {
  action: MerchantAction;
  offer: CounterOffer | null;
  reason: string | null;
}

export type NegStatus = "OPEN" | "CLOSED_WON" | "ESCALATED";

export interface NegotiateResponse {
  status: NegStatus;
  merchant_move: MerchantMove;
  audit_excerpt: string;
  final_terms: CounterOffer | null;
  order_id: string | null;
}

export interface UISession {
  buyer_key: string;
  buyer_id: string;
  negotiation_id: string;
  product_id: string;
}

export interface AuditEntry {
  turn: number;
  actor: string;
  action: string;
  payload: Record<string, any> | null;
  verdict: string | null;
  reason: string | null;
}

export interface AuditResponse {
  negotiation_id: string;
  trail: AuditEntry[];
  text: string;
}

export interface OrderInfo {
  order_id: string;
  razorpay_order_id: string;
  amount_paise: number | null;
}
