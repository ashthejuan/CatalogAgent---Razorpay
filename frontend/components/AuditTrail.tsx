import type { AuditEntry, CounterOffer } from "@/lib/types";
import { formatOffer } from "@/lib/format";

function offerFromEntry(e: AuditEntry): CounterOffer | null {
  const p = e.payload;
  if (!p || typeof p !== "object") return null;
  // Nested under .offer (history-shaped payloads)
  const nested = p.offer;
  if (nested && typeof nested === "object" && "unit_price" in nested) {
    return nested as unknown as CounterOffer;
  }
  // Backend audits dump CounterOffer at the top level (buyer, merchant, policy)
  if ("unit_price" in p && "min_volume" in p) {
    return p as unknown as CounterOffer;
  }
  return null;
}

function reasonFromEntry(e: AuditEntry): string | null {
  if (e.reason) return e.reason;
  if (e.payload && e.payload.reason) return e.payload.reason as string;
  if (e.payload && e.payload.error) return String(e.payload.error);
  return null;
}

const ACTOR_COLOR: Record<string, string> = {
  buyer_agent: "var(--buyer)",
  merchant_llm: "var(--merchant)",
  policy_engine: "var(--fg)",
  payments: "var(--payments)",
  system: "var(--muted)",
};

export default function AuditTrail({ trail }: { trail: AuditEntry[] }) {
  if (!trail.length) {
    return <div className="muted small">No activity yet.</div>;
  }

  const byTurn: Record<number, AuditEntry[]> = {};
  for (const e of trail) {
    (byTurn[e.turn] ||= []).push(e);
  }
  const turns = Object.keys(byTurn)
    .map(Number)
    .sort((a, b) => a - b);

  return (
    <div className="trail">
      {turns.map((turn) => (
        <div className="turn" key={turn}>
          <div className="turn__head">
            <span className="turn__num">Turn {turn}</span>
            <span className="turn__line" />
          </div>
          {byTurn[turn].map((e, i) => {
            const offer = offerFromEntry(e);
            const reason = reasonFromEntry(e);
            const color = ACTOR_COLOR[e.actor] || "var(--fg)";
            return (
              <div className="entry" key={i}>
                <span
                  className="chip"
                  style={{ color, borderColor: color }}
                >
                  {e.actor}
                </span>
                <span className="entry__action">{e.action}</span>
                {e.verdict && (
                  <span className={`badge badge--${e.verdict.toLowerCase()}`}>
                    {e.verdict}
                  </span>
                )}
                {offer && <div className="offer">{formatOffer(offer)}</div>}
                {reason && <div className="reason">{reason}</div>}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}
