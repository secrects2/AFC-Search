"""ScraperAPI integration for bypassing IP blocks.

ScraperAPI provides a simple REST API that handles proxies, CAPTCHAs, and
browser rendering automatically. Free tier: 5000 credits/month.

Sign up: https://www.scraperapi.com/signup (免費，不需信用卡)
"""
from __future__ import annotations

import logging
import time
from urllib.parse import quote

import requests

LOGGER = logging.getLogger(__name__)

SCRAPERAPI_ENDPOINT = "https://api.scraperapi.com"


def fetch_via_scraperapi(
    url: str,
    api_key: str,
    timeout: float = 30,
    render_js: bool = False,
    retries: int = 1,
) -> str:
    """Fetch a page through ScraperAPI.

    Args:
        url: Target URL to fetch.
        api_key: ScraperAPI API key.
        timeout: Request timeout in seconds.
        render_js: Whether to render JavaScript (costs 5 credits vs 1).
        retries: Number of retries.

    Returns:
        HTML text of the page.

    Raises:
        PermissionError: If the page is blocked.
        RuntimeError: If all retries fail.
    """
    params = {
        "api_key": api_key,
        "url": url,
        "country_code": "tw",  # Use Taiwan IP for local e-commerce
    }
    if render_js:
        params["render"] = "true"

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            LOGGER.debug("ScraperAPI fetch [attempt %d]: %s", attempt + 1, url[:60])
            response = requests.get(
                SCRAPERAPI_ENDPOINT,
                params=params,
                timeout=timeout,
                headers={"Accept": "text/html"},
            )
            if response.status_code == 200:
                LOGGER.info("ScraperAPI 成功：%s (%d chars)", url[:50], len(response.text))
                return response.text
            elif response.status_code == 403:
                raise PermissionError(f"ScraperAPI blocked: HTTP {response.status_code}")
            elif response.status_code == 429:
                LOGGER.warning("ScraperAPI 額度用完")
                raise PermissionError("ScraperAPI quota exceeded")
            else:
                raise RuntimeError(f"ScraperAPI error: HTTP {response.status_code}")
        except (PermissionError, RuntimeError):
            raise
        except Exception as exc:
            last_error = exc
            LOGGER.warning("ScraperAPI fetch failed [attempt %d]: %s", attempt + 1, exc)
            if attempt < retries:
                time.sleep(2)

    if last_error:
        raise last_error
    raise RuntimeError(f"ScraperAPI failed for: {url}")
