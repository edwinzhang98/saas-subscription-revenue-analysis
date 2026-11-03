"""Screenshot every dashboard page for the README (reports/screenshots/*.png).

    python scripts/screenshots.py            # starts Streamlit on a free port, captures, stops it

Uses Playwright with the locally installed Google Chrome (channel="chrome"), so no browser
download is needed. Each page is captured at full content height, 1440 px wide.
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "screenshots"
PAGES = [
    ("01_overview", ""),
    ("02_data_model", "data_model"),
    ("03_mrr_waterfall", "mrr_waterfall"),
    ("04_nrr_cohorts", "nrr_cohorts"),
    ("05_plan_transitions", "plan_transitions"),
    ("06_expansion_signals", "expansion_signals"),
    ("07_silent_churn", "silent_churn"),
    ("08_recommendations", "recommendations"),
]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_healthy(url: str, timeout: float = 60) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(f"{url}/_stcore/health", timeout=2) as r:
                if r.status == 200:
                    return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError("Streamlit did not start")


def capture(page, url: str, path: Path) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(url, wait_until="networkidle")
    page.wait_for_selector("[data-testid='stMainBlockContainer']", timeout=60_000)
    # wait until Streamlit has finished running the script (no 'running' indicator) and charts are drawn
    page.wait_for_function("() => !document.querySelector('[data-testid=\"stStatusWidget\"] [data-testid=\"stStatusWidgetRunningIcon\"]')",
                           timeout=60_000)
    page.wait_for_timeout(2500)
    height = page.evaluate("""() => {
        const main = document.querySelector('[data-testid="stMain"]') || document.scrollingElement;
        return Math.max(main.scrollHeight, document.body.scrollHeight);
    }""")
    page.set_viewport_size({"width": 1440, "height": int(min(height + 40, 16000))})
    page.wait_for_timeout(1500)
    page.screenshot(path=str(path), full_page=True)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "dashboard/app.py", "--server.port", str(port),
                             "--server.headless", "true"], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        wait_healthy(url)
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(device_scale_factor=1)
            for name, path in PAGES:
                target = OUT / f"{name}.png"
                capture(page, f"{url}/{path}", target)
                print(f"  {target.relative_to(ROOT)}")
            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    main()
