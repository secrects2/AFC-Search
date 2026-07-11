"""Platform rate limiter — enforce per-platform min delays and cooldowns."""
from __future__ import annotations

import logging
import random
import time
from typing import Any
from datetime import datetime, timezone

from src.database import Database
from src.services.source_health import SourceHealthTracker

LOGGER = logging.getLogger(__name__)


class PlatformRateLimiter:
    """Per-platform rate limiting with jitter and cooldown tracking."""

    def __init__(self, config: dict[str, Any], db: Database):
        """
        config format: platform_rate_limits from AppConfig
        """
        self._config = config
        self._db = db
        self._health_tracker = SourceHealthTracker(db)
        self._last_request: dict[str, float] = {}  # platform -> timestamp

    def _get_settings(self, platform: str) -> dict[str, Any]:
        settings = self._config.get(platform)
        if not settings:
            settings = self._config.get("default", {})
        return settings

    def before_request(self, platform: str) -> bool:
        """
        Block until the platform's min_delay + jitter has elapsed.
        Returns True if allowed to proceed, False if in cooldown.
        """
        if not platform:
            return True

        # Check cooldown
        if self._health_tracker.should_skip("direct_html", platform):
            return False

        settings = self._get_settings(platform)
        min_delay = settings.get("min_delay_seconds", 0)
        
        if min_delay:
            jitter = settings.get("random_jitter_seconds", 0)
            delay = min_delay + random.uniform(0, jitter)

            last_time = self._last_request.get(platform, 0.0)
            now = time.time()
            elapsed = now - last_time

            if elapsed < delay:
                wait_time = delay - elapsed
                LOGGER.debug("Rate limit for %s: waiting %.1f seconds", platform, wait_time)
                time.sleep(wait_time)

        self._last_request[platform] = time.time()
        return True

    def on_429(self, platform: str, source: str = "direct_html") -> None:
        """
        Handle HTTP 429 logic. Triggers cooldown.
        Implements specific escalation logic for PChome.
        """
        settings = self._get_settings(platform)
        cooldown_mins = settings.get("cooldown_after_429_minutes", 60)
        
        if platform == "pchome":
            # Check consecutive 429s (if rate_limited_count_24h is >= 3 today)
            records = self._db.get_source_health()
            pchome_record = next((r for r in records if r.platform == "pchome" and r.source_name == source), None)
            
            # Since this is on_429, we'll assume it's about to be recorded or was recorded.
            # We will escalate to 6 hours if rate_limited_count_24h >= 2 (so this one makes it 3)
            # Actually, we can check if it's >= 2 before this one is recorded, or >= 3 if it was already recorded.
            if pchome_record and pchome_record.rate_limited_count_24h >= 2:
                LOGGER.warning("PChome hit 429 multiple times! Escalating cooldown to 6 hours (360 mins)")
                cooldown_mins = 360

        self._health_tracker.trigger_cooldown(source, platform, cooldown_mins)
