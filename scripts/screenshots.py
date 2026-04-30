"""Capture a baseline screenshot set for the Phase 3 "Clarity" redesign.

Ten representative routes are visited under the two supported themes
(light / dark) and two viewports (mobile 375x812, desktop 1440x900).
Output is written to ``docs/screenshots/<commit-sha>/`` so before/after
comparisons always live alongside a reviewable Git reference.

This script is a *helper*: it does not run in CI. It assumes the app is
already reachable over HTTP (usually at ``http://localhost:8000`` during
APX dev or at the dev Databricks App URL) and that a Playwright Chromium
install is available::

    uv pip install playwright
    playwright install chromium
    python scripts/screenshots.py --base-url http://localhost:8000 \\
        --cookie "AZ_SESSION=…"

The cookie is only needed against a deployed Databricks App; a local
``bun run dev`` session skips it.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROUTES: list[tuple[str, str]] = [
    ("catalog-grid", "/catalog"),
    ("catalog-filters-mas", "/catalog?kind=mas"),
    ("catalog-filters-genie", "/catalog?kind=genie"),
    ("agent-detail", "/catalog/sample-agent"),
    ("chat-new", "/chat/new?endpoint=sample"),
    ("chat-conversation", "/chat/sample-conversation"),
    ("admin-catalog", "/admin/catalog"),
    ("admin-settings", "/admin/settings"),
    ("theme-toggle-sidebar", "/catalog"),  # ThemeToggle sits in the sidebar footer
    ("empty-catalog", "/catalog?kind=none"),
]

THEMES = ["light", "dark"]
VIEWPORTS: dict[str, tuple[int, int]] = {
    "mobile": (375, 812),
    "desktop": (1440, 900),
}


@dataclass
class Options:
    base_url: str
    out_dir: Path
    cookie: str | None
    theme_cookie_name: str


def _current_commit_sha() -> str:
    """Return the current HEAD sha, or ``"working"`` if we're not in a repo.

    The repo can be absent in Databricks App runtimes (the code lives inside
    the APX bundle) so we degrade gracefully instead of aborting.
    """
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return sha or "working"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "working"


async def _capture(options: Options) -> None:
    # The Playwright import is deferred so ``python scripts/screenshots.py
    # --help`` works even when the dependency isn't installed.
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except ImportError:
        print(
            "Playwright is not installed. Run `uv pip install playwright` "
            "then `playwright install chromium`.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    options.out_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            for theme in THEMES:
                for viewport_name, (w, h) in VIEWPORTS.items():
                    context = await browser.new_context(
                        viewport={"width": w, "height": h},
                        color_scheme=theme,  # type: ignore[arg-type]
                        device_scale_factor=2,
                    )
                    if options.cookie:
                        # A raw ``Cookie`` header is easier to supply than
                        # parsing Set-Cookie; we apply it to every request
                        # via ``extra_http_headers``.
                        await context.set_extra_http_headers(
                            {"Cookie": options.cookie}
                        )
                    # Seed the theme through localStorage so ThemeProvider
                    # doesn't flash the opposite palette before hydrating.
                    await context.add_init_script(
                        f"window.localStorage.setItem('scgp-theme', '{theme}');"
                    )

                    page = await context.new_page()
                    for name, path in ROUTES:
                        url = options.base_url.rstrip("/") + path
                        target = (
                            options.out_dir
                            / f"{name}__{theme}__{viewport_name}.png"
                        )
                        try:
                            await page.goto(url, wait_until="networkidle")
                        except Exception as exc:  # noqa: BLE001 — best-effort
                            print(f"  SKIP {name} @ {url}: {exc}")
                            continue
                        await page.screenshot(path=str(target), full_page=True)
                        print(f"  wrote {target.relative_to(Path.cwd())}")
                    await context.close()
        finally:
            await browser.close()


def parse_args(argv: list[str]) -> Options:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("SCGP_APP_URL", "http://localhost:8000"),
        help="Root URL of the running app (default: SCGP_APP_URL env or localhost:8000)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory (default: docs/screenshots/<sha>/)",
    )
    parser.add_argument(
        "--cookie",
        default=os.environ.get("SCGP_APP_COOKIE"),
        help="Raw Cookie header for authenticated Databricks App captures",
    )
    parser.add_argument(
        "--theme-cookie-name",
        default="scgp-theme",
        help="LocalStorage key used by ThemeProvider (rarely needs changing)",
    )
    ns = parser.parse_args(argv)

    sha = _current_commit_sha()
    out_dir = ns.out_dir or Path("docs/screenshots") / sha
    return Options(
        base_url=ns.base_url,
        out_dir=out_dir,
        cookie=ns.cookie,
        theme_cookie_name=ns.theme_cookie_name,
    )


def main(argv: list[str] | None = None) -> int:
    options = parse_args(argv or sys.argv[1:])
    asyncio.run(_capture(options))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
