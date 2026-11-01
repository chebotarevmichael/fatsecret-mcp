"""Refreshing the catalog from the server: on request without waiting, and once a Diary Day."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime

import pytest

from fatsecret_mcp.catalog.freshness import CatalogRefresher, NeverSynced
from fatsecret_mcp.catalog.store import CatalogStore
from fatsecret_mcp.catalog.sync import CatalogSync
from fatsecret_mcp.config.models import Household
from fatsecret_mcp.domain.models import Meal
from fatsecret_mcp.fatsecret.gateway import AccessToken
from tests.conftest import FakeClock
from tests.fatsecret.fake import FakeFatSecret, food_entry

pytestmark = pytest.mark.anyio
TODAY = date(2026, 10, 14)


async def no_wait(_: float) -> None: ...


@pytest.fixture
def user(household: Household) -> AccessToken:
    return household.user("jakub").fatsecret


@pytest.fixture
def store() -> CatalogStore:
    return CatalogStore(sqlite3.connect(":memory:"))


@pytest.fixture
def refresher(
    store: CatalogStore, fatsecret: FakeFatSecret, user: AccessToken, household: Household, clock: FakeClock
) -> CatalogRefresher:
    return CatalogRefresher(store, "jakub", fatsecret, user, household.user("jakub").timezone, clock)


def log(fatsecret: FakeFatSecret, user: AccessToken, name: str, day: date) -> None:
    entry = food_entry(name, Meal.SNACKS, day, kcal=100, amount="100 g", number_of_units=1, food_id=name)
    fatsecret.add_food_entry(user, entry)


async def first_sync(fatsecret: FakeFatSecret, store: CatalogStore, user: AccessToken, day: date) -> None:
    await CatalogSync(fatsecret, store, "jakub", user, sleep=no_wait).sync(day)


async def test_a_refresh_reads_recent_days_and_lists_new_foods(
    refresher: CatalogRefresher, fatsecret: FakeFatSecret, store: CatalogStore, user: AccessToken
) -> None:
    log(fatsecret, user, "Kurt", date(2026, 10, 1))
    await first_sync(fatsecret, store, user, date(2026, 10, 12))
    log(fatsecret, user, "Ayran", date(2026, 10, 13))
    before = fatsecret.requests

    refreshed = await refresher.refresh()

    assert [item.name for item in refreshed.new_items] == ["Ayran"]
    assert refreshed.report.stopped is None
    # The month totals of October, and the one new day.
    assert fatsecret.requests - before == 2


async def test_a_refresh_on_request_doesnt_wait_out_error_12(
    refresher: CatalogRefresher, fatsecret: FakeFatSecret, store: CatalogStore, user: AccessToken
) -> None:
    log(fatsecret, user, "Kurt", date(2026, 10, 1))
    await first_sync(fatsecret, store, user, date(2026, 10, 12))
    fatsecret.limit_requests_to(fatsecret.requests)

    refreshed = await refresher.refresh()

    assert refreshed.report.stopped is not None
    assert "try again" in refreshed.report.stopped
    assert refreshed.report.throttled == 1


async def test_the_server_never_starts_the_first_sync(refresher: CatalogRefresher) -> None:
    with pytest.raises(NeverSynced):
        await refresher.refresh()


async def test_a_daily_refresh_is_due_once_each_diary_day(
    refresher: CatalogRefresher,
    fatsecret: FakeFatSecret,
    store: CatalogStore,
    user: AccessToken,
    clock: FakeClock,
) -> None:
    never = refresher._due()  # pyright: ignore[reportPrivateUsage]
    await first_sync(fatsecret, store, user, TODAY)
    same_day = refresher._due()  # pyright: ignore[reportPrivateUsage]
    clock.now = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)
    next_day = refresher._due()  # pyright: ignore[reportPrivateUsage]

    assert (never, same_day, next_day) == (False, False, True)
