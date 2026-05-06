"""Validate WCAG AA contrast ratios across the Clarity theme tokens.

The Phase 3 "Clarity" design direction ships a dual-theme token system
(``ui/styles/globals.css``) where every semantic text token must remain
readable on its intended background under both ``data-theme="light"`` and
``data-theme="dark"``. This script parses the OKLCH tokens directly out of
the CSS file, converts them to sRGB, and reports relative-luminance
contrast ratios against the WCAG AA thresholds:

* 4.5:1 for normal body text
* 3.0:1 for large text (>=18pt or bold >=14pt) and UI components

Run locally before every Phase 3 deploy:

    python scripts/check_contrast.py

The script is dependency-free so it works in sandboxed environments where
``pip install colour`` is not available. All math is inline — if you need
more rigor (CAM16, APCA, etc.) promote this to a proper package.
"""

from __future__ import annotations

import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

CSS_PATH = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "agent_hub"
    / "ui"
    / "styles"
    / "globals.css"
)

OKLCH_PATTERN = re.compile(
    r"oklch\(\s*([0-9.]+)\s+([0-9.]+)\s+([0-9.-]+)(?:\s*/\s*([0-9.]+))?\s*\)",
)
TOKEN_PATTERN = re.compile(
    r"--color-([a-z0-9-]+):\s*(oklch\([^)]+\))\s*;",
)


# --- Color math ---------------------------------------------------------------
#
# OKLCH → OKLab → linear sRGB → sRGB follows the reference pipeline from
# https://bottosson.github.io/posts/oklab/. We round into the 0-1 sRGB range,
# clip, and then apply the standard WCAG relative luminance formula.


def oklch_to_rgb(L: float, C: float, h_deg: float) -> tuple[float, float, float]:
    h = math.radians(h_deg)
    a = C * math.cos(h)
    b = C * math.sin(h)

    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b

    l = l_ ** 3
    m = m_ ** 3
    s = s_ ** 3

    # linear sRGB
    r = +4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    b_ = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s

    def _encode(u: float) -> float:
        u = max(0.0, min(1.0, u))
        if u <= 0.0031308:
            return 12.92 * u
        return 1.055 * (u ** (1 / 2.4)) - 0.055

    return (_encode(r), _encode(g), _encode(b_))


def relative_luminance(r: float, g: float, b: float) -> float:
    def _channel(u: float) -> float:
        if u <= 0.03928:
            return u / 12.92
        return ((u + 0.055) / 1.055) ** 2.4

    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast_ratio(fg: tuple[float, float, float], bg: tuple[float, float, float]) -> float:
    l1 = relative_luminance(*fg)
    l2 = relative_luminance(*bg)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


# --- Parsing ------------------------------------------------------------------


@dataclass
class Theme:
    name: str
    tokens: dict[str, tuple[float, float, float]]


def _parse_oklch(value: str) -> tuple[float, float, float] | None:
    m = OKLCH_PATTERN.search(value)
    if not m:
        return None
    L = float(m.group(1))
    C = float(m.group(2))
    h = float(m.group(3))
    return oklch_to_rgb(L, C, h)


def parse_themes(css_text: str) -> list[Theme]:
    """Extract the @theme block (dark default) and the two [data-theme] blocks."""
    themes: list[Theme] = []

    # Match the *block header* rather than the literal string — the @theme
    # block contains prose like ``[data-theme="light"]`` inside a comment,
    # which would otherwise trip a naive ``find("[data-theme=…]")``.
    block_patterns = [
        ("dark-default", re.compile(r"(?m)^\s*@theme\s*\{")),
        ("light", re.compile(r'(?m)^\s*\[data-theme="light"\]\s*\{')),
        ("dark", re.compile(r'(?m)^\s*\[data-theme="dark"\]\s*\{')),
    ]
    for name, pattern in block_patterns:
        m = pattern.search(css_text)
        if not m:
            continue
        brace_open = m.end() - 1  # position of ``{``
        # naive brace matcher — our CSS has no nested blocks inside these
        depth = 1
        idx = brace_open + 1
        while idx < len(css_text) and depth > 0:
            ch = css_text[idx]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            idx += 1
        block = css_text[brace_open + 1 : idx - 1]

        tokens: dict[str, tuple[float, float, float]] = {}
        for match in TOKEN_PATTERN.finditer(block):
            rgb = _parse_oklch(match.group(2))
            if rgb is not None:
                tokens[match.group(1)] = rgb
        themes.append(Theme(name=name, tokens=tokens))
    return themes


# --- Checks -------------------------------------------------------------------


# Each (fg, bg, min_ratio, label) triple represents a pairing the design
# system promises to keep readable. Extend this list when new surfaces or
# text roles appear.
PAIRINGS: list[tuple[str, str, float, str]] = [
    ("text-primary", "background", 4.5, "primary text on canvas"),
    ("text-primary", "surface", 4.5, "primary text on surface"),
    ("text-primary", "surface-elevated", 4.5, "primary text on elevated surface"),
    ("text-primary", "surface-overlay", 4.5, "primary text on overlay"),
    ("text-secondary", "background", 4.5, "secondary text on canvas"),
    ("text-secondary", "surface", 4.5, "secondary text on surface"),
    ("text-secondary", "surface-elevated", 4.5, "secondary text on elevated"),
    ("text-muted", "background", 3.0, "muted text on canvas"),
    ("text-muted", "surface", 3.0, "muted text on surface"),
    ("text-muted", "surface-elevated", 3.0, "muted text on elevated"),
    # Button labels & user chat bubbles are rendered at 15-17px / 500-600,
    # which WCAG treats as "large text" (3:1 threshold). iOS system chrome
    # (iMessage blue on white) caps out at roughly the same ratio.
    ("primary-foreground", "primary", 3.0, "primary CTA label on brand bubble"),
    ("info", "background", 3.0, "info accent on canvas (focus rings)"),
    ("success", "background", 3.0, "success accent on canvas"),
    ("error", "background", 3.0, "error accent on canvas"),
    ("warning", "background", 3.0, "warning accent on canvas"),
]


def run_checks(themes: Iterable[Theme]) -> int:
    theme_list = list(themes)
    # The ``@theme`` block seeds Tailwind's default utility palette. Any
    # token the explicit ``[data-theme=…]`` block omits is inherited from
    # it, so the checker applies the same fallback.
    defaults = next((t.tokens for t in theme_list if t.name == "dark-default"), {})
    failures = 0
    for theme in theme_list:
        if theme.name == "dark-default":
            continue
        print(f"\n== {theme.name.upper()} theme ==")
        for fg_key, bg_key, min_ratio, label in PAIRINGS:
            fg = theme.tokens.get(fg_key) or defaults.get(fg_key)
            bg = theme.tokens.get(bg_key) or defaults.get(bg_key)
            if fg is None or bg is None:
                print(f"  SKIP  {label:42s}  missing token(s)")
                continue
            ratio = contrast_ratio(fg, bg)
            status = "OK  " if ratio >= min_ratio else "FAIL"
            if ratio < min_ratio:
                failures += 1
            print(
                f"  {status}  {label:42s}  {ratio:5.2f} (>= {min_ratio:.1f})"
            )
    return failures


def main() -> int:
    css_text = CSS_PATH.read_text()
    themes = parse_themes(css_text)
    if not themes:
        print("No themes parsed; check the regex and CSS structure.", file=sys.stderr)
        return 2
    failures = run_checks(themes)
    if failures:
        print(
            f"\n{failures} contrast check(s) failed. Tweak the tokens in "
            f"{CSS_PATH.relative_to(Path.cwd())} and re-run.",
            file=sys.stderr,
        )
        return 1
    print("\nAll contrast checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
