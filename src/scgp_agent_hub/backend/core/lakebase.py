"""Lakebase Autoscaling integration: config, engine, session, and dependency."""

from __future__ import annotations

import os
import threading
from collections.abc import Generator
from contextlib import asynccontextmanager
from typing import Annotated, Any, AsyncGenerator, TypeAlias

from databricks.sdk import WorkspaceClient
from fastapi import FastAPI, Request
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.exc import ProgrammingError
from sqlmodel import Session, text

from ._base import LifespanDependency
from ._config import logger


class DatabaseConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="")

    port: int = Field(default=5432, validation_alias="PGPORT")
    database_name: str = Field(default="databricks_postgres")
    project_id: str = Field(
        default="scgp-agent-hub", validation_alias="LAKEBASE_PROJECT_ID"
    )
    branch_id: str = Field(
        default="production", validation_alias="LAKEBASE_BRANCH_ID"
    )
    pool_size: int = Field(default=10)
    max_overflow: int = Field(default=20)
    pool_timeout: int = Field(default=30)
    pool_recycle: int = Field(default=1800)
    pool_pre_ping: bool = Field(default=True)


def _get_dev_db_port() -> int | None:
    port = os.environ.get("APX_DEV_DB_PORT")
    return int(port) if port else None


def _build_engine_url(
    db_config: DatabaseConfig, ws: WorkspaceClient, dev_port: int | None
) -> str:
    if dev_port:
        logger.info(f"Using local dev database at localhost:{dev_port}")
        username = "postgres"
        password = os.environ.get("APX_DEV_DB_PWD")
        if password is None:
            raise ValueError("APX server didn't provide a password")
        return f"postgresql+psycopg://{username}:{password}@localhost:{dev_port}/postgres?sslmode=disable"

    logger.info(f"Using Lakebase Autoscale project: {db_config.project_id}")
    parent = f"projects/{db_config.project_id}/branches/{db_config.branch_id}"
    endpoints = list(ws.postgres.list_endpoints(parent=parent))
    if not endpoints:
        raise ValueError(f"No endpoints found for {parent}")

    ep_name = endpoints[0].name
    endpoint = ws.postgres.get_endpoint(name=ep_name)
    host = endpoint.status.hosts.host
    username = (
        ws.config.client_id if ws.config.client_id else ws.current_user.me().user_name
    )

    return f"postgresql+psycopg://{username}:@{host}:{db_config.port}/{db_config.database_name}"


def _get_endpoint_name(db_config: DatabaseConfig, ws: WorkspaceClient) -> str:
    parent = f"projects/{db_config.project_id}/branches/{db_config.branch_id}"
    endpoints = list(ws.postgres.list_endpoints(parent=parent))
    return endpoints[0].name if endpoints else ""


def create_db_engine(db_config: DatabaseConfig, ws: WorkspaceClient) -> Engine:
    dev_port = _get_dev_db_port()
    engine_url = _build_engine_url(db_config, ws, dev_port)

    engine_kwargs: dict[str, Any] = {
        "pool_size": db_config.pool_size,
        "max_overflow": db_config.max_overflow,
        "pool_timeout": db_config.pool_timeout,
        "pool_recycle": db_config.pool_recycle,
        "pool_pre_ping": db_config.pool_pre_ping,
    }

    if not dev_port:
        engine_kwargs["connect_args"] = {"sslmode": "require"}

    engine = create_engine(engine_url, **engine_kwargs)

    if not dev_port:
        ep_name = _get_endpoint_name(db_config, ws)

        def before_connect(dialect, conn_rec, cargs, cparams):  # type: ignore[no-untyped-def]
            cred = ws.postgres.generate_database_credential(endpoint=ep_name)
            cparams["password"] = cred.token

        event.listens_for(engine, "do_connect")(before_connect)

    return engine


def validate_db(engine: Engine, db_config: DatabaseConfig) -> None:
    dev_port = _get_dev_db_port()

    if dev_port:
        logger.info(f"Validating local dev database at localhost:{dev_port}")
    else:
        logger.info(f"Validating Lakebase connection to project {db_config.project_id}")

    try:
        with Session(engine) as session:
            session.connection().execute(text("SELECT 1"))
            session.close()
    except Exception as e:
        logger.error("Lakebase validation failed: %s", e, exc_info=True)
        raise ConnectionError(f"Failed to connect to the database: {e}")

    logger.info("Database connection validated successfully")


migration_status: dict[str, Any] = {"done": False, "error": None}


_TABLES_DDL: list[str] = [
    """CREATE TABLE IF NOT EXISTS conversations (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_email    VARCHAR(255) NOT NULL,
        endpoint_name VARCHAR(255) NOT NULL,
        title         VARCHAR(500),
        metadata_json JSONB,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    # Idempotent ALTER for databases created before metadata_json existed.
    # Used by chat_service to track per-conversation state for non-OpenAI
    # backends (e.g. Genie's conversation_id, which is a separate API
    # primitive from our internal conversations.id).
    """ALTER TABLE conversations ADD COLUMN IF NOT EXISTS metadata_json JSONB""",
    """CREATE TABLE IF NOT EXISTS messages (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        role            VARCHAR(20) NOT NULL CHECK (role IN ('user', 'assistant')),
        content         TEXT NOT NULL,
        token_count     INTEGER,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS memory_long_term (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_email    VARCHAR(255) NOT NULL,
        endpoint_name VARCHAR(255) NOT NULL,
        insight       TEXT NOT NULL,
        source_msg_id UUID REFERENCES messages(id) ON DELETE SET NULL,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        expires_at    TIMESTAMPTZ
    )""",
    """CREATE TABLE IF NOT EXISTS catalog_config (
        endpoint_name  VARCHAR(255) PRIMARY KEY,
        display_name   VARCHAR(500),
        description    TEXT,
        agent_type     VARCHAR(50) NOT NULL DEFAULT 'MAS',
        visible        BOOLEAN NOT NULL DEFAULT true,
        owner_email    VARCHAR(255),
        metadata_json  JSONB,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS admin_settings (
        key        VARCHAR(100) PRIMARY KEY,
        value      TEXT NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_by VARCHAR(255)
    )""",
    """CREATE TABLE IF NOT EXISTS user_roles (
        email      VARCHAR(255) PRIMARY KEY,
        role       VARCHAR(20) NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    # Per-user UI preferences. Phase 3 of the agent-hub roadmap introduces
    # a dual-theme (light/dark/system) toggle; the choice is persisted here
    # so it follows the user across devices.
    """CREATE TABLE IF NOT EXISTS user_prefs (
        user_email VARCHAR(255) PRIMARY KEY,
        theme      VARCHAR(10) NOT NULL DEFAULT 'system'
                       CHECK (theme IN ('system', 'light', 'dark')),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    # Per-user opt-outs layered under the admin master switches in
    # admin_settings.feature_flags. Effective enablement = admin master ON
    # AND user has not set the feature override to false. See
    # services.feature_flags_service for the resolution logic.
    """ALTER TABLE user_prefs ADD COLUMN IF NOT EXISTS feature_overrides JSONB""",
    # Cached suggestion bubbles for non-Genie agents (and parsed Genie
    # follow-ups). Keyed on the assistant message id so a conversation
    # reload doesn't re-spend tokens. Source records which path produced
    # the suggestions so admins can audit token usage.
    """CREATE TABLE IF NOT EXISTS suggestions_cache (
        message_id   UUID PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
        suggestions  JSONB NOT NULL,
        source       VARCHAR(20) NOT NULL,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    # ECharts payloads attached to a Genie assistant message. We store the
    # raw rows + columns so the UI can offer a "view as table" toggle and
    # CSV download without an extra Genie roundtrip, plus the prebuilt
    # ECharts option so the chart renders identically on reload.
    """CREATE TABLE IF NOT EXISTS chart_artifacts (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        message_id      UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
        conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        chart_kind      VARCHAR(20) NOT NULL,
        title           VARCHAR(500),
        columns_json    JSONB NOT NULL,
        rows_json       JSONB NOT NULL,
        option_json     JSONB NOT NULL,
        truncated       BOOLEAN NOT NULL DEFAULT false,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    # Per-message chart order. Genie can emit multiple ``query``
    # attachments per turn and we render them as a stacked set; ``idx``
    # lets the UI show them in the same order Genie returned them.
    # Existing rows default to 0 -- that's fine for single-chart
    # messages.
    """ALTER TABLE chart_artifacts ADD COLUMN IF NOT EXISTS idx INTEGER NOT NULL DEFAULT 0""",
    # Per-user, per-agent saved questions. UNIQUE on (user, endpoint, text)
    # de-dupes accidental double-pins; position is honored ASC for the UI
    # rail so users can drag-reorder their pins.
    """CREATE TABLE IF NOT EXISTS pinned_questions (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_email    VARCHAR(255) NOT NULL,
        endpoint_name VARCHAR(255) NOT NULL,
        text          TEXT NOT NULL,
        label         VARCHAR(120),
        position      INTEGER NOT NULL DEFAULT 0,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (user_email, endpoint_name, text)
    )""",
    # Append-only history of pin interactions (create / update / delete /
    # click). Lets the dev team answer "which pinned questions are actually
    # used" without building a dedicated analytics UI. ``pin_id`` is
    # nullable because deletes reference a row that's already gone. We
    # snapshot the text + label at event time so deleted pins remain
    # queryable. Writes are best-effort -- a telemetry failure must never
    # break the user's pin action (see pin_event_service.record_event).
    """CREATE TABLE IF NOT EXISTS pin_events (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_email    VARCHAR(255) NOT NULL,
        endpoint_name VARCHAR(255) NOT NULL,
        pin_id        UUID,
        event_type    VARCHAR(20) NOT NULL
                         CHECK (event_type IN ('create','update','delete','click')),
        text          TEXT,
        label         VARCHAR(120),
        metadata_json JSONB DEFAULT '{}'::jsonb,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
]

_INDEXES_DDL: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations (user_email, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_conversations_endpoint ON conversations (endpoint_name)",
    "CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages (conversation_id, created_at ASC)",
    "CREATE INDEX IF NOT EXISTS idx_memory_user_endpoint ON memory_long_term (user_email, endpoint_name, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_catalog_visible ON catalog_config (visible) WHERE visible = true",
    "CREATE INDEX IF NOT EXISTS idx_chart_artifacts_msg ON chart_artifacts (message_id)",
    "CREATE INDEX IF NOT EXISTS idx_chart_artifacts_msg_idx ON chart_artifacts (message_id, idx ASC, created_at ASC)",
    "CREATE INDEX IF NOT EXISTS idx_pins_user_endpoint ON pinned_questions (user_email, endpoint_name, position ASC, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_pin_events_user ON pin_events (user_email, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_pin_events_endpoint ON pin_events (endpoint_name, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_pin_events_type ON pin_events (event_type, created_at DESC)",
]

# Default feature_flags JSON ships with all three features ``enabled: false``
# (master kill ON) so the production rollout matches the existing
# SCGP_DISABLE_UC_MCP_CHAT pattern: deploy first, admin flips the master
# switch when ready, single SQL flip rolls back without a code revert.
_DEFAULT_FEATURE_FLAGS = (
    '{'
    '"ai_suggestions": {"enabled": false, "default_on": true,'
    ' "models": {"default": "databricks-meta-llama-3-3-70b-instruct"}},'
    '"charts":         {"enabled": false, "default_on": true,'
    ' "max_rows": 5000},'
    '"pinned":         {"enabled": false, "default_on": true,'
    ' "max_per_agent": 30}'
    '}'
)


_SEED_DDL: list[str] = [
    "INSERT INTO admin_settings (key, value) VALUES ('memory_mode', 'short_term') ON CONFLICT (key) DO NOTHING",
    f"INSERT INTO admin_settings (key, value) VALUES ('feature_flags', '{_DEFAULT_FEATURE_FLAGS}') ON CONFLICT (key) DO NOTHING",
]


# One-shot data migrations. Each entry is gated by an ``admin_settings`` flag
# so it runs at most once per database, even across app restarts. The flag
# key MUST be stable; changing it re-runs the migration.
#
# Add entries here when you need a one-time fix to existing rows that can't
# be expressed as idempotent DDL. Prefer small, explicit, auditable SQL.
_DATA_MIGRATIONS: list[tuple[str, str, str]] = [
    # 2026-04-17: when _default_visible_for began returning True for
    # GENIE_SPACE, we flipped the spaces that had been silently hidden
    # by the old default.
    #
    # Design note: we do NOT try to distinguish "admin explicitly hid
    # this" from "was hidden by the old default" because
    # ``catalog_config`` has no `updated_by` column and every discovery
    # run bumps `updated_at`, making any time-based heuristic
    # meaningless. We accept that risk because:
    #   1. The migration flag (admin_settings) gates this to run at most
    #      ONCE per database. Any hides admin performs AFTER this point
    #      are preserved.
    #   2. Prior to this deploy, Genie Spaces were default-hidden and
    #      nobody on the team has run an explicit hide workflow on a
    #      Genie Space -- it was physically impossible for them to see
    #      one to hide it.
    # If you revert this change, also reset the flag key below so a new
    # one-shot migration runs to flip them back. See
    # docs/obo-auth-design.md §14 history note.
    (
        "migration_genie_default_visible_2026_04_17_v2",
        """UPDATE catalog_config
             SET visible = true, updated_at = NOW()
           WHERE agent_type = 'GENIE_SPACE'
             AND visible = false""",
        "Promoted Genie Spaces to default-visible (touched %d rows)",
    ),
    # 2026-04-29: strip persisted Genie progress placeholders from
    # ``messages.content``. Before this deploy, ``_stream_genie`` seeded
    # each assistant row with ``status_prefix`` and saved
    # ``status_prefix + answer_text`` at completion, so transcripts
    # reloaded with ``_Preparing warehouse..._\n\n_Reviewing context..._
    # \n\n_Generating SQL..._\n\n`` in front of the real answer (and
    # sometimes duplicated when Genie re-entered the same status). The
    # chat_service.py persist fix prevents new rows from carrying this
    # noise; this migration cleans the legacy rows once.
    #
    # The regex matches one or more leading italic progress tokens of
    # the form ``_<label>..._\n\n`` where ``<label>`` is one of the
    # known Genie statuses (or the generic ``Status: <value>`` fallback
    # from ``_STATUS_LABELS``). We intentionally anchor on ``^`` and
    # greedily consume the whole prefix block so duplicated entries
    # collapse in a single pass.
    (
        "strip_genie_progress_prefix_2026_04",
        r"""UPDATE messages
               SET content = regexp_replace(
                     content,
                     '^(_(Preparing warehouse|Reviewing context|Generating SQL|Running query|Refreshing results|Submitted|Status: [^_]+)\.\.\._\s*\n\n)+',
                     '',
                     ''
               )
             WHERE role = 'assistant'
               AND content ~ '^_(Preparing warehouse|Reviewing context|Generating SQL|Running query|Refreshing results|Submitted|Status: )'""",
        "Stripped legacy Genie progress prefixes (touched %d rows)",
    ),
]


def _run_data_migrations(engine: Engine) -> None:
    """Run each one-shot data migration in `_DATA_MIGRATIONS` at most once.

    Uses ``admin_settings`` as the migration ledger. Failures are logged and
    do not block startup -- the catalog layer still works, the admin just has
    to toggle visibility by hand until the migration succeeds on a later run.
    """
    for flag_key, sql, log_tmpl in _DATA_MIGRATIONS:
        try:
            with engine.begin() as conn:
                already = conn.execute(
                    text("SELECT 1 FROM admin_settings WHERE key = :k").bindparams(
                        k=flag_key
                    )
                ).one_or_none()
                if already:
                    continue

                result = conn.execute(text(sql))
                rowcount = getattr(result, "rowcount", -1)
                conn.execute(
                    text(
                        "INSERT INTO admin_settings (key, value, updated_by) "
                        "VALUES (:k, :v, 'startup-migration') "
                        "ON CONFLICT (key) DO NOTHING"
                    ).bindparams(k=flag_key, v="applied")
                )
                logger.info("Data migration %s: " + log_tmpl, flag_key, rowcount)
        except Exception as exc:
            logger.warning("Data migration %s skipped: %s", flag_key, exc)


def _run_migrations_bg(engine: Engine) -> None:
    """Create missing tables in a background thread."""
    try:
        for ddl in _TABLES_DDL:
            try:
                with engine.begin() as conn:
                    conn.execute(text(ddl))
            except Exception as table_err:
                logger.warning(f"DDL skipped (may already exist): {table_err}")

        logger.info("All tables ensured via raw DDL")

        for ddl in _INDEXES_DDL:
            try:
                with engine.begin() as conn:
                    conn.execute(text(ddl))
            except Exception as idx_err:
                logger.warning(f"Index DDL skipped: {idx_err}")

        logger.info("All indexes ensured")

        for ddl in _SEED_DDL:
            try:
                with engine.begin() as conn:
                    conn.execute(text(ddl))
            except Exception as seed_err:
                logger.warning(f"Seed DDL skipped: {seed_err}")

        _run_data_migrations(engine)

        migration_status["done"] = True
        logger.info("Database migration completed successfully")
    except Exception as e:
        logger.error(f"Background table creation failed: {e}")
        migration_status["error"] = str(e)


def _start_background_migrations(engine: Engine, ws: WorkspaceClient) -> None:
    """Launch table creation in a daemon thread so startup isn't blocked."""

    def _run() -> None:
        _run_migrations_bg(engine)
        if migration_status["done"]:
            _ensure_admin_user(engine, ws)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    logger.info("Background table creation thread started")


def _ensure_admin_user(engine: Engine, ws: WorkspaceClient) -> None:
    """Insert current user as admin if user_roles table has no admins."""
    try:
        email = ws.current_user.me().user_name
        if not email:
            return
        with engine.begin() as conn:
            has_admin = conn.execute(
                text("SELECT 1 FROM user_roles WHERE role = 'admin' LIMIT 1")
            ).one_or_none()
            if has_admin:
                return
            existing = conn.execute(
                text("SELECT email FROM user_roles WHERE email = :email").bindparams(email=email)
            ).one_or_none()
            if existing:
                conn.execute(
                    text("UPDATE user_roles SET role = 'admin' WHERE email = :email").bindparams(email=email)
                )
            else:
                conn.execute(
                    text("INSERT INTO user_roles (email, role) VALUES (:email, 'admin')").bindparams(email=email)
                )
            logger.info(f"Assigned admin role to {email}")
    except Exception as e:
        logger.warning(f"Admin bootstrap skipped: {e}")


class _LakebaseDependency(LifespanDependency):
    @asynccontextmanager
    async def lifespan(self, app: FastAPI) -> AsyncGenerator[None, None]:
        db_config = DatabaseConfig()  # type: ignore[call-arg]
        ws = app.state.workspace_client

        try:
            engine = create_db_engine(db_config, ws)
            validate_db(engine, db_config)
            _start_background_migrations(engine, ws)
            app.state.engine = engine
            app.state._ws_ref = ws
            logger.info("Lakebase connected successfully")
        except Exception as e:
            logger.warning(
                "Lakebase unavailable (%s). "
                "Database-dependent routes will return 503. "
                "Set APX_DEV_DB_PORT for local Postgres or deploy to Databricks Apps.",
                e,
            )
            app.state.engine = None
            app.state._ws_ref = ws

        yield

        if app.state.engine is not None:
            app.state.engine.dispose()

    @staticmethod
    def __call__(request: Request) -> Generator[Session, None, None]:
        engine = request.app.state.engine
        if engine is None:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=503,
                detail="Database unavailable. Lakebase is not configured.",
            )
        with Session(bind=engine) as session:
            yield session


LakebaseDependency: TypeAlias = Annotated[Session, _LakebaseDependency.depends()]
