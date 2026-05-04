"""Chart service -- heuristic chart picker + ECharts option builder.

The picker is the half of the system the plan flags as load-bearing:
the rest of the pipeline (Genie fetch, persistence) is plumbing, but
``pick_chart`` is the call that decides what the user actually sees.
A wrong choice is recoverable in the UI (the user can flip to "view as
table"), but a *crash* on a weird column shape is not -- the streaming
chat path would hard-fail.

We use canonical column-shape fixtures that mirror the cases in the
plan:

  - 1 categorical + 1 numeric, low cardinality -> bar
  - 1 categorical + 1 numeric, share/percent semantics -> pie
  - time/date + numeric -> line
  - 2 numerics -> scatter
  - high-cardinality categorical -> table fallback
  - empty rows / empty columns -> table fallback
"""

from __future__ import annotations

from typing import Any

import pytest

from scgp_agent_hub.backend.services import chart_service as cs


# --------------------------------------------------------------------------- #
# Canonical column fixtures
# --------------------------------------------------------------------------- #


def _col(name: str, type_text: str) -> dict[str, str]:
    return {"name": name, "type": type_text}


# --------------------------------------------------------------------------- #
# pick_chart -- the heuristic the UX hinges on.
# --------------------------------------------------------------------------- #


class TestPickChart:
    def test_categorical_plus_numeric_low_card_is_bar(self) -> None:
        cols = [_col("region", "STRING"), _col("revenue", "BIGINT")]
        rows = [["West", 1000], ["East", 800], ["North", 600], ["South", 500]]
        assert cs.pick_chart(cols, rows) == "bar"

    def test_categorical_plus_share_numeric_is_pie(self) -> None:
        # The numeric column is named "share" with low cardinality on
        # the categorical -> the picker prefers pie over bar.
        cols = [_col("segment", "STRING"), _col("share_pct", "DOUBLE")]
        rows = [["A", 0.4], ["B", 0.3], ["C", 0.2], ["D", 0.1]]
        assert cs.pick_chart(cols, rows) == "pie"

    def test_pie_only_when_low_cardinality(self) -> None:
        # 12 distinct categories with share semantics -> too noisy for
        # a pie; the picker should fall back to bar.
        cols = [_col("segment", "STRING"), _col("percent", "DOUBLE")]
        rows = [[f"S{i}", 0.05] for i in range(12)]
        assert cs.pick_chart(cols, rows) == "bar"

    def test_date_plus_numeric_is_line(self) -> None:
        cols = [_col("day", "DATE"), _col("revenue", "BIGINT")]
        rows = [
            ["2026-01-01", 100],
            ["2026-01-02", 110],
            ["2026-01-03", 95],
        ]
        assert cs.pick_chart(cols, rows) == "line"

    def test_timestamp_plus_numeric_is_line(self) -> None:
        cols = [_col("ts", "TIMESTAMP"), _col("count", "INT")]
        rows = [["2026-01-01T00:00:00Z", 1], ["2026-01-01T01:00:00Z", 2]]
        assert cs.pick_chart(cols, rows) == "line"

    def test_two_numerics_is_scatter(self) -> None:
        cols = [_col("x", "DOUBLE"), _col("y", "DOUBLE")]
        rows = [[i, i * 1.5] for i in range(20)]
        assert cs.pick_chart(cols, rows) == "scatter"

    def test_high_cardinality_categorical_falls_back_to_table(self) -> None:
        # >MAX_BAR_CATEGORIES distinct values: a bar would render as a
        # ribbon of unreadable spikes; fall back to table.
        cols = [_col("user_email", "STRING"), _col("logins", "BIGINT")]
        rows = [[f"u{i}@x.com", i] for i in range(cs.MAX_BAR_CATEGORIES + 5)]
        assert cs.pick_chart(cols, rows) == "table"

    def test_empty_columns_is_table(self) -> None:
        assert cs.pick_chart([], [[1]]) == "table"

    def test_empty_rows_is_table(self) -> None:
        # No data to pick from -- the heuristic counts distinct values
        # in the categorical column and 0 distinct -> table fallback.
        cols = [_col("region", "STRING"), _col("revenue", "BIGINT")]
        assert cs.pick_chart(cols, []) == "table"

    def test_three_numeric_cols_with_time_is_multi_series_line(self) -> None:
        cols = [
            _col("day", "DATE"),
            _col("revenue", "DOUBLE"),
            _col("orders", "BIGINT"),
        ]
        rows = [["2026-01-01", 1.0, 2], ["2026-01-02", 1.5, 3]]
        assert cs.pick_chart(cols, rows) == "line"

    def test_three_cols_categorical_with_numerics_is_grouped_bar(self) -> None:
        cols = [
            _col("region", "STRING"),
            _col("q1", "BIGINT"),
            _col("q2", "BIGINT"),
        ]
        rows = [["West", 100, 120], ["East", 80, 95]]
        assert cs.pick_chart(cols, rows) == "bar"

    def test_three_cols_high_card_categorical_is_table(self) -> None:
        cols = [
            _col("user_email", "STRING"),
            _col("revenue", "BIGINT"),
            _col("count", "BIGINT"),
        ]
        rows = [[f"u{i}@x.com", i, i] for i in range(cs.MAX_BAR_CATEGORIES + 1)]
        assert cs.pick_chart(cols, rows) == "table"

    def test_unknown_type_is_treated_as_string(self) -> None:
        # An unfamiliar SQL type (e.g. ARRAY<...>, MAP<...>) is treated
        # as a categorical -> still picks bar when cardinality is low.
        cols = [_col("tag", "ARRAY<STRING>"), _col("count", "BIGINT")]
        rows = [["a", 1], ["b", 2], ["c", 3]]
        assert cs.pick_chart(cols, rows) == "bar"


# --------------------------------------------------------------------------- #
# build_option -- ECharts payload shape contract.
# --------------------------------------------------------------------------- #


class TestBuildOption:
    """ECharts payload shape contract.

    The frontend hands ``option_json`` straight to ``<ReactECharts/>``,
    so the structure is the contract. We assert presence of the
    interactivity bits the plan calls out (tooltip / dataZoom / legend
    / toolbox) plus the data shape per chart kind.
    """

    def _expect_interactivity(self, option: dict[str, Any]) -> None:
        # toolbox is always present for restore + saveAsImage.
        assert isinstance(option.get("toolbox"), dict)
        # palette is set so the FE doesn't fall back to grey defaults.
        assert isinstance(option.get("color"), list) and option["color"]

    def test_bar_two_columns(self) -> None:
        cols = [_col("region", "STRING"), _col("revenue", "BIGINT")]
        rows = [["West", 1000], ["East", 800]]
        opt = cs.build_option("bar", cols, rows, title="Revenue by region")
        self._expect_interactivity(opt)
        assert opt["xAxis"]["data"] == ["West", "East"]
        assert opt["series"][0]["type"] == "bar"
        assert opt["series"][0]["data"] == [1000.0, 800.0]
        assert "dataZoom" in opt
        assert "tooltip" in opt

    def test_line_with_date_uses_time_axis(self) -> None:
        cols = [_col("day", "DATE"), _col("revenue", "BIGINT")]
        rows = [["2026-01-01", 100], ["2026-01-02", 110]]
        opt = cs.build_option("line", cols, rows, title=None)
        assert opt["xAxis"]["type"] == "time"
        # series data should be [x, y] tuples (skipping nulls).
        series = opt["series"][0]
        assert series["type"] == "line"
        assert series["data"] == [["2026-01-01", 100.0], ["2026-01-02", 110.0]]

    def test_pie_payload_shape(self) -> None:
        cols = [_col("seg", "STRING"), _col("share", "DOUBLE")]
        rows = [["A", 0.4], ["B", 0.6]]
        opt = cs.build_option("pie", cols, rows, title="Share by segment")
        series = opt["series"][0]
        assert series["type"] == "pie"
        names = sorted(d["name"] for d in series["data"])
        values = sorted(d["value"] for d in series["data"])
        assert names == ["A", "B"]
        assert values == [0.4, 0.6]
        # Pie uses item-trigger tooltip, not axis cross-pointer.
        assert opt["tooltip"]["trigger"] == "item"

    def test_scatter_filters_non_numeric(self) -> None:
        cols = [_col("x", "DOUBLE"), _col("y", "DOUBLE")]
        rows = [[1, 2], ["bogus", 3], [4, "nope"], [5, 6]]
        opt = cs.build_option("scatter", cols, rows)
        assert opt["series"][0]["type"] == "scatter"
        assert opt["series"][0]["data"] == [[1.0, 2.0], [5.0, 6.0]]

    def test_table_kind_returns_stub_option(self) -> None:
        # The frontend renders a <table>, not an ECharts canvas. We
        # still need a well-formed option so the artifact shape stays
        # uniform on the wire.
        cols = [_col("k", "STRING"), _col("v", "BIGINT")]
        rows = [["a", 1]]
        opt = cs.build_option("table", cols, rows)
        assert opt["xAxis"] == {"show": False}
        assert opt["yAxis"] == {"show": False}
        assert opt["series"] == []

    def test_multi_series_bar_emits_one_series_per_numeric_col(self) -> None:
        cols = [
            _col("region", "STRING"),
            _col("q1", "BIGINT"),
            _col("q2", "BIGINT"),
        ]
        rows = [["W", 1, 2], ["E", 3, 4]]
        opt = cs.build_option("bar", cols, rows)
        assert [s["name"] for s in opt["series"]] == ["q1", "q2"]
        assert opt["series"][0]["data"] == [1.0, 3.0]
        assert opt["series"][1]["data"] == [2.0, 4.0]

    def test_empty_rows_yields_table_stub(self) -> None:
        cols = [_col("k", "STRING"), _col("v", "BIGINT")]
        opt = cs.build_option("bar", cols, [])
        assert opt["series"] == []


# --------------------------------------------------------------------------- #
# build_chart_artifact -- end-to-end pipeline with monkeypatched fetch +
# persist. Important to verify the row cap kicks in.
# --------------------------------------------------------------------------- #


class TestBuildChartArtifact:
    def test_no_rows_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            cs, "fetch_query_result", lambda *_a, **_kw: ([], [], False)
        )
        out = cs.build_chart_artifact(
            None,
            None,
            space_id="s",
            genie_conv_id="c",
            genie_message_id="m",
            attachment_id="a",
            assistant_message_id="msg",
            conversation_id="conv",
        )
        assert out is None

    def test_truncates_to_max_rows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Large result -> the pipeline must cap by ``max_rows`` and set
        # ``truncated=true`` so the FE shows the warning.
        cols = [{"name": "k", "type": "STRING"}, {"name": "v", "type": "INT"}]
        big_rows = [[f"k{i}", i] for i in range(10)]
        monkeypatch.setattr(
            cs, "fetch_query_result", lambda *_a, **_kw: (cols, big_rows, False)
        )
        monkeypatch.setattr(
            cs.feature_flags_service, "chart_max_rows", lambda _s: 3
        )
        captured: dict[str, Any] = {}

        def _persist(_session: Any, **kwargs: Any) -> str:
            captured.update(kwargs)
            return "chart-123"

        monkeypatch.setattr(cs, "persist_artifact", _persist)
        out = cs.build_chart_artifact(
            None,
            None,
            space_id="s",
            genie_conv_id="c",
            genie_message_id="m",
            attachment_id="a",
            assistant_message_id="msg",
            conversation_id="conv",
            title="My chart",
        )
        assert out is not None
        assert out["chart_id"] == "chart-123"
        assert out["truncated"] is True
        assert out["rows_count"] == 3
        # Persist saw the truncated row set, not the full one.
        assert len(captured["rows"]) == 3
        assert captured["truncated"] is True

    def test_genie_truncation_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cols = [{"name": "k", "type": "STRING"}, {"name": "v", "type": "INT"}]
        rows = [["a", 1]]
        # genie_truncated = True even when row count fits under our cap.
        monkeypatch.setattr(
            cs, "fetch_query_result", lambda *_a, **_kw: (cols, rows, True)
        )
        monkeypatch.setattr(
            cs.feature_flags_service, "chart_max_rows", lambda _s: 100
        )
        monkeypatch.setattr(
            cs, "persist_artifact", lambda *_a, **_kw: "chart-9"
        )
        out = cs.build_chart_artifact(
            None,
            None,
            space_id="s",
            genie_conv_id="c",
            genie_message_id="m",
            attachment_id="a",
            assistant_message_id="msg",
            conversation_id="conv",
        )
        assert out is not None
        assert out["truncated"] is True


# --------------------------------------------------------------------------- #
# Numeric coercion edge cases (called by build_option for bar/line/scatter)
# --------------------------------------------------------------------------- #


class TestCoerceNumber:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (None, None),
            (1, 1.0),
            (1.5, 1.5),
            ("3.14", 3.14),
            ("  42 ", 42.0),
            ("", None),
            ("not a number", None),
            (True, 1.0),
            ([1, 2], None),
        ],
    )
    def test_handles_common_inputs(self, raw: Any, expected: Any) -> None:
        assert cs._coerce_number(raw) == expected


# --------------------------------------------------------------------------- #
# _data_zoom_inside_only -- regression against the "slider strip peek".
#
# A prior revision returned both ``inside`` and ``slider`` entries. The
# slider was positioned at ``bottom: 0`` while the chart legend lived at
# ``top: "bottom"``; on a fixed-height card the two overlapped and the
# slider read as a second chart peeking out from under the card. We now
# return inside-only and every chart kind that opts into data zoom must
# inherit that -- drop this test and the regression can sneak back in.
# --------------------------------------------------------------------------- #


class TestDataZoom:
    def test_inside_only(self) -> None:
        entries = cs._data_zoom_inside_only()
        assert entries == [{"type": "inside"}]

    @pytest.mark.parametrize("kind", ["bar", "line", "scatter"])
    def test_build_option_does_not_inject_slider_dataZoom(
        self, kind: str
    ) -> None:
        # Each chart kind that carries dataZoom must inherit inside-only;
        # a slider entry sneaking back in would re-open the overlap bug.
        if kind == "scatter":
            cols = [_col("x", "DOUBLE"), _col("y", "DOUBLE")]
            rows = [[1, 2], [3, 4]]
        else:
            cols = [_col("region", "STRING"), _col("revenue", "BIGINT")]
            rows = [["A", 1], ["B", 2]]
        opt = cs.build_option(kind, cols, rows)  # type: ignore[arg-type]
        zoom = opt.get("dataZoom")
        assert isinstance(zoom, list) and zoom, f"{kind} missing dataZoom"
        assert all(z.get("type") == "inside" for z in zoom), (
            f"{kind} must use inside-only dataZoom, got {zoom}"
        )
        assert not any(z.get("type") == "slider" for z in zoom), (
            f"{kind} accidentally ships a slider dataZoom"
        )

    @pytest.mark.parametrize("kind", ["bar", "line", "scatter"])
    def test_build_option_ships_exactly_one_inside_data_zoom(
        self, kind: str
    ) -> None:
        # Lock the exact shape the frontend normalizer expects on legacy
        # rehydrate. If a future commit appends a slider/clone entry the
        # contract test fires here, not as a visual glitch in production.
        if kind == "scatter":
            cols = [_col("x", "DOUBLE"), _col("y", "DOUBLE")]
            rows = [[1, 2], [3, 4]]
        else:
            cols = [_col("region", "STRING"), _col("revenue", "BIGINT")]
            rows = [["A", 1], ["B", 2]]
        opt = cs.build_option(kind, cols, rows)  # type: ignore[arg-type]
        zoom = opt.get("dataZoom")
        assert zoom == [{"type": "inside"}], (
            f"{kind} dataZoom must equal [{{'type': 'inside'}}], got {zoom}"
        )

    def test_pie_has_no_data_zoom(self) -> None:
        # Pie has no x-axis to zoom -- the builder intentionally leaves
        # ``dataZoom`` off; this asserts the omission stays deliberate.
        cols = [_col("seg", "STRING"), _col("share", "DOUBLE")]
        rows = [["A", 0.4], ["B", 0.6]]
        opt = cs.build_option("pie", cols, rows)
        assert "dataZoom" not in opt


# --------------------------------------------------------------------------- #
# Legend placement -- regression against the "bottom strip peek".
#
# Even after the slider dataZoom was removed, anchoring the legend at
# ``top: "bottom"`` plus ``grid.bottom: 56`` left a visible band under the
# plot that users read as a second mini-chart. The fix anchors the legend
# above the plot (below the title) and shrinks the bottom gutter. These
# tests lock the new placement in so the regression cannot silently return.
# --------------------------------------------------------------------------- #


class TestLegendPlacement:
    def test_pie_legend_is_anchored_above_plot(self) -> None:
        cols = [_col("seg", "STRING"), _col("share", "DOUBLE")]
        rows = [["A", 0.4], ["B", 0.6]]
        opt = cs.build_option("pie", cols, rows)
        legend = opt.get("legend")
        assert isinstance(legend, dict)
        assert legend.get("top") != "bottom", (
            "pie legend must not anchor at the bottom -- regresses "
            "the 'second chart peek' glitch"
        )
        assert isinstance(legend.get("top"), int)

    def test_multi_series_bar_legend_is_anchored_above_plot(self) -> None:
        cols = [
            _col("region", "STRING"),
            _col("q1", "BIGINT"),
            _col("q2", "BIGINT"),
        ]
        rows = [["W", 1, 2], ["E", 3, 4]]
        opt = cs.build_option("bar", cols, rows)
        legend = opt.get("legend")
        assert isinstance(legend, dict)
        assert legend.get("top") != "bottom"
        assert isinstance(legend.get("top"), int)
        assert legend.get("data") == ["q1", "q2"]

    def test_multi_series_line_legend_is_anchored_above_plot(self) -> None:
        cols = [
            _col("day", "DATE"),
            _col("revenue", "DOUBLE"),
            _col("orders", "BIGINT"),
        ]
        rows = [["2026-01-01", 1.0, 2], ["2026-01-02", 1.5, 3]]
        opt = cs.build_option("line", cols, rows)
        legend = opt.get("legend")
        assert isinstance(legend, dict)
        assert legend.get("top") != "bottom"

    @pytest.mark.parametrize("kind", ["bar", "line"])
    def test_single_series_line_or_bar_omits_legend(self, kind: str) -> None:
        # A single-series chart already labels itself via the y-axis
        # name; the legend would be redundant noise above the plot. The
        # builder must skip it to keep the top rail tidy.
        cols = [_col("region", "STRING"), _col("revenue", "BIGINT")]
        rows = [["West", 1000], ["East", 800]]
        opt = cs.build_option(kind, cols, rows)  # type: ignore[arg-type]
        assert "legend" not in opt, (
            f"{kind} with one numeric column should not ship a legend"
        )

    def test_grid_reserves_room_at_top_not_bottom(self) -> None:
        # The grid padding mirrors the legend move: just enough at the
        # top for the legend rail (which now lives at top: 8 because the
        # canvas title was dropped, so 40 px is enough), no dead space
        # at the bottom (32 px covers x-axis tick labels with
        # ``containLabel: True``).
        cols = [_col("region", "STRING"), _col("revenue", "BIGINT")]
        rows = [["A", 1], ["B", 2]]
        opt = cs.build_option("bar", cols, rows)
        grid = opt.get("grid")
        assert isinstance(grid, dict)
        assert grid.get("top") == 40
        assert grid.get("bottom") == 32

    def test_legend_anchored_at_top_8(self) -> None:
        # Legend lives at top: 8 with a right gutter so it never crashes
        # into the toolbox icons (top: 8, right: 16).
        cols = [_col("seg", "STRING"), _col("share", "DOUBLE")]
        rows = [["A", 0.4], ["B", 0.6]]
        opt = cs.build_option("pie", cols, rows)
        legend = opt.get("legend")
        assert isinstance(legend, dict)
        assert legend.get("top") == 8
        assert legend.get("right") == 80, (
            "legend must reserve a right gutter so wide multi-series "
            "labels don't crash into the toolbox icons"
        )

    @pytest.mark.parametrize("kind", ["bar", "line", "pie", "scatter", "table"])
    def test_no_in_canvas_title(self, kind: str) -> None:
        # The HTML card header in echart-card.tsx already shows the
        # title with truncation + metadata. A second in-canvas title
        # wraps onto the toolbox row on long Genie prompts and creates
        # a layout collision. ``build_option`` therefore must not echo
        # the title back into the option payload, regardless of kind.
        if kind == "scatter":
            cols = [_col("x", "DOUBLE"), _col("y", "DOUBLE")]
            rows = [[1, 2], [3, 4]]
        elif kind == "pie":
            cols = [_col("seg", "STRING"), _col("share", "DOUBLE")]
            rows = [["A", 0.4], ["B", 0.6]]
        else:
            cols = [_col("region", "STRING"), _col("revenue", "BIGINT")]
            rows = [["A", 1], ["B", 2]]
        long_title = "Total sales by region (province), including the " * 5
        opt = cs.build_option(kind, cols, rows, title=long_title)  # type: ignore[arg-type]
        # Either absent or the empty-dict sentinel are acceptable; the
        # critical contract is that ECharts has no text to render.
        title = opt.get("title", {})
        assert title == {} or title is None, (
            f"{kind} unexpectedly echoed title into option: {title!r}"
        )
