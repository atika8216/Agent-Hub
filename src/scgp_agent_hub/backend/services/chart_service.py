"""Chart service -- Genie SQL results -> ECharts artifacts.

Responsibilities, in order of how the streaming pipeline calls them:

1. :func:`fetch_query_result` -- read the SQL rows + schema for a Genie
   ``query`` attachment via the workspace API (``ws.api_client.do``).
2. :func:`pick_chart` -- choose a chart kind from column types and
   cardinality. Heuristic-first; we deliberately avoid an extra LLM
   round-trip here because the heuristics cover ~95% of the dashboarding
   shapes Genie produces and a wrong chart is recoverable client-side
   (the user can switch to "view as table").
3. :func:`build_option` -- emit the full ``ECharts option`` dict that the
   frontend hands straight to ``<ReactECharts option={...} />``. Includes
   ``tooltip``, ``axisPointer``, ``dataZoom``, ``legend``, and ``toolbox``
   so the drill-down interactions in the plan ship without a custom JS
   layer in the frontend.
4. :func:`persist_artifact` -- insert the rows + columns + option into
   ``chart_artifacts`` and return the new ``chart_id``.

The full pipeline ``build_chart_artifact`` ties them together so the
streaming caller has a single entry point.

Design notes:
- We never widen the row count past ``feature_flags.charts.max_rows``.
  When we truncate we set ``truncated=true`` so the frontend can show a
  "showing first N rows" badge.
- ``option_json`` includes full data inline. ECharts can render from a
  dataset reference but inlining is simpler for a one-shot artifact and
  matches how reload-from-DB rehydrates the chart without a second SQL
  fetch.
- The ``table`` chart kind is a deliberate fallback that simply renders
  the rows as a grid. The frontend switches to a ``<table>`` element
  rather than ECharts; we still emit a stub ECharts option so the
  artifact shape stays uniform.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Literal

from databricks.sdk import WorkspaceClient
from sqlmodel import Session, text

from ..core._config import logger
from . import feature_flags_service

ChartKind = Literal["bar", "line", "pie", "scatter", "table"]

# Categorical cap above which a bar chart becomes unreadable. Beyond this
# we fall back to a table.
MAX_BAR_CATEGORIES = 25

# Pie charts read poorly past ~8 slices. We additionally require either an
# explicit "share / percent" semantic in the column name OR a small slice
# count to switch from bar -> pie.
MAX_PIE_CATEGORIES = 8

# Tolerant matchers for column type strings emitted by the SQL engine.
# Genie/DBSQL returns strings like "INT", "BIGINT", "DOUBLE", "DECIMAL(10,2)",
# "TIMESTAMP", "DATE", "STRING", "VARCHAR(255)" -- regexes keep this loose.
_NUMERIC_RE = re.compile(
    r"^(?:tinyint|smallint|int|integer|bigint|long|short|byte|float|double|real|decimal|numeric)\b",
    re.IGNORECASE,
)
_DATETIME_RE = re.compile(r"^(?:date|timestamp|time)\b", re.IGNORECASE)
_BOOLEAN_RE = re.compile(r"^bool", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Genie SQL result fetch
# --------------------------------------------------------------------------- #


def fetch_query_result(
    ws: WorkspaceClient,
    *,
    space_id: str,
    genie_conv_id: str,
    message_id: str,
    attachment_id: str,
) -> tuple[list[dict[str, str]], list[list[Any]], bool]:
    """Fetch the SQL execution result for a Genie ``query`` attachment.

    Returns ``(columns, rows, truncated_by_genie)`` where ``columns`` is a
    list of ``{"name": ..., "type": ...}`` dicts and ``rows`` is a 2D
    list. ``truncated_by_genie`` reflects the upstream
    ``manifest.truncated`` flag (genuine warehouse-side truncation, not
    our row cap which is applied separately).

    Returns ``([], [], False)`` on any failure -- chart rendering is a
    best-effort enhancement and must never break the chat path.
    """
    if not (space_id and genie_conv_id and message_id and attachment_id):
        return [], [], False
    path = (
        f"/api/2.0/genie/spaces/{space_id}/conversations/{genie_conv_id}"
        f"/messages/{message_id}/attachments/{attachment_id}/query-result"
    )
    try:
        resp = ws.api_client.do("GET", path)
    except Exception as e:
        logger.info("genie query-result fetch failed (%s): %s", path, e)
        return [], [], False
    if not isinstance(resp, dict):
        return [], [], False

    statement = resp.get("statement_response") or resp
    manifest = (statement or {}).get("manifest") or {}
    schema = (manifest or {}).get("schema") or {}
    raw_cols = schema.get("columns") or []
    columns: list[dict[str, str]] = []
    if isinstance(raw_cols, list):
        for c in raw_cols:
            if not isinstance(c, dict):
                continue
            name = str(c.get("name") or "").strip()
            if not name:
                continue
            type_text = str(
                c.get("type_text")
                or c.get("type_name")
                or c.get("type")
                or ""
            ).strip()
            columns.append({"name": name, "type": type_text})

    result = (statement or {}).get("result") or {}
    rows_raw = result.get("data_array")
    rows: list[list[Any]] = []
    if isinstance(rows_raw, list):
        for r in rows_raw:
            if isinstance(r, list):
                rows.append(list(r))

    truncated = bool(manifest.get("truncated") or False)
    return columns, rows, truncated


# --------------------------------------------------------------------------- #
# Heuristic chart picker
# --------------------------------------------------------------------------- #


def _classify_column(type_text: str) -> Literal["numeric", "datetime", "boolean", "string"]:
    """Bucket a SQL type string into a chart-relevant category."""
    if not type_text:
        return "string"
    if _DATETIME_RE.match(type_text):
        return "datetime"
    if _NUMERIC_RE.match(type_text):
        return "numeric"
    if _BOOLEAN_RE.match(type_text):
        return "boolean"
    return "string"


def _looks_like_share(name: str) -> bool:
    """Heuristic: column name suggests a share/percent/proportion column."""
    n = (name or "").lower()
    return any(
        token in n
        for token in ("pct", "percent", "share", "ratio", "proportion", "%")
    )


def _coerce_number(v: Any) -> float | None:
    """Best-effort numeric coercion. Returns None when not numeric."""
    if v is None:
        return None
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def pick_chart(
    columns: list[dict[str, str]],
    rows: list[list[Any]],
) -> ChartKind:
    """Choose a chart kind from the column shape + a row sample.

    Rules (first match wins):
      1. 2 columns, time/date + numeric              -> line
      2. 2 columns, categorical + numeric, ≤8 cats,
         and the numeric column name suggests share  -> pie
      3. 2 columns, categorical + numeric, ≤25 cats  -> bar
      4. 2 columns, both numeric                     -> scatter
      5. 3+ columns, time + multiple numerics        -> line (multi-series)
      6. anything else                               -> table
    """
    if not columns:
        return "table"
    classes = [_classify_column(c.get("type", "")) for c in columns]
    n_cols = len(columns)

    if n_cols == 2:
        a, b = classes[0], classes[1]
        # Datetime/date paired with numeric -> line
        if (a == "datetime" and b == "numeric") or (a == "numeric" and b == "datetime"):
            return "line"
        # Categorical + numeric
        cat_idx, num_idx = (0, 1) if b == "numeric" else (1, 0) if a == "numeric" else (None, None)
        if cat_idx is not None and num_idx is not None and classes[cat_idx] in ("string", "boolean"):
            distinct = _distinct_count(rows, cat_idx)
            if distinct == 0:
                return "table"
            num_name = columns[num_idx].get("name", "")
            if distinct <= MAX_PIE_CATEGORIES and _looks_like_share(num_name):
                return "pie"
            if distinct <= MAX_BAR_CATEGORIES:
                return "bar"
            return "table"
        if a == "numeric" and b == "numeric":
            return "scatter"
        return "table"

    if n_cols >= 3:
        # Multi-series line: first column is time/date, all remaining are numeric.
        if classes[0] == "datetime" and all(c == "numeric" for c in classes[1:]):
            return "line"
        # First column categorical, rest numeric, low cardinality -> grouped bar.
        if (
            classes[0] in ("string", "boolean")
            and all(c == "numeric" for c in classes[1:])
            and _distinct_count(rows, 0) <= MAX_BAR_CATEGORIES
        ):
            return "bar"

    return "table"


def _distinct_count(rows: list[list[Any]], col_idx: int) -> int:
    seen: set[Any] = set()
    for r in rows:
        if 0 <= col_idx < len(r):
            try:
                seen.add(r[col_idx])
            except TypeError:
                # Unhashable (e.g. list / dict cell) -- skip.
                continue
    return len(seen)


# --------------------------------------------------------------------------- #
# ECharts option builder
# --------------------------------------------------------------------------- #


_DEFAULT_PALETTE = [
    "#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de",
    "#3ba272", "#fc8452", "#9a60b4", "#ea7ccc",
]


def _common_toolbox() -> dict[str, Any]:
    """ECharts toolbox with restore + save-as-image. Drill-down sugar."""
    return {
        "feature": {
            "dataZoom": {"yAxisIndex": "none"},
            "restore": {},
            "saveAsImage": {},
        },
        "right": 16,
        "top": 8,
    }


def _common_tooltip() -> dict[str, Any]:
    """Cross-axis tooltip + axisPointer for crisp value reads on hover."""
    return {
        "trigger": "axis",
        "axisPointer": {"type": "cross"},
        "confine": True,
    }


def _data_zoom_inside_only() -> list[dict[str, Any]]:
    """Inside zoom only.

    A prior revision also returned a ``slider`` variant rendered at
    ``bottom: 0`` with a fixed ``height: 16`` -- but the legend lives at
    ``top: "bottom"`` in the ECharts grid, so the two elements overlapped
    and the slider strip read as "a second chart peeking from under
    the card" in the rendered card. Wheel / trackpad / touch drag still
    zoom via the ``inside`` entry, so we lose nothing visual.
    """
    return [{"type": "inside"}]


def build_option(
    chart_kind: ChartKind,
    columns: list[dict[str, str]],
    rows: list[list[Any]],
    *,
    title: str | None = None,
) -> dict[str, Any]:
    """Build a complete ECharts ``option`` dict for ``chart_kind``."""
    safe_title = (title or "").strip()
    base: dict[str, Any] = {
        "color": list(_DEFAULT_PALETTE),
        "title": (
            {"text": safe_title, "left": "center", "top": 4, "textStyle": {"fontSize": 14}}
            if safe_title
            else {}
        ),
        "grid": {"left": 48, "right": 24, "top": 48, "bottom": 56, "containLabel": True},
        "toolbox": _common_toolbox(),
    }

    if chart_kind == "table" or not columns or not rows:
        # Stub option -- the frontend renders a <table> instead of an
        # ECharts canvas for this kind. We keep ``columns`` + ``rows`` in
        # the artifact (separately persisted) so the UI never has to
        # reach into ``option_json`` for the data.
        base["xAxis"] = {"show": False}
        base["yAxis"] = {"show": False}
        base["series"] = []
        return base

    if chart_kind == "pie":
        # Two-column expectation from pick_chart.
        cat_idx, num_idx = _two_col_indices(columns)
        slices = []
        for r in rows[:MAX_PIE_CATEGORIES * 2]:  # safety cap
            if cat_idx >= len(r) or num_idx >= len(r):
                continue
            v = _coerce_number(r[num_idx])
            if v is None:
                continue
            slices.append({"name": str(r[cat_idx]), "value": v})
        return {
            **base,
            "tooltip": {"trigger": "item", "confine": True},
            "legend": {"orient": "horizontal", "top": "bottom"},
            "series": [
                {
                    "type": "pie",
                    "radius": ["40%", "70%"],
                    "avoidLabelOverlap": True,
                    "data": slices,
                    "label": {"show": True, "formatter": "{b}: {d}%"},
                }
            ],
        }

    if chart_kind == "scatter":
        x_idx, y_idx = 0, 1
        data = []
        for r in rows:
            if len(r) < 2:
                continue
            x = _coerce_number(r[x_idx])
            y = _coerce_number(r[y_idx])
            if x is None or y is None:
                continue
            data.append([x, y])
        return {
            **base,
            "tooltip": _common_tooltip(),
            "xAxis": {"type": "value", "name": columns[x_idx].get("name", "x")},
            "yAxis": {"type": "value", "name": columns[y_idx].get("name", "y")},
            "dataZoom": _data_zoom_inside_only(),
            "series": [
                {
                    "type": "scatter",
                    "name": f"{columns[y_idx].get('name', 'y')} vs {columns[x_idx].get('name', 'x')}",
                    "data": data,
                    "symbolSize": 8,
                }
            ],
        }

    if chart_kind == "line":
        return _build_line_or_bar(base, columns, rows, kind="line")
    if chart_kind == "bar":
        return _build_line_or_bar(base, columns, rows, kind="bar")

    # Shouldn't reach here -- table is handled above and ChartKind is closed.
    base["xAxis"] = {"show": False}
    base["yAxis"] = {"show": False}
    base["series"] = []
    return base


def _two_col_indices(columns: list[dict[str, str]]) -> tuple[int, int]:
    """Return ``(categorical_idx, numeric_idx)`` for a 2-column layout.

    Falls back to ``(0, 1)`` when classification is ambiguous.
    """
    if len(columns) < 2:
        return 0, 1
    a = _classify_column(columns[0].get("type", ""))
    b = _classify_column(columns[1].get("type", ""))
    if a == "numeric" and b != "numeric":
        return 1, 0
    return 0, 1


def _build_line_or_bar(
    base: dict[str, Any],
    columns: list[dict[str, str]],
    rows: list[list[Any]],
    *,
    kind: Literal["line", "bar"],
) -> dict[str, Any]:
    """Shared option builder for line and bar.

    - 2 columns: single series, x = first column, y = second.
    - 3+ columns: multi-series, x = first column, y = each numeric column
      after the first as a separate series.
    """
    classes = [_classify_column(c.get("type", "")) for c in columns]
    n_cols = len(columns)

    if n_cols == 2:
        x_vals: list[Any] = [r[0] if len(r) > 0 else None for r in rows]
        y_vals: list[float | None] = [_coerce_number(r[1]) if len(r) > 1 else None for r in rows]
        x_axis_type = "category" if classes[0] in ("string", "boolean") else (
            "time" if classes[0] == "datetime" else "value"
        )
        x_axis: dict[str, Any] = {"name": columns[0].get("name", ""), "type": x_axis_type}
        if x_axis_type == "category":
            x_axis["data"] = [None if v is None else str(v) for v in x_vals]
            series_data: list[Any] = y_vals
        else:
            series_data = [
                [x_vals[i], y_vals[i]]
                for i in range(len(x_vals))
                if y_vals[i] is not None
            ]
        return {
            **base,
            "tooltip": _common_tooltip(),
            "legend": {"data": [columns[1].get("name", "")], "top": "bottom"},
            "xAxis": x_axis,
            "yAxis": {"type": "value", "name": columns[1].get("name", "")},
            "dataZoom": _data_zoom_inside_only(),
            "series": [
                {
                    "name": columns[1].get("name", ""),
                    "type": kind,
                    "data": series_data,
                    "smooth": kind == "line",
                    "areaStyle": {} if kind == "line" else None,
                    "barMaxWidth": 36 if kind == "bar" else None,
                }
            ],
        }

    # Multi-series.
    x_vals = [r[0] if len(r) > 0 else None for r in rows]
    series_names = [columns[i].get("name", f"series_{i}") for i in range(1, n_cols)]
    x_axis_type = "category" if classes[0] in ("string", "boolean") else (
        "time" if classes[0] == "datetime" else "value"
    )
    x_axis = {"name": columns[0].get("name", ""), "type": x_axis_type}
    if x_axis_type == "category":
        x_axis["data"] = [None if v is None else str(v) for v in x_vals]

    series: list[dict[str, Any]] = []
    for s_idx in range(1, n_cols):
        y_vals = [_coerce_number(r[s_idx]) if len(r) > s_idx else None for r in rows]
        if x_axis_type == "category":
            data = y_vals
        else:
            data = [
                [x_vals[i], y_vals[i]]
                for i in range(len(x_vals))
                if y_vals[i] is not None
            ]
        series.append(
            {
                "name": columns[s_idx].get("name", f"series_{s_idx}"),
                "type": kind,
                "data": data,
                "smooth": kind == "line",
                "barMaxWidth": 36 if kind == "bar" else None,
            }
        )

    return {
        **base,
        "tooltip": _common_tooltip(),
        "legend": {"data": series_names, "top": "bottom"},
        "xAxis": x_axis,
        "yAxis": {"type": "value"},
        "dataZoom": _data_zoom_inside_only(),
        "series": series,
    }


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def persist_artifact(
    session: Session,
    *,
    message_id: str,
    conversation_id: str,
    chart_kind: ChartKind,
    title: str | None,
    columns: list[dict[str, str]],
    rows: list[list[Any]],
    option: dict[str, Any],
    truncated: bool,
    idx: int = 0,
) -> str:
    """Insert a row into ``chart_artifacts`` and return the new ``chart_id``.

    ``idx`` is the 0-based render order within a message. Single-chart
    messages can leave it at the default. The ``idx`` column is added
    by an idempotent ``ALTER TABLE`` in lakebase.py.
    """
    chart_id = str(uuid.uuid4())
    try:
        session.exec(
            text(
                """INSERT INTO chart_artifacts
                    (id, message_id, conversation_id, chart_kind,
                     title, columns_json, rows_json, option_json, truncated, idx)
                   VALUES (CAST(:cid AS uuid), CAST(:mid AS uuid), CAST(:conv AS uuid),
                           :kind, :title,
                           CAST(:cols AS jsonb), CAST(:rows AS jsonb), CAST(:opt AS jsonb),
                           :trunc, :idx)"""
            ).bindparams(
                cid=chart_id,
                mid=message_id,
                conv=conversation_id,
                kind=chart_kind,
                title=(title or "")[:500] or None,
                cols=json.dumps(columns),
                rows=json.dumps(rows, default=str),
                opt=json.dumps(option, default=str),
                trunc=truncated,
                idx=int(idx),
            )
        )
        session.commit()
    except Exception as e:
        logger.warning("chart_artifacts insert failed for msg=%s: %s", message_id, e)
        try:
            session.rollback()
        except Exception:
            pass
        return ""
    return chart_id


def get_artifact(session: Session, message_id: str) -> dict[str, Any] | None:
    """Return the first chart artifact for ``message_id`` or None.

    Back-compat helper for the legacy ``GET /messages/{id}/chart`` route
    and reloads that only hydrate a single chart per message. Orders by
    ``idx ASC, created_at ASC`` so multi-chart messages consistently
    surface the primary chart (idx=0) here. For the full list use
    :func:`list_artifacts`.
    """
    if not message_id:
        return None
    try:
        row = session.exec(
            text(
                """SELECT id, message_id, conversation_id, chart_kind, title,
                          columns_json, rows_json, option_json, truncated, created_at, idx
                   FROM chart_artifacts
                   WHERE message_id = CAST(:mid AS uuid)
                   ORDER BY idx ASC, created_at ASC
                   LIMIT 1"""
            ).bindparams(mid=str(message_id))
        ).one_or_none()
    except Exception as e:
        logger.warning("chart_artifacts read failed for msg=%s: %s", message_id, e)
        return None
    if not row:
        return None
    return {
        "id": str(row[0]),
        "message_id": str(row[1]),
        "conversation_id": str(row[2]),
        "chart_kind": str(row[3]),
        "title": str(row[4]) if row[4] is not None else "",
        "columns": _decode_json(row[5], default=[]),
        "rows": _decode_json(row[6], default=[]),
        "option": _decode_json(row[7], default={}),
        "truncated": bool(row[8]),
        "created_at": row[9],
        "idx": int(row[10]) if row[10] is not None else 0,
    }


def list_artifacts(session: Session, message_id: str) -> list[dict[str, Any]]:
    """Return every chart artifact for ``message_id`` in render order.

    Ordered by ``idx ASC, created_at ASC`` so the primary chart always
    comes first and retries / reruns slot in after. Returns ``[]`` on
    missing message or DB error -- callers treat this as "no charts".
    """
    if not message_id:
        return []
    try:
        rows = session.exec(
            text(
                """SELECT id, message_id, conversation_id, chart_kind, title,
                          columns_json, rows_json, option_json, truncated, created_at, idx
                   FROM chart_artifacts
                   WHERE message_id = CAST(:mid AS uuid)
                   ORDER BY idx ASC, created_at ASC"""
            ).bindparams(mid=str(message_id))
        ).all()
    except Exception as e:
        logger.warning("chart_artifacts list failed for msg=%s: %s", message_id, e)
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "id": str(row[0]),
                "message_id": str(row[1]),
                "conversation_id": str(row[2]),
                "chart_kind": str(row[3]),
                "title": str(row[4]) if row[4] is not None else "",
                "columns": _decode_json(row[5], default=[]),
                "rows": _decode_json(row[6], default=[]),
                "option": _decode_json(row[7], default={}),
                "truncated": bool(row[8]),
                "created_at": row[9],
                "idx": int(row[10]) if row[10] is not None else 0,
            }
        )
    return out


def count_artifacts(session: Session, message_id: str) -> int:
    """Return the number of chart artifacts attached to ``message_id``.

    Used by ``MessageOut.chart_count`` so clients can skip the artifact
    fetch when the number is zero.
    """
    if not message_id:
        return 0
    try:
        row = session.exec(
            text(
                "SELECT COUNT(*) FROM chart_artifacts WHERE message_id = CAST(:mid AS uuid)"
            ).bindparams(mid=str(message_id))
        ).one_or_none()
    except Exception as e:
        logger.warning("chart_artifacts count failed for msg=%s: %s", message_id, e)
        return 0
    if not row:
        return 0
    value = row[0]
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def _decode_json(raw: Any, *, default: Any) -> Any:
    if raw is None:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except (json.JSONDecodeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# End-to-end pipeline
# --------------------------------------------------------------------------- #


def build_chart_artifact(
    ws: WorkspaceClient,
    session: Session,
    *,
    space_id: str,
    genie_conv_id: str,
    genie_message_id: str,
    attachment_id: str,
    assistant_message_id: str,
    conversation_id: str,
    title: str | None = None,
    idx: int = 0,
) -> dict[str, Any] | None:
    """Run the full Genie -> ECharts -> persist pipeline.

    Returns ``{"chart_id": ..., "kind": ..., "option": ..., "truncated": ...}``
    on success, or ``None`` when there's nothing to chart (no rows / no
    columns / fetch failed). Callers emit the SSE ``chart`` event from
    this dict.

    ``idx`` is the 0-based render order within a message. When Genie
    returns multiple ``query`` attachments per turn, the caller iterates
    and passes the attachment position here so the UI can render them in
    a stable stacked order.
    """
    columns, rows, genie_truncated = fetch_query_result(
        ws,
        space_id=space_id,
        genie_conv_id=genie_conv_id,
        message_id=genie_message_id,
        attachment_id=attachment_id,
    )
    if not columns or not rows:
        return None

    max_rows = feature_flags_service.chart_max_rows(session)
    truncated_by_us = False
    if len(rows) > max_rows:
        rows = rows[:max_rows]
        truncated_by_us = True
    truncated = bool(genie_truncated or truncated_by_us)

    kind = pick_chart(columns, rows)
    option = build_option(kind, columns, rows, title=title)

    chart_id = persist_artifact(
        session,
        message_id=assistant_message_id,
        conversation_id=conversation_id,
        chart_kind=kind,
        title=title,
        columns=columns,
        rows=rows,
        option=option,
        truncated=truncated,
        idx=idx,
    )
    if not chart_id:
        return None

    return {
        "chart_id": chart_id,
        "kind": kind,
        "option": option,
        "title": title or "",
        "columns": columns,
        "rows_count": len(rows),
        "truncated": truncated,
        "idx": idx,
    }
