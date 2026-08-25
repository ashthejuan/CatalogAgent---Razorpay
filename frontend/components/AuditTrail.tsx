import type { AuditEntry, CounterOffer } from "@/lib/types";
import { formatOffer } from "@/lib/format";

function offerFromEntry(e: AuditEntry): CounterOffer | null {
  if (
    e.actor === "buyer_agent" &&
    e.action === "counter_offer" &&
    e.payload &&
    typeof e.payload === "object" &&
    "unit_price" in e.payload
  ) {
    return e.payload as unknown as CounterOffer;
  }
  if (e.actor === "merchant_llm" && e.payload && e.payload.offer) {
    return e.payload.offer as unknown as CounterOffer;
  }
  return null;
}

function reasonFromEntry(e: AuditEntry): string | null {
  if (e.reason) return e.reason;
  if (e.payload && e.payload.reason) return e.payload.reason as string;
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
