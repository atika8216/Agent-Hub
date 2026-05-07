"""Grant a Databricks App service principal access to a Lakebase project.

The first time you deploy a Databricks App, the workspace creates a fresh
service principal for it. The SP exists at the workspace level but it is
NOT yet a Lakebase Postgres role inside your project, so the app's first
DB connection fails with::

    password authentication failed for user '<sp-uuid>'

This script automates the one-time grant by registering the SP as a
Lakebase role via the public ``POST /api/2.0/postgres/.../roles`` API
(``WorkspaceClient.postgres.create_role``). That endpoint is the only
supported way to wire a workspace identity into a Lakebase project --
hand-rolled SQL ``CREATE ROLE`` only produces a ``NO_LOGIN`` placeholder
because Lakebase OAuth auth requires the role to be created with
``auth_method=LAKEBASE_OAUTH_V1`` and a ``DATABRICKS_SUPERUSER``
membership.

Usage::

    python scripts/grant_lakebase_sp.py \\
        --profile <cli-profile> \\
        --lakebase-project <project-id> \\
        --app-name <app-slug>

Idempotent: if the role already exists with the right spec, the script
exits 0 with a no-op message. If it exists but with the wrong
``auth_method`` (e.g. ``NO_LOGIN`` from a previous manual ``CREATE ROLE``
attempt), the script deletes the broken role and recreates it cleanly.

Exit codes:
    0  granted (or already granted)
    1  argparse / unexpected error
    2  app not found in workspace -- run ``databricks bundle deploy`` first
    3  Lakebase project / endpoint not found -- create the project first
    4  Lakebase API call denied -- caller is not Lakebase admin
    5  unexpected SDK error during role create/delete (see message)
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import cast

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import DatabricksError, NotFound, PermissionDenied
from databricks.sdk.service.postgres import (
    Role,
    RoleAuthMethod,
    RoleIdentityType,
    RoleMembershipRole,
    RoleRoleSpec,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Grant a Databricks App service principal access to a Lakebase project.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--profile",
        required=True,
        help="Databricks CLI profile (must have Lakebase admin on the project).",
    )
    parser.add_argument(
        "--lakebase-project",
        required=True,
        help="Lakebase Autoscale project_id (e.g. 'agent-hub').",
    )
    parser.add_argument(
        "--app-name",
        required=True,
        help="Databricks App slug (e.g. 'agent-hub-dev'). Used to look up the app SP.",
    )
    parser.add_argument(
        "--branch",
        default="production",
        help="Lakebase branch (default: production).",
    )
    return parser.parse_args()


def _resolve_sp(ws: WorkspaceClient, app_name: str) -> str:
    try:
        app = ws.apps.get(name=app_name)
    except NotFound:
        print(
            f"ERROR: app '{app_name}' not found in this workspace. "
            f"Run 'databricks bundle deploy' first.",
            file=sys.stderr,
        )
        sys.exit(2)
    sp_uuid = getattr(app, "service_principal_client_id", None)
    if not sp_uuid:
        print(
            f"ERROR: app '{app_name}' has no service_principal_client_id yet. "
            f"Wait for the deploy to finish, then retry.",
            file=sys.stderr,
        )
        sys.exit(2)
    return sp_uuid


def _find_existing_role(ws: WorkspaceClient, parent: str, sp_uuid: str) -> Role | None:
    """Return the existing Lakebase role for this SP, if any.

    Match on ``spec.postgres_role == sp_uuid`` so we catch both API-created
    roles and any orphan roles a user created with raw SQL (those show up
    via ``list_roles`` because Lakebase tracks every Postgres role).
    """
    try:
        roles = list(ws.postgres.list_roles(parent=parent))
    except NotFound:
        print(
            f"ERROR: Lakebase branch '{parent}' not found.",
            file=sys.stderr,
        )
        sys.exit(3)
    for role in roles:
        # ``list_roles`` returns the role spec under ``status`` for existing
        # roles (the create-time ``spec`` is mirrored back as ``status``).
        status = role.status
        if status and status.postgres_role == sp_uuid:
            return role
    return None


def _delete_role(ws: WorkspaceClient, role: Role) -> None:
    name = role.name
    if not name:
        return
    print(f"[info] deleting stale role {name} (auth_method != LAKEBASE_OAUTH_V1)")
    op = ws.postgres.delete_role(name=name)
    # ``delete_role`` returns a ``DeleteRoleOperation``; wait briefly so the
    # subsequent create doesn't race on the same postgres_role identifier.
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            ws.postgres.get_role(name=name)
        except NotFound:
            return
        time.sleep(1)
    # Best-effort: if it's still around after 30s, fall through and let
    # create_role surface the conflict.
    _ = op  # noqa: F841 -- kept for clarity / debugging


def _has_correct_spec(role: Role) -> bool:
    status = role.status
    if not status:
        return False
    if status.auth_method != RoleAuthMethod.LAKEBASE_OAUTH_V1:
        return False
    members = status.membership_roles or []
    if RoleMembershipRole.DATABRICKS_SUPERUSER not in members:
        return False
    return True


def _create_role(
    ws: WorkspaceClient, parent: str, sp_uuid: str
) -> None:
    role = Role(
        spec=RoleRoleSpec(
            identity_type=RoleIdentityType.SERVICE_PRINCIPAL,
            auth_method=RoleAuthMethod.LAKEBASE_OAUTH_V1,
            membership_roles=[RoleMembershipRole.DATABRICKS_SUPERUSER],
            postgres_role=sp_uuid,
        ),
    )
    try:
        # Don't pass role_id -- the API requires it to start with a lowercase
        # letter (^[a-z]...) and SP UUIDs typically start with a digit. The
        # SP identity is conveyed via ``spec.postgres_role`` instead, which
        # the runtime maps to the OAuth login. Lakebase auto-generates a
        # ``rol-<short>`` resource name.
        ws.postgres.create_role(parent=parent, role=role)
    except PermissionDenied as e:
        print(
            f"ERROR: create_role denied. Your CLI profile must be the "
            f"Lakebase project owner or an admin on it. Ask your Lakebase "
            f"admin to run this script, or grant your principal admin "
            f"access on the project.\n"
            f"  upstream: {e}",
            file=sys.stderr,
        )
        sys.exit(4)
    except DatabricksError as e:
        print(f"ERROR: create_role failed: {e}", file=sys.stderr)
        sys.exit(5)


def main() -> int:
    args = parse_args()
    try:
        ws = WorkspaceClient(profile=args.profile)
    except DatabricksError as e:
        print(f"ERROR: cannot authenticate profile '{args.profile}': {e}", file=sys.stderr)
        return 1

    sp_uuid = _resolve_sp(ws, args.app_name)
    parent = f"projects/{args.lakebase_project}/branches/{args.branch}"

    # Confirm the project/branch actually exists before we touch anything.
    try:
        list(ws.postgres.list_endpoints(parent=parent))
    except NotFound:
        print(
            f"ERROR: Lakebase project/branch '{parent}' not found. "
            f"Create the project first (Compute -> Lakebase -> New Project).",
            file=sys.stderr,
        )
        return 3

    print(
        f"[info] target SP: {sp_uuid}\n"
        f"       project:   {args.lakebase_project}/{args.branch}"
    )

    existing = _find_existing_role(ws, parent, sp_uuid)
    if existing is not None:
        if _has_correct_spec(existing):
            print(
                f"[ok]   role for {sp_uuid} already exists with "
                f"LAKEBASE_OAUTH_V1 + DATABRICKS_SUPERUSER. Nothing to do."
            )
            return 0
        # Wrong spec -- typically NO_LOGIN from a previous manual SQL attempt.
        # Recreate cleanly.
        _delete_role(ws, cast(Role, existing))

    _create_role(ws, parent, sp_uuid)

    print(
        f"[ok]   registered {sp_uuid} as Lakebase role with "
        f"LAKEBASE_OAUTH_V1 + DATABRICKS_SUPERUSER\n"
        f"       restart the app so the migration retries:\n"
        f"         databricks apps stop  {args.app_name} --profile {args.profile}\n"
        f"         databricks apps start {args.app_name} --profile {args.profile}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
