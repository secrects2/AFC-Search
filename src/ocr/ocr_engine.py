from __future__ import annotations

from pathlib import Path


class OCREngine:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.engine_name = self._detect_engine() if enabled else None

    @staticmethod
    def _detect_engine() -> str | None:
        for module_name in ("pytesseract", "easyocr", "paddleocr"):
            try:
                __import__(module_name)
                return module_name
            except Exception:
                continue
        return None

    def read_text(self, image_path: Path) -> tuple[str, str]:
        if not self.enabled:
            return "", "disabled"
        if not self.engine_name:
            return "", "disabled"

        if self.engine_name == "pytesseract":
            try:
                from PIL import Image  # type: ignore
                import pytesseract  # type: ignore

                return pytesseract.image_to_string(Image.open(image_path), lang="chi_tra+eng"), "ok"
            except Exception:
                return "", "ocr_failed"

        return "", "disabled"


def capture_screenshot(url: str, screenshot_path: Path, headless: bool = True) -> tuple[str, str]:
    if not url.lower().startswith(("http://", "https://")):
        return "", "disabled"
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception:
        return "", "disabled"

    try:
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=headless)
            page = browser.new_page(viewport={"width": 1366, "height": 1800})
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.screenshot(path=str(screenshot_path), full_page=True)
            browser.close()
        return str(screenshot_path), "ok"
    except Exception:
        return "", "screenshot_failed"

