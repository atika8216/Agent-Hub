#!/usr/bin/env python3
"""Agent-type audit for ``catalog_config`` rows.

Part of Phase 1 of the SCGP Agent Hub master roadmap. Reads every row in
``catalog_config`` and flags mismatches between the endpoint-name prefix
(``uc:`` / ``mcp:`` / ``genie:`` / plain) and the persisted
``agent_type`` column.

**Read-only**. The script never writes to Lakebase -- output is intended
for humans to review before running ``POST /agents/discover`` or manually
editing rows.

Run locally against the same Lakebase the app uses:

    uv run python scripts/audit_agent_types.py

Or point at an explicit database URL:

    DATABASE_URL='postgresql://...' uv run python scripts/audit_agent_types.py

Exit codes:
    0  every row's prefix matches its persisted agent_type
    1  one or more mismatches found (details printed)
    2  could not connect to the database / query failed
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent

# Allow ``python scripts/audit_agent_types.py`` without ``uv sync`` by
# adding ``src/`` to sys.path -- mirrors the test runner config.
sys.path.insert(0, str(REPO_ROOT / "src"))


# --------------------------------------------------------------------------- #
# Classification rules
# --------------------------------------------------------------------------- #

_UC_PREFIX = "uc:"
_MCP_PREFIX = "mcp:"
_GENIE_PREFIX = "genie:"

# Allowed (prefix, agent_type) pairs. Prefixes uniquely determine the
# agent_type column; plain endpoints are open-ended and are validated in
# the ``expected_types_for`` helper instead.
_PREFIX_TO_EXPECTED: dict[str, frozenset[str]] = {
    _UC_PREFIX: frozenset({"HTTP_CONNECTION"}),
    _MCP_PREFIX: frozenset({"MCP_ENDPOINT"}),
    _GENIE_PREFIX: frozenset({"GENIE_SPACE"}),
}

_PLAIN_ALLOWED = frozenset({"MAS", "AGENT", "KA", "MODEL", "EXTERNAL"})


def expected_types_for(endpoint_name: str) -> frozenset[str]:
    """Return the set of ``agent_type`` values valid for the given prefix."""
    for prefix, allowed in _PREFIX_TO_EXPECTED.items():
        if endpoint_name.startswith(prefix):
            return allowed
    return _PLAIN_ALLOWED


# --------------------------------------------------------------------------- #
# DB access
# --------------------------------------------------------------------------- #


def _build_connect_url() -> str:
    """Resolve the Postgres URL the same way the app does.

    Supports both ``DATABASE_URL`` (used in local dev / bundles) and the
    individual ``PG*`` env vars the app's ``core/database.py`` falls
    back on.
    """
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url

    # Reconstruct from PG* env vars if needed.
    host = os.environ.get("PGHOST") or os.environ.get("LAKEBASE_HOST")
    user = os.environ.get("PGUSER") or os.environ.get("LAKEBASE_USER")
    pwd = os.environ.get("PGPASSWORD") or os.environ.get("LAKEBASE_PASSWORD")
    db = os.environ.get("PGDATABASE") or os.environ.get("LAKEBASE_DB") or "postgres"
    port = os.environ.get("PGPORT", "5432")
    if not (host and user and pwd):
        print(
            "error: neither DATABASE_URL nor (PGHOST/PGUSER/PGPASSWORD) is set",
            file=sys.stderr,
        )
        sys.exit(2)
    return f"postgresql://{user}:{pwd}@{host}:{port}/{db}"


def _fetch_rows(url: str) -> list[tuple[str, str | None]]:
    """Pull ``(endpoint_name, agent_type)`` for every catalog row.

    Uses psycopg3 directly to keep the script usable even when the full
    FastAPI stack is not importable (e.g. the Lakebase creds are stale
    and ``core.database`` would otherwise blow up on import).
    """
    try:
        import psycopg  # type: ignore[import-not-found]
    except ImportError:
        print(
            "error: psycopg is required. Install via 'uv sync' or "
            "'pip install psycopg[binary]'.",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        with psycopg.connect(url, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT endpoint_name, agent_type FROM catalog_config "
                    "ORDER BY endpoint_name"
                )
                return [(str(r[0]), r[1] if r[1] is None else str(r[1])) for r in cur.fetchall()]
    except Exception as e:
        print(f"error: failed to query catalog_config: {e}", file=sys.stderr)
        sys.exit(2)


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def _iter_mismatches(
    rows: Iterable[tuple[str, str | None]],
) -> list[tuple[str, str, frozenset[str]]]:
    """Return ``(endpoint_name, actual_type, expected_types)`` for bad rows."""
    mismatches: list[tuple[str, str, frozenset[str]]] = []
    for endpoint_name, actual in rows:
        actual_str = (actual or "").upper().strip()
        allowed = expected_types_for(endpoint_name)
        if actual_str not in allowed:
            mismatches.append((endpoint_name, actual_str or "<NULL>", allowed))
    return mismatches


def _print_report(
    total: int,
    mismatches: list[tuple[str, str, frozenset[str]]],
) -> None:
    print(f"catalog_config rows scanned: {total}")
    print(f"mismatches found:           {len(mismatches)}")
    if not mismatches:
        print("OK -- every endpoint prefix agrees with its agent_type")
        return

    print()
    print(f"{'endpoint_name':60s}  {'actual':20s}  expected")
    print(f"{'-' * 60:60s}  {'-' * 20:20s}  {'-' * 30}")
    for name, actual, allowed in mismatches:
        allowed_str = "|".join(sorted(allowed))
        print(f"{name:60.60s}  {actual:20.20s}  {allowed_str}")

    print()
    print("Remediation options:")
    print("  - Run POST /agents/discover to re-classify via live introspection")
    print("  - Or update catalog_config.agent_type directly via SQL if the")
    print("    discovery source is unavailable")


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override DATABASE_URL (otherwise env/PG* vars are used).",
    )
    args = parser.parse_args(argv)

    url = args.database_url or _build_connect_url()
    rows = _fetch_rows(url)
    mismatches = _iter_mismatches(rows)
    _print_report(len(rows), mismatches)
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
