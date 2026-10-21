"""Capture README screenshots of the Streamlit flow (replay mode) with Playwright.

Prerequisites:
  pip install -e ".[docs]"
  python -m xlagent app --headless --port 8517      (in another terminal, XLAGENT_REPLAY=1)
Then:
  python scripts/capture_screenshots.py --url http://localhost:8517

Uses the locally installed Microsoft Edge (channel "msedge") so no browser
download is needed; pass --channel chromium after `playwright install chromium`
on other systems.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

OUT = Path(__file__).resolve().parents[1] / "docs" / "img"
log = logging.getLogger("capture")


def _shot(page: Page, name: str, anchor: str | None = None) -> Path:
    """Viewport screenshot with the `anchor` heading at the top (Streamlit scrolls an
    inner container, so full-page screenshots do not work)."""
    path = OUT / f"{name}.png"
    if anchor:
        page.get_by_text(anchor, exact=True).first.evaluate(
            "el => el.scrollIntoView({block: 'start'})"
        )
    page.wait_for_timeout(800)
    page.screenshot(path=str(path))
    log.info("saved %s", path)
    return path


def capture(url: str, channel: str) -> list[Path]:
    OUT.mkdir(parents=True, exist_ok=True)
    shots = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=channel)
        page = browser.new_page(viewport={"width": 1280, "height": 860})
        page.goto(url)
        page.get_by_role("button", name="Load demo example (sales consolidation)").click()
        page.get_by_text("What the agent will see").wait_for()
        shots.append(_shot(page, "app_1_upload_profile"))

        page.get_by_role("button", name="Start the interview").click()
        page.get_by_role("button", name="Send answer").wait_for(timeout=60_000)
        shots.append(_shot(page, "app_2_interview", "3. Interview"))
        page.get_by_role("button", name="Send answer").click()
        page.get_by_text("Should refunds also be listed").wait_for(timeout=60_000)
        page.wait_for_timeout(500)
        page.get_by_role("button", name="Send answer").click()
        page.get_by_role("button", name="Confirm spec").wait_for(timeout=60_000)
        shots.append(_shot(page, "app_3_spec", "4. Confirm the requirements"))

        page.get_by_role("button", name="Confirm spec").click()
        page.get_by_role("button", name="Build my tool").click()
        page.get_by_role("button", name="Download the tool (.zip)").wait_for(timeout=180_000)
        shots.append(_shot(page, "app_4_build_download", "5. Write, check and test the script"))
        browser.close()
    return shots


def make_gif(shots: list[Path], dest: Path) -> None:
    from PIL import Image

    frames = []
    for shot in shots:
        img = Image.open(shot).convert("RGB")
        img.thumbnail((960, 645))
        canvas = Image.new("RGB", (960, 645), "white")
        canvas.paste(img, (0, 0))
        frames.append(canvas.convert("P", palette=Image.Palette.ADAPTIVE))
    frames[0].save(dest, save_all=True, append_images=frames[1:], duration=2500, loop=0)
    log.info("saved %s", dest)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8517")
    parser.add_argument("--channel", default="msedge")
    args = parser.parse_args()
    shots = capture(args.url, args.channel)
    make_gif(shots, OUT / "app_flow.gif")


if __name__ == "__main__":
    main()
