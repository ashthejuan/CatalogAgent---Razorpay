"""SQLite access layer."""

import app.config  # noqa: F401 — load .env before os.environ reads

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app.schemas import CatalogProduct, Product, VolumeTier

_DEFAULT_DB = Path(__file__).resolve().parent.parent / "catalogagent.db"
DB_PATH = os.environ.get("CATALOGAGENT_DB_PATH", str(_DEFAULT_DB))


@dataclass
class Buyer:
    buyer_key_hash: str
    buyer_id: str
    budget_cap: float


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create Phase 1 tables. Later phases add negotiations, audit_log, orders."""
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS products (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                base_unit_price REAL NOT NULL,
                stock INTEGER NOT NULL,
                lead_time_min_days INTEGER NOT NULL,
                lead_time_max_days INTEGER NOT NULL,
                volume_tiers TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS buyers (
                buyer_key_hash TEXT PRIMARY KEY,
                buyer_id TEXT NOT NULL,
                budget_cap REAL NOT NULL
            );

            -- Phase 3: append-only audit trail. No UPDATE/DELETE is ever issued
            -- against this table anywhere in the codebase (enforced by review + tests).
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                negotiation_id TEXT NOT NULL,
                turn INTEGER NOT NULL,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                payload TEXT NOT NULL,
                verdict TEXT,
                reason TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            -- Phase 4/6 will add: negotiations, orders.
            """
        )


def _row_to_product(row: sqlite3.Row) -> Product:
    tiers = [VolumeTier.model_validate(t) for t in json.loads(row["volume_tiers"])]
    return Product(
        id=row["id"],
        name=row["name"],
        category=row["category"],
        base_unit_price=row["base_unit_price"],
        stock=row["stock"],
        lead_time_min_days=row["lead_time_min_days"],
        lead_time_max_days=row["lead_time_max_days"],
        volume_tiers=tiers,
    )


def upsert_product(p: Product) -> None:
    tiers_json = json.dumps([t.model_dump() for t in p.volume_tiers])
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO products (
                id, name, category, base_unit_price, stock,
                lead_time_min_days, lead_time_max_days, volume_tiers
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                category = excluded.category,
                base_unit_price = excluded.base_unit_price,
                stock = excluded.stock,
                lead_time_min_days = excluded.lead_time_min_days,
                lead_time_max_days = excluded.lead_time_max_days,
                volume_tiers = excluded.volume_tiers
            """,
            (
                p.id,
                p.name,
                p.category,
                p.base_unit_price,
                p.stock,
                p.lead_time_min_days,
                p.lead_time_max_days,
                tiers_json,
            ),
        )


def get_product(product_id: str) -> Product | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM products WHERE id = ?", (product_id,)
        ).fetchone()
    if row is None:
        return None
    return _row_to_product(row)


def get_catalog() -> list[Product]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM products ORDER BY category, id").fetchall()
    return [_row_to_product(row) for row in rows]


def get_public_catalog() -> list[CatalogProduct]:
    """Public catalog view — list prices only; floor_price stays server-side."""
    return [CatalogProduct.from_product(p) for p in get_catalog()]


def get_buyer_by_hash(key_hash: str) -> Buyer | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT buyer_key_hash, buyer_id, budget_cap FROM buyers WHERE buyer_key_hash = ?",
            (key_hash,),
        ).fetchone()
    if row is None:
        return None
    return Buyer(
        buyer_key_hash=row["buyer_key_hash"],
        buyer_id=row["buyer_id"],
        budget_cap=row["budget_cap"],
    )


def get_buyer_by_id(buyer_id: str) -> Buyer | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT buyer_key_hash, buyer_id, budget_cap FROM buyers WHERE buyer_id = ?",
            (buyer_id,),
        ).fetchone()
    if row is None:
        return None
    return Buyer(
        buyer_key_hash=row["buyer_key_hash"],
        buyer_id=row["buyer_id"],
        budget_cap=row["budget_cap"],
    )


def insert_buyer(buyer_id: str, key_hash: str, budget_cap: float) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO buyers (buyer_key_hash, buyer_id, budget_cap) VALUES (?, ?, ?)",
            (key_hash, buyer_id, budget_cap),
        )


def _audit_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "negotiation_id": row["negotiation_id"],
        "turn": row["turn"],
        "actor": row["actor"],
        "action": row["action"],
        "payload": json.loads(row["payload"]),
        "verdict": row["verdict"],
        "reason": row["reason"],
        "created_at": row["created_at"],
    }


def append_audit(
    negotiation_id: str,
    turn: int,
    actor: str,
    action: str,
    payload: dict,
    verdict: str | None = None,
    reason: str | None = None,
) -> int:
    """Append-only write to the audit trail. There is no update/delete path.

    Returns the new row id.
    """
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO audit_log
                (negotiation_id, turn, actor, action, payload, verdict, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                negotiation_id,
                turn,
                actor,
                action,
                json.dumps(payload),
                verdict,
                reason,
            ),
        )
        return cur.lastrowid


def get_audit_trail(negotiation_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE negotiation_id = ? ORDER BY id ASC",
            (negotiation_id,),
        ).fetchall()
    return [_audit_row_to_dict(row) for row in rows]


def format_audit_trail(negotiation_id: str) -> str:
    """Render the trail as a readable aligned table (used by endpoint + demo)."""
    trail = get_audit_trail(negotiation_id)
    if not trail:
        return f"no audit trail for {negotiation_id}"
    lines = [f"audit trail: {negotiation_id}", "-" * 64]
    for e in trail:
        verdict = e["verdict"] or "-"
        reason = f" | {e['reason']}" if e["reason"] else ""
        lines.append(
            f"turn {e['turn']:<2} {e['actor']:<12} {e['action']:<14} "
            f"[{verdict}]{reason}"
        )
    return "\n".join(lines)


def clear_products() -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM products")
