#!/usr/bin/env python3
"""Scope drift checker for Databricks Apps OBO.

Diffs ``user_authorization.scopes`` (app.yaml) against
``user_api_scopes`` (databricks.yml). Prints a human diff, exits non-zero
on drift, and -- when the effective scope set changed vs the snapshot in
``.apx/last-deployed-scopes.json`` -- reminds the operator that existing
users must revoke + re-consent to pick up the change (F5 in
``docs/obo-auth-design.md``).

Zero runtime dependencies -- uses a tiny purpose-built parser so the
script runs from ``python scripts/check_scopes.py`` even before
``uv sync``.

Usage:

    python scripts/check_scopes.py                # check, print diff, exit non-zero on drift
    python scripts/check_scopes.py --update-snapshot   # after a successful deploy

Exit codes:
    0  scopes aligned; no action needed
    1  drift detected (app.yaml and databricks.yml disagree)
    2  effective scope set changed vs last snapshot (F5 reminder)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_YAML = REPO_ROOT / "app.yaml"
BUNDLE_YAML = REPO_ROOT / "databricks.yml"
SNAPSHOT = REPO_ROOT / ".apx" / "last-deployed-scopes.json"

# Scopes we declare in app.yaml but cannot add to databricks.yml because
# the bundle CLI rejects them as "not a valid scope". Tracked in
# docs/obo-auth-design.md (F1 for iam.access-control:workspace, F2 for
# model-serving). When the platform accepts them, remove them from this
# set as part of closing F1/F2.
KNOWN_ALLOWED_DRIFT_ONLY_IN_APP_YAML = {
    "model-serving",
    "iam.access-control:workspace",
    # Agent Bricks ``/api/2.0/permissions/knowledge-assistants`` rejects
    # OBO tokens with ``Provided OAuth token does not have required
    # scopes: access-management``, but Databricks Apps does not yet list
    # ``access-management`` as a valid ``user_api_scopes`` value either
    # (bundle CLI responds with "is not a valid scope"). Tracked alongside
    # F1/F2 -- docs/rollback-obo-gaps-2026-04-17.md §11.2.1 fallback.
    "access-management",
}


# --------------------------------------------------------------------------- #
# Minimal YAML list extractor (indentation-aware, no external deps)
# --------------------------------------------------------------------------- #

def _strip_yaml_comment(line: str) -> str:
    """Drop trailing YAML line comments without breaking quoted strings."""
    in_single = False
    in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return line[:i]
    return line


def _extract_list_under_key(yaml_text: str, key_path: list[str]) -> list[str]:
    """Return the list of scalar items nested under ``key_path``.

    Works for the narrow subset of YAML we actually use:
    - plain mapping keys at consistent indent
    - scalar list items prefixed with ``-``
    - line comments (full-line or trailing)

    For anything more complex we'd want PyYAML; that's deliberately
    avoided so this script has zero install cost.
    """
    lines = yaml_text.splitlines()
    target_depth = 0
    search_index = 0

    # Walk the key path: for each key we find its indent, then scan for the
    # next key at a deeper indent.
    for depth_index, key in enumerate(key_path):
        pattern = re.compile(rf"^(\s*){re.escape(key)}\s*:\s*$")
        found = False
        for idx in range(search_index, len(lines)):
            body = _strip_yaml_comment(lines[idx]).rstrip()
            if not body.strip():
                continue
            m = pattern.match(body)
            if not m:
                continue
            indent = len(m.group(1))
            # For non-terminal keys, require indent matches the accumulated
            # depth so we don't match a different YAML subtree that happens
            # to have the same key name.
            if indent != target_depth:
                continue
            target_depth = indent + 2
            search_index = idx + 1
            found = True
            break
        if not found:
            return []

    items: list[str] = []
    for idx in range(search_index, len(lines)):
        raw = lines[idx]
        body = _strip_yaml_comment(raw).rstrip()
        stripped = body.strip()
        if not stripped:
            continue
        leading = len(body) - len(body.lstrip())
        if leading < target_depth:
            break
        if stripped.startswith("- "):
            items.append(stripped[2:].strip().strip("\"'"))
        else:
            # A sibling key at the same or lower indent terminates the list.
            if leading <= target_depth:
                break
    return items


def read_app_yaml_scopes(path: Path = APP_YAML) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return _extract_list_under_key(text, ["user_authorization", "scopes"])


def read_bundle_yaml_scopes(path: Path = BUNDLE_YAML) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return _extract_list_under_key(
        text,
        ["resources", "apps", "scgp_agent_hub", "user_api_scopes"],
    )


# --------------------------------------------------------------------------- #
# Diff reporting
# --------------------------------------------------------------------------- #

class Colors:
    RESET = "\033[0m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BOLD = "\033[1m"


def _c(text: str, color: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"{color}{text}{Colors.RESET}"


def report_diff(app_scopes: list[str], bundle_scopes: list[str]) -> int:
    app_set = set(app_scopes)
    bundle_set = set(bundle_scopes)

    only_in_app = sorted(app_set - bundle_set)
    only_in_bundle = sorted(bundle_set - app_set)

    # Filter out drift we already accept (see KNOWN_ALLOWED_DRIFT_ONLY_IN_APP_YAML).
    filtered_only_in_app = [
        s for s in only_in_app if s not in KNOWN_ALLOWED_DRIFT_ONLY_IN_APP_YAML
    ]
    accepted_drift = [
        s for s in only_in_app if s in KNOWN_ALLOWED_DRIFT_ONLY_IN_APP_YAML
    ]

    print(_c("OBO scope drift check", Colors.BOLD))
    print(f"  app.yaml       ({len(app_scopes)} scopes): {sorted(app_set)}")
    print(f"  databricks.yml ({len(bundle_scopes)} scopes): {sorted(bundle_set)}")
    print()

    if accepted_drift:
        print(_c(
            f"  accepted drift (only in app.yaml, known-F2): {accepted_drift}",
            Colors.YELLOW,
        ))

    if filtered_only_in_app:
        print(_c(f"  MISSING from databricks.yml: {filtered_only_in_app}", Colors.RED))
    if only_in_bundle:
        print(_c(f"  UNEXPECTED in databricks.yml: {only_in_bundle}", Colors.RED))

    if not filtered_only_in_app and not only_in_bundle:
        print(_c("  OK -- files are aligned", Colors.GREEN))
        return 0

    print()
    print(_c("Drift detected. Reconcile before deploying.", Colors.RED))
    return 1


# --------------------------------------------------------------------------- #
# Snapshot + F5 reminder
# --------------------------------------------------------------------------- #

F5_REMINDER = """\
F5 REMINDER: Databricks does NOT re-prompt existing users for consent
after a scope change. Each user who has authorized this app previously
must REVOKE the app under Account Settings -> Apps, then revisit the
app to be shown a fresh consent screen that includes the new scopes.
Otherwise their forwarded X-Forwarded-Access-Token will continue to
carry only the old scope set (see docs/obo-auth-design.md F5).
"""


def _effective_scope_set(app_scopes: list[str], bundle_scopes: list[str]) -> list[str]:
    """The scope set deployed at runtime is whatever the bundle grants.

    app.yaml scopes are advisory; only databricks.yml is deployed. The
    F5 snapshot therefore tracks the bundle set -- if it changed, users
    need to re-consent.
    """
    _ = app_scopes
    return sorted(set(bundle_scopes))


def load_snapshot() -> list[str] | None:
    if not SNAPSHOT.exists():
        return None
    try:
        data = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    scopes = data.get("effective_scopes")
    if isinstance(scopes, list):
        return [str(s) for s in scopes]
    return None


def write_snapshot(scopes: list[str]) -> None:
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(
        json.dumps({"effective_scopes": scopes}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--update-snapshot",
        action="store_true",
        help="Overwrite .apx/last-deployed-scopes.json with the current bundle "
             "scope set. Run this after a successful deploy.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the F5 reminder when scopes are unchanged.",
    )
    args = parser.parse_args(argv)

    app_scopes = read_app_yaml_scopes()
    bundle_scopes = read_bundle_yaml_scopes()

    drift_exit = report_diff(app_scopes, bundle_scopes)

    effective = _effective_scope_set(app_scopes, bundle_scopes)
    previous = load_snapshot()

    if args.update_snapshot:
        write_snapshot(effective)
        print()
        print(_c(f"Snapshot updated: {SNAPSHOT.relative_to(REPO_ROOT)}", Colors.GREEN))
        return drift_exit

    if previous is None:
        if not args.quiet:
            print()
            print(_c("No prior snapshot found -- run with --update-snapshot "
                    "after the next successful deploy.", Colors.YELLOW))
        return drift_exit

    if sorted(previous) != effective:
        print()
        print(_c("Effective user_api_scopes changed since last snapshot:", Colors.YELLOW))
        print(f"  before: {sorted(previous)}")
        print(f"  after : {effective}")
        print()
        print(_c(F5_REMINDER, Colors.YELLOW))
        # drift_exit still wins if both are true, but we want to signal
        # F5 when the files are aligned and only the snapshot is behind.
        return drift_exit or 2

    if drift_exit == 0 and not args.quiet:
        print()
        print(_c(
            "No scope changes vs last snapshot -- users keep their "
            "existing consent.",
            Colors.GREEN,
        ))

    return drift_exit


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
