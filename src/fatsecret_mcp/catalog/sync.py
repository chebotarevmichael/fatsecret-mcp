"""Filling a User's catalog from their Food Diary, and checking which Foods FatSecret's catalog lacks.

A sync reads month totals backwards from the current month to find the Diary Days with Food Entries,
then reads only those days. A full sync goes back until six months in a row are empty; an incremental
one re-reads the days since three days before the last sync, which also picks up recent edits.

Requests go through the gateway's own pacing. After error 12 (too many actions) the same request is
sent again after a minute, then two, four… A run sends at most `budget` requests; progress is saved
after every request, so a run that stops for any reason carries on where it stopped.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from functools import partial

import anyio

from fatsecret_mcp.catalog.items import Spread, observation
from fatsecret_mcp.catalog.store import CatalogStore, SyncMode, SyncPhase, SyncState
from fatsecret_mcp.domain.models import Nutrition
from fatsecret_mcp.fatsecret.errors import DailyQuotaExceeded, FatSecretError, NotFound, TooManyActions
from fatsecret_mcp.fatsecret.gateway import AccessToken, FatSecretGateway

DEFAULT_BUDGET = 3000
EMPTY_MONTHS_TO_STOP = 6
INCREMENTAL_OVERLAP = timedelta(days=3)
FIRST_BACKOFF_SECONDS = 60.0
THROTTLE_RETRIES = 6
"""Error 12 this many times in a row for one request stops the run: 1 + 2 + … + 32 minutes of waiting."""

logger = logging.getLogger(__name__)


class SyncStopped(Exception):
    """The run stopped early; what it read is kept and the next run carries on."""


class BudgetReached(SyncStopped):
    def __init__(self, budget: int) -> None:
        super().__init__(f"the budget of {budget} requests for this run is spent")


class StillThrottled(SyncStopped):
    def __init__(self) -> None:
        super().__init__(f"FatSecret kept answering error 12 after {THROTTLE_RETRIES} pauses")


class QuotaReached(SyncStopped):
    def __init__(self) -> None:
        super().__init__("FatSecret's daily quota is used up")


@dataclass
class RunReport:
    requests: int = 0
    """Requests that reached FatSecret, answered or refused."""
    throttled: int = 0
    """Times FatSecret answered error 12."""
    mode: SyncMode | None = None
    resumed: bool = False
    months_read: int = 0
    days_read: int = 0
    spreads: list[Spread] = field(default_factory=list[Spread])
    checked: int = 0
    regional: int = 0
    stopped: str | None = None


class CatalogSync:
    def __init__(
        self,
        fatsecret: FatSecretGateway,
        store: CatalogStore,
        user_id: str,
        token: AccessToken,
        *,
        budget: int = DEFAULT_BUDGET,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
        progress: Callable[[str], None] = lambda _: None,
    ) -> None:
        if budget <= 0:
            raise ValueError("A sync needs a budget of at least one request.")

        self._fatsecret = fatsecret
        self._store = store
        self._user = user_id
        self._token = token
        self._budget = budget
        self._sleep = sleep
        self._progress = progress
        self.report = RunReport()

    async def sync(self, today: date, *, full: bool = False) -> RunReport:
        """Carry on an unfinished sync, or start a new one: full on request or the first time."""
        state = self._store.state(self._user)

        if state is not None and state.phase is not SyncPhase.DONE:
            self.report.resumed = True
            self._progress(f"Carrying on the {state.mode} sync started on {state.started_on}.")
        else:
            state = self._new_state(state, today, full=full)
            self._store.start_days(self._user)
            self._store.save_state(self._user, state)

        self.report.mode = state.mode

        try:
            state = await self._find_days(state, today)
            await self._read_days(state)

            if state.mode is SyncMode.FULL:
                self._store.drop_unfound_days(self._user)

            state = replace(
                state,
                phase=SyncPhase.DONE,
                last_completed_on=state.started_on,
                full_completed=state.full_completed or state.mode is SyncMode.FULL,
            )
            self._store.save_state(self._user, state)
        except SyncStopped as stopped:
            self.report.stopped = str(stopped)
        finally:
            self.report.spreads = self._store.rebuild_items(self._user)

        for spread in self.report.spreads:
            logger.warning(
                "Food %s Serving %s (%s): %d entries give %.0f-%.0f kcal per Serving, median %.0f, "
                "latest %.0f%s",
                spread.food_id,
                spread.serving_id,
                spread.name,
                spread.entries,
                spread.lowest_kcal,
                spread.highest_kcal,
                spread.median_kcal,
                spread.latest_kcal,
                "; needs review" if spread.doubtful else "",
            )

        return self.report

    async def classify(self, today: date) -> RunReport:
        """Check each Food not yet checked: one the catalog can't return (error 106) is regional."""
        unchecked = self._store.unchecked_food_ids(self._user)

        self._progress(f"Checking {len(unchecked)} Foods against FatSecret's catalog.")

        try:
            for food_id in unchecked:
                try:
                    await self._send(partial(self._fatsecret.get_food, food_id))
                    regional = False
                except NotFound:
                    regional = True

                self._store.record_check(food_id, is_regional=regional, checked_on=today)
                self.report.checked += 1
                self.report.regional += int(regional)
        except SyncStopped as stopped:
            self.report.stopped = str(stopped)

        return self.report

    def _new_state(self, previous: SyncState | None, today: date, *, full: bool) -> SyncState:
        this_month = today.replace(day=1)
        last = previous.last_completed_on if previous is not None else None

        if full or previous is None or not previous.full_completed or last is None:
            return SyncState(
                mode=SyncMode.FULL,
                phase=SyncPhase.MONTHS,
                next_month=this_month,
                stop_month=None,
                empty_months=0,
                since=None,
                started_on=today,
                last_completed_on=last,
                full_completed=previous.full_completed if previous is not None else False,
            )

        since = min(last - INCREMENTAL_OVERLAP, today)

        return SyncState(
            mode=SyncMode.INCREMENTAL,
            phase=SyncPhase.MONTHS,
            next_month=this_month,
            stop_month=since.replace(day=1),
            empty_months=0,
            since=since,
            started_on=today,
            last_completed_on=last,
            full_completed=True,
        )

    async def _find_days(self, state: SyncState, today: date) -> SyncState:
        while state.phase is SyncPhase.MONTHS:
            month = state.next_month
            totals = await self._send(partial(self._fatsecret.get_month_totals, self._token, month))
            earliest = state.since or month
            # An all-zero day may only be one whose entries were deleted; it isn't worth a request.
            days = {day for day, total in totals.items() if earliest <= day <= today and total != Nutrition()}

            if state.since is not None:
                # Days kept from before that now have no entries were cleared in the app.
                kept = self._store.days_with_entries(self._user, state.since)
                days |= {day for day in kept if day.replace(day=1) == month}

            self._store.add_days(self._user, days)
            self.report.months_read += 1

            previous_month = (month - timedelta(days=1)).replace(day=1)
            empty_months = 0 if days else state.empty_months + 1

            if state.stop_month is None:
                finished = empty_months >= EMPTY_MONTHS_TO_STOP
            else:
                finished = previous_month < state.stop_month

            state = replace(
                state,
                next_month=previous_month,
                empty_months=empty_months,
                phase=SyncPhase.DAYS if finished else SyncPhase.MONTHS,
            )
            self._store.save_state(self._user, state)
            self._progress(f"{month:%Y-%m}: {len(days)} days with entries")

        return state

    async def _read_days(self, state: SyncState) -> None:
        pending = self._store.pending_days(self._user)

        self._progress(f"Reading {len(pending)} Diary Days.")

        for number, day in enumerate(pending, start=1):
            entries = await self._send(partial(self._fatsecret.list_food_entries, self._token, day))
            observations = [logged for entry in entries if (logged := observation(entry)) is not None]

            self._store.replace_day(self._user, day, observations)
            self.report.days_read += 1

            if number % 25 == 0:
                self._progress(f"{number}/{len(pending)} days read, {self.report.requests} requests")

    async def _send[T](self, request: Callable[[], Awaitable[T]]) -> T:
        """One request within the budget, sent again after a growing pause while FatSecret throttles."""
        backoff = FIRST_BACKOFF_SECONDS

        for _ in range(THROTTLE_RETRIES + 1):
            if self.report.requests >= self._budget:
                raise BudgetReached(self._budget)

            try:
                result = await request()
            except TooManyActions as error:
                if error.sent:
                    self.report.requests += 1
                    self.report.throttled += 1

                self._progress(f"FatSecret: too many actions; waiting {backoff:.0f} s")
                await self._sleep(backoff)
                backoff *= 2
                continue
            except DailyQuotaExceeded as error:
                self.report.requests += int(error.sent)
                raise QuotaReached() from None
            except FatSecretError as error:
                self.report.requests += int(error.sent)
                raise

            self.report.requests += 1

            return result

        raise StillThrottled()
