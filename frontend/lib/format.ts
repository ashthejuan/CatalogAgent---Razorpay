import type { CounterOffer } from "@/lib/types";

/** Render a contract offer as a single readable sentence, not JSON. */
export function formatOffer(o: CounterOffer): string {
  const recurring = o.recurring ? "Yes" : "No";
  return `Unit price ₹${o.unit_price.toFixed(2)} · Volume ${o.min_volume.toLocaleString(
    "en-IN",
  )} · Net ${o.payment_terms_days} days · Delivery ${o.delivery_days} days · Recurring: ${recurring}`;
}

/** Render paise as Indian Rupees, e.g. 1150000 -> "₹11,500.00". */
export function formatINR(paise: number): string {
  return (
    "₹" +
    (paise / 100).toLocaleString("en-IN", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })
  );
}
