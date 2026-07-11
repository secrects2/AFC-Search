"""Source health tracker."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from src.database import Database

LOGGER = logging.getLogger(__name__)


class SourceHealthTracker:
    """Track success/failure rates per source and auto-manage cooldowns."""

    def __init__(self, db: Database):
        self._db = db

    def record(self, source: str, platform: str, status: str) -> None:
        """Record an observation result and update source_health table incrementally."""
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        
        # We read the current state first
        records = self._db.get_source_health()
        target = None
        for r in records:
            if r.source_name == source and r.platform == platform:
                target = r
                break
                
        success_count = target.success_count_24h if target else 0
        error_count = target.error_count_24h if target else 0
        blocked_count = target.blocked_count_24h if target else 0
        rate_limited_count = target.rate_limited_count_24h if target else 0
        last_success_at = target.last_success_at if target else ""
        cooldown_until = target.cooldown_until if target else ""
        enabled = target.enabled if target else True

        if status == "success":
            success_count += 1
            last_success_at = now_iso
        elif status == "blocked":
            blocked_count += 1
        elif status == "rate_limited":
            rate_limited_count += 1
        elif status in ("error", "price_unknown", "captcha_required", "traffic_verify"):
            error_count += 1
            
        self._db.upsert_source_health(
            source_name=source,
            platform=platform,
            success_count_24h=success_count,
            error_count_24h=error_count,
            blocked_count_24h=blocked_count,
            rate_limited_count_24h=rate_limited_count,
            last_success_at=last_success_at,
            cooldown_until=cooldown_until,
            enabled=enabled
        )

    def should_skip(self, source: str, platform: str) -> bool:
        """Check if source should be skipped (cooldown or disabled)."""
        records = self._db.get_source_health()
        now_iso = datetime.now(timezone.utc).isoformat()
        
        for r in records:
            if r.source_name == source and r.platform == platform:
                if not r.enabled:
                    return True
                if r.cooldown_until and r.cooldown_until > now_iso:
                    return True
        return False

    def trigger_cooldown(self, source: str, platform: str, minutes: int) -> None:
        """Manually trigger a cooldown for a source."""
        cooldown_until = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(timespec="seconds")
        LOGGER.warning("Triggered %d min cooldown for %s on %s", minutes, source, platform)
        
        # Read existing to preserve counts
        records = self._db.get_source_health()
        target = next((r for r in records if r.source_name == source and r.platform == platform), None)
        
        if target:
            self._db.upsert_source_health(
                source_name=source,
                platform=platform,
                success_count_24h=target.success_count_24h,
                error_count_24h=target.error_count_24h,
                blocked_count_24h=target.blocked_count_24h,
                rate_limited_count_24h=target.rate_limited_count_24h,
                last_success_at=target.last_success_at,
                cooldown_until=cooldown_until,
                enabled=target.enabled
            )
        else:
            self._db.upsert_source_health(
                source_name=source,
                platform=platform,
                cooldown_until=cooldown_until,
            )

    def auto_cooldown_rules(self) -> None:
        """Apply automatic cooldown rules based on stats."""
        records = self._db.get_source_health()
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        
        for r in records:
            if not r.enabled or (r.cooldown_until and r.cooldown_until > now_iso):
                continue
                
            # Rule 1: Shopee direct crawl high block rate -> cooldown 24h
            if r.platform == "shopee" and r.source_name == "direct_html":
                if r.blocked_count_24h > 10:
                    self.trigger_cooldown(r.source_name, r.platform, 1440)
                    
            # Rule 2: PChome high rate limit -> cooldown 60m
            if r.platform == "pchome":
                if r.rate_limited_count_24h > 5:
                    self.trigger_cooldown(r.source_name, r.platform, 60)
