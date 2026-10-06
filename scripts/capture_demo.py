"""Exercise the running UI and capture factual desktop/mobile screenshots."""

from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "screenshots"
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


def main() -> None:
    password = os.environ.get("KD_DEMO_PASSWORD", "")
    if not password:
        raise SystemExit("KD_DEMO_PASSWORD is required")
    if not EDGE.is_file():
        raise SystemExit(f"Microsoft Edge was not found at {EDGE}")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(EDGE), headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        browser_errors: list[str] = []
        page.on("pageerror", lambda error: browser_errors.append(str(error)))
        page.goto("http://127.0.0.1:8000", wait_until="networkidle")
        page.get_by_label("E-mail").fill("admin@demo.invalid")
        page.get_by_label("Senha").fill(password)
        page.get_by_role("button", name="Entrar no KnowledgeDesk").click()
        page.wait_for_selector("#workspace-select")
        if browser_errors:
            raise RuntimeError(f"browser JavaScript errors: {browser_errors}")
        page.wait_for_selector("#document-rows tr")
        page.wait_for_function("document.querySelectorAll('#document-rows tr').length >= 20")
        mode = page.locator("#mode-pill").inner_text()
        if mode != "RAG":
            raise RuntimeError(f"expected RAG mode, observed {mode!r}")
        rows = page.locator("#document-rows tr").count()
        if rows != 24:
            raise RuntimeError(f"expected 24 authorized admin documents, observed {rows}")
        page.screenshot(path=str(OUTPUT / "library-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(OUTPUT / "library-mobile.png"), full_page=True)
        browser.close()
    print(f"captured authenticated UI with {rows} documents in {mode} mode")


if __name__ == "__main__":
    main()
