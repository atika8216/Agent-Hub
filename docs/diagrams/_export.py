"""Render the SCGP Agent Hub diagrams to high-resolution PNGs.

Run from the repo root:
    python docs/diagrams/_export.py
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright


DIAGRAMS_DIR = Path(__file__).resolve().parent


JOBS = [
    {
        "html": "scgp-agent-hub-architecture.html",
        "base": "scgp-agent-hub-architecture",
        "viewport": (1480, 980),
    },
    {
        "html": "scgp-agent-hub-journey.html",
        "base": "scgp-agent-hub-journey",
        "viewport": (1600, 1060),
    },
]

SCALES = [2, 4]


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        try:
            for job in JOBS:
                html_path = (DIAGRAMS_DIR / job["html"]).resolve()
                vw, vh = job["viewport"]
                for dpr in SCALES:
                    context = browser.new_context(
                        viewport={"width": vw, "height": vh},
                        device_scale_factor=dpr,
                    )
                    page = context.new_page()
                    page.goto(html_path.as_uri())
                    page.wait_for_load_state("networkidle")
                    out = DIAGRAMS_DIR / f"{job['base']}_{dpr}x.png"
                    page.screenshot(path=str(out), full_page=True)
                    context.close()
                    print(f"wrote {out} ({dpr}x)")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
