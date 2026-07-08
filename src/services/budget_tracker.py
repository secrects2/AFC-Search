"""Budget tracker for search API usage.

Enforces daily and monthly limits on search API calls
by querying the api_usage_logs table.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta

from src.database import Database

LOGGER = logging.getLogger(__name__)


class BudgetExhausted(Exception):
    """Raised when search API budget is exhausted."""
    pass


class BudgetTracker:
    """Track and enforce search API usage budgets."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.daily_budget = int(os.environ.get("SEARCH_DAILY_BUDGET", "20"))
        self.monthly_budget = int(os.environ.get("SEARCH_MONTHLY_BUDGET", "500"))

    @property
    def today_start(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00")

    @property
    def month_start(self) -> str:
        now = datetime.now(timezone.utc)
        return now.replace(day=1).strftime("%Y-%m-%dT00:00:00")

    @property
    def today_used(self) -> int:
        return self.db.get_api_usage_count(since=self.today_start)

    @property
    def month_used(self) -> int:
        return self.db.get_api_usage_count(since=self.month_start)

    @property
    def daily_remaining(self) -> int:
        return max(0, self.daily_budget - self.today_used)

    @property
    def monthly_remaining(self) -> int:
        return max(0, self.monthly_budget - self.month_used)

    def can_search(self) -> bool:
        return self.daily_remaining > 0 and self.monthly_remaining > 0

    def check_budget(self) -> None:
        """Raise BudgetExhausted if budget is exceeded."""
        if self.daily_remaining <= 0:
            raise BudgetExhausted(
                f"每日搜尋預算已用完 ({self.today_used}/{self.daily_budget})"
            )
        if self.monthly_remaining <= 0:
            raise BudgetExhausted(
                f"每月搜尋預算已用完 ({self.month_used}/{self.monthly_budget})"
            )

    def usage_summary(self) -> dict[str, int]:
        return {
            "daily_used": self.today_used,
            "daily_budget": self.daily_budget,
            "daily_remaining": self.daily_remaining,
            "monthly_used": self.month_used,
            "monthly_budget": self.monthly_budget,
            "monthly_remaining": self.monthly_remaining,
        }
