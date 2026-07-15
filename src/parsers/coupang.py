"""Coupang parser using the rendered product page when HTTP is blocked."""
from __future__ import annotations

import re
from pathlib import Path

from src.loader import parse_price_value
from src.parsers.base import ParserOutput
from src.parsers.generic import GenericParser


class CoupangParser(GenericParser):
    platform = "coupang"

    def parse(self, url: str, output_dir: Path) -> ParserOutput:
        # Coupang's visible sale price is rendered by JavaScript and direct
        # requests commonly receive HTTP 403, so try the rendered DOM first.
        rendered = self._parse_rendered(url)
        if rendered is not None and rendered.price is not None:
            return rendered

        return super().parse(url, output_dir)

    def _parse_rendered(self, url: str) -> ParserOutput | None:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except Exception:
            return None

        try:
            with sync_playwright() as playwright:
                connected_over_cdp = False
                try:
                    # The user's normal Chrome session can pass Coupang's
                    # browser checks where a fresh headless browser receives
                    # HTTP 403. The scheduled task can still use headless as
                    # a fallback when Chrome is not running.
                    browser = playwright.chromium.connect_over_cdp(
                        "http://127.0.0.1:9222"
                    )
                    connected_over_cdp = True
                except Exception:
                    browser = playwright.chromium.launch(headless=self.config.headless)

                if browser.contexts:
                    context = browser.contexts[0]
                else:
                    context = browser.new_context(
                        viewport={"width": 1366, "height": 1800},
                        locale="zh-TW",
                        timezone_id="Asia/Taipei",
                    )
                page = context.new_page()
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(2000)

                    price = self._extract_visible_price(page)
                    if price is None:
                        return None

                    title = page.title() or ""
                    if " | Coupang" in title:
                        title = title.split(" | Coupang", 1)[0].strip()

                    return ParserOutput(
                        platform=self.platform,
                        url=url,
                        title=title,
                        price=price,
                        parse_status="ok",
                        evidence_text=f"Coupang rendered DOM: {price}",
                        raw_data={
                            "price_source": "coupang_rendered_dom",
                            "rendered_url": page.url,
                            "final_url": page.url,
                        },
                    )
                finally:
                    page.close()
                    if not connected_over_cdp:
                        browser.close()
        except Exception:
            return None

    @staticmethod
    def _extract_visible_price(page) -> float | None:
        """Read the first sale-price element, excluding struck/list prices."""
        selectors = (
            ".price-container-v2 .twc-text-red-700",
            ".price-container .twc-text-red-700",
        )
        for selector in selectors:
            try:
                for element in page.locator(selector).all():
                    price = CoupangParser._parse_price_text(element.inner_text())
                    if price is not None:
                        return price
            except Exception:
                continue

        # Keep a conservative fallback for a minor Coupang class change.
        try:
            container = page.locator(".price-container-v2, .price-container").first
            text = container.inner_text(timeout=3000)
            match = re.search(r"\$\s*([0-9][0-9,]*(?:\.\d+)?)", text)
            if match:
                return parse_price_value(match.group(1))
        except Exception:
            pass
        return None

    @staticmethod
    def _parse_price_text(text: str) -> float | None:
        match = re.search(r"\$\s*([0-9][0-9,]*(?:\.\d+)?)", text or "")
        return parse_price_value(match.group(1)) if match else None
