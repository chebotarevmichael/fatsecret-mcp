"""Keeping the catalog up to date from the server: when asked, and once a day while it runs.

Both carry on the User's last sync, which re-reads the days since three days before it: a few
requests a day. The server never starts the first, full sync; `sync-catalog` does. A refresh asked
for by a tool doesn't wait out error 12, so the tool answers at once; the daily one waits it out.
One lock keeps the two from running at the same time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from zoneinfo import ZoneInfo

import anyio

from fatsecret_mcp.catalog.items import CatalogItem
from fatsecret_mcp.catalog.store import CatalogStore, SyncPhase
from fatsecret_mcp.catalog.sync import THROTTLE_RETRIES, CatalogSync, RunReport
from fatsecret_mcp.domain.diary_day import Clock, today
from fatsecret_mcp.fatsecret.gateway import AccessToken, FatSecretGateway

ASKED_BUDGET = 60
DAILY_BUDGET = 300
CHECK_EVERY_SECONDS = 3600.0
FIRST_CHECK_AFTER_SECONDS = 60.0
"""The daily check waits a minute after the server starts, so it doesn't slow the first tool calls."""

logger = logging.getLogger(__name__)


class NeverSynced(Exception):
    def __init__(self) -> None:
        super().__init__(
            "The catalog hasn't been filled yet. The Operator runs `fatsecret-mcp sync-catalog` once first."
        )


@dataclass(frozen=True)
class Refreshed:
    report: RunReport
    new_items: list[CatalogItem] = field(default_factory=list[CatalogItem])
    """Foods and Servings the catalog didn't have before, most logged first."""


class CatalogRefresher:
    def __init__(
        self,
        store: CatalogStore,
        user_id: str,
        fatsecret: FatSecretGateway,
        token: AccessToken,
        timezone: ZoneInfo,
        clock: Clock,
    ) -> None:
        self._store = store
        self._user_id = user_id
        self._fatsecret = fatsecret
        self._token = token
        self._timezone = timezone
        self._clock = clock
        self._lock = anyio.Lock()

    async def refresh(self) -> Refreshed:
        """Read what's new in the Food Diary now, without waiting out FatSecret's throttling."""
        return await self._refresh(budget=ASKED_BUDGET, throttle_retries=0)

    async def refresh_daily(self) -> None:
        """While the server runs, refresh once each Diary Day; never raises, only logs."""
        await anyio.sleep(FIRST_CHECK_AFTER_SECONDS)

        while True:
            try:
                if self._due():
                    refreshed = await self._refresh(budget=DAILY_BUDGET, throttle_retries=THROTTLE_RETRIES)

                    logger.info(
                        "Catalog refreshed: %d days read, %d new Servings, %d requests",
                        refreshed.report.days_read,
                        len(refreshed.new_items),
                        refreshed.report.requests,
                    )
            except Exception:
                logger.exception("The daily catalog refresh failed")

            await anyio.sleep(CHECK_EVERY_SECONDS)

    def _due(self) -> bool:
        """An unfinished sync, or none yet today, once the first sync was started."""
        state = self._store.state(self._user_id)

        if state is None:
            return False

        return state.phase is not SyncPhase.DONE or state.last_completed_on != self._today()

    async def _refresh(self, *, budget: int, throttle_retries: int) -> Refreshed:
        if self._store.state(self._user_id) is None:
            raise NeverSynced()

        async with self._lock:
            known = {(item.food_id, item.serving_id) for item in self._store.items(self._user_id)}
            sync = CatalogSync(
                self._fatsecret,
                self._store,
                self._user_id,
                self._token,
                budget=budget,
                throttle_retries=throttle_retries,
            )
            report = await sync.sync(self._today())
            items = self._store.items(self._user_id)

        new_items = [item for item in items if (item.food_id, item.serving_id) not in known]

        return Refreshed(report=report, new_items=new_items)

    def _today(self) -> date:
        return today(self._clock(), self._timezone)
