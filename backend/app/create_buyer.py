"""CLI to provision buyer API keys (stores hash only)."""

import argparse
import hashlib
import hmac
import os
import secrets
import sys

from app.db import get_buyer_by_id, init_db, insert_buyer


def hash_buyer_key(key: str) -> str:
    pepper = os.environ.get("KEY_PEPPER")
    if pepper:
        return hmac.new(pepper.encode(), key.encode(), hashlib.sha256).hexdigest()
    return hashlib.sha256(key.encode()).hexdigest()


def create_buyer(name: str, budget_cap: float) -> str:
    if get_buyer_by_id(name) is not None:
        raise ValueError(f"Buyer '{name}' already exists — refusing to duplicate.")

    key = f"bk_{name}_{secrets.token_hex(4)}"
    key_hash = hash_buyer_key(key)
    insert_buyer(name, key_hash, budget_cap)
    return key


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Provision a buyer API key.")
    parser.add_argument("name", help="Buyer identifier (e.g. acme)")
    parser.add_argument("--budget", type=float, required=True, help="Budget cap")
    args = parser.parse_args(argv)

    init_db()

    try:
        key = create_buyer(args.name, args.budget)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print("")
    print("=" * 60)
    print("  STORE THIS NOW — shown only once")
    print("=" * 60)
    print(f"  Buyer key: {key}")
    print("=" * 60)
    print("")

    key_hash = hash_buyer_key(key)
    print(f"Created buyer_id={args.name}, budget_cap={args.budget}, hash_prefix={key_hash[:8]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
