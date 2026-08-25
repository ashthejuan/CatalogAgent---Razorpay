# CatalogAgent

## Overview

Placeholder — full overview arrives in Phase 7.

## Architecture

Placeholder — architecture notes arrive in Phase 7.

## Security

Placeholder — security documentation arrives in Phase 7.

## Quickstart

Requires Python 3.11 and the project venv (`.venv/`). Secrets live in `.env` (never committed).

```bash
# 1. from backend/: load secrets, then start the server
set -a; source .env; set +a
./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000

# 2. in a second terminal — public, agent-readable catalog
curl -s http://127.0.0.1:8000/catalog | python -m json.tool
```

Expected: JSON with 10 products; each volume tier exposes only `min_qty` and
`unit_price` (the internal `floor_price` is deliberately stripped from the
public catalog — see Security).

Notes:
- Run these in **Git Bash** (MINGW64), not PowerShell — PowerShell's `curl` is
  an `Invoke-WebRequest` alias and behaves differently.
- If port 8000 is "already in use", pick another (e.g. 8001) and keep the
  server and curl ports consistent.
- `python -m json.tool` needs `python` on PATH; otherwise use
  `.venv/Scripts/python.exe -m json.tool`.

## AWS Production Path

Placeholder — AWS production path arrives in Phase 7.

## Demo Video

Placeholder — demo video link arrives in Phase 7.
