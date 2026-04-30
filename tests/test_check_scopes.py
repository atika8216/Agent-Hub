"""Unit tests for scripts/check_scopes.py.

Focus: the YAML list extractor is the only non-obvious piece -- we depend
on it for drift detection and the F5 snapshot, so pin its behavior.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_check_scopes():
    """Dynamically import scripts/check_scopes.py.

    It's not a package member, so we load it via importlib to keep the
    script file both runnable as a script and importable from tests.
    """
    path = Path(__file__).resolve().parent.parent / "scripts" / "check_scopes.py"
    spec = importlib.util.spec_from_file_location("check_scopes", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_scopes"] = module
    spec.loader.exec_module(module)
    return module


check_scopes = _load_check_scopes()


class TestYamlListExtractor:
    def test_extracts_simple_list(self) -> None:
        text = """
foo:
  bar:
    - a
    - b
    - c
"""
        assert check_scopes._extract_list_under_key(text, ["foo", "bar"]) == [
            "a",
            "b",
            "c",
        ]

    def test_handles_comments(self) -> None:
        text = """
# top-level comment
foo:
  # mid
  bar:
    - a  # trailing comment
    - b
"""
        assert check_scopes._extract_list_under_key(text, ["foo", "bar"]) == [
            "a",
            "b",
        ]

    def test_stops_at_sibling_key(self) -> None:
        text = """
foo:
  bar:
    - a
    - b
  baz:
    - c
"""
        assert check_scopes._extract_list_under_key(text, ["foo", "bar"]) == [
            "a",
            "b",
        ]

    def test_returns_empty_on_missing_key(self) -> None:
        text = "foo:\n  bar:\n    - a\n"
        assert check_scopes._extract_list_under_key(text, ["foo", "missing"]) == []

    def test_app_yaml_format(self) -> None:
        """Real-world: app.yaml nests user_authorization.scopes."""
        text = """
command:
  - "python"
user_authorization:
  scopes:
    - serving.serving-endpoints
    - model-serving
    - iam.access-control:workspace
"""
        got = check_scopes._extract_list_under_key(
            text, ["user_authorization", "scopes"]
        )
        assert got == [
            "serving.serving-endpoints",
            "model-serving",
            "iam.access-control:workspace",
        ]

    def test_databricks_yml_deep_nesting(self) -> None:
        text = """
resources:
  apps:
    scgp_agent_hub:
      source_code_path: .
      user_api_scopes:
        - serving.serving-endpoints
        - iam.access-control:workspace
        - sql
"""
        got = check_scopes._extract_list_under_key(
            text,
            ["resources", "apps", "scgp_agent_hub", "user_api_scopes"],
        )
        assert got == [
            "serving.serving-endpoints",
            "iam.access-control:workspace",
            "sql",
        ]


class TestDriftReport:
    def test_aligned_returns_zero(self, capsys) -> None:
        rc = check_scopes.report_diff(["a", "b"], ["a", "b"])
        assert rc == 0

    def test_drift_returns_nonzero(self, capsys) -> None:
        rc = check_scopes.report_diff(["a", "b"], ["a"])
        assert rc == 1

    def test_model_serving_is_accepted_drift(self, capsys) -> None:
        """model-serving in app.yaml only is F2-documented and should NOT trigger drift."""
        rc = check_scopes.report_diff(["a", "model-serving"], ["a"])
        assert rc == 0
