"""Filling the catalog from a Food Diary: finding days, carrying on after a stop, error 12, regional Foods."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from fatsecret_mcp.catalog.items import observation
from fatsecret_mcp.catalog.store import CatalogStore, SyncMode, SyncPhase
from fatsecret_mcp.catalog.sync import DEFAULT_BUDGET, FIRST_BACKOFF_SECONDS, THROTTLE_RETRIES, CatalogSync
from fatsecret_mcp.config.models import Household
from fatsecret_mcp.domain.models import Food, Meal, Serving
from fatsecret_mcp.fatsecret.client import FatSecretApi
from fatsecret_mcp.fatsecret.gateway import AccessToken
from fatsecret_mcp.fatsecret.request_limits import RequestLimits
from tests.conftest import FakeRequestClock
from tests.fatsecret.fake import FakeFatSecret, food_entry
from tests.fatsecret.recorded import CONSUMER, USER, RecordedFatSecret

pytestmark = pytest.mark.anyio
TODAY = date(2026, 11, 1)


class Sleeps:
    def __init__(self) -> None:
        self.seconds: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.seconds.append(seconds)


@pytest.fixture
def user(household: Household) -> AccessToken:
    return household.user("jakub").fatsecret


@pytest.fixture
def store(tmp_path: Path) -> CatalogStore:
    return CatalogStore(sqlite3.connect(tmp_path / "catalog.sqlite3"))


def log(fatsecret: FakeFatSecret, user: AccessToken, day: date, name: str = "Kurt", **kwargs: float) -> None:
    entry = food_entry(
        name,
        Meal.SNACKS,
        day,
        kcal=kwargs.get("kcal", 34),
        amount=f"{kwargs.get('grams', 13):g} g",
        food_id="90000102" if name == "Kurt" else f"food-{name}",
        serving_id="90000202" if name == "Kurt" else f"serving-{name}",
        number_of_units=kwargs.get("units", 0.13),
    )
    fatsecret.add_food_entry(user, entry)


def sync_of(
    fatsecret: FakeFatSecret, store: CatalogStore, user: AccessToken, *, budget: int = DEFAULT_BUDGET
) -> CatalogSync:
    return CatalogSync(fatsecret, store, "jakub", user, budget=budget, sleep=Sleeps())


async def test_a_full_sync_reads_only_days_with_entries_and_stops_after_six_empty_months(
    fatsecret: FakeFatSecret, store: CatalogStore, user: AccessToken
) -> None:
    log(fatsecret, user, date(2026, 10, 30))
    log(fatsecret, user, date(2026, 10, 31), kcal=33, grams=12.5, units=0.125)
    log(fatsecret, user, date(2026, 6, 8), "Tan", kcal=163, grams=525, units=5.25)

    report = await sync_of(fatsecret, store, user).sync(TODAY)

    # November back to June, then May back to December 2025: six empty months in a row.
    assert report.months_read == 12
    assert report.days_read == 3
    assert report.requests == 15
    assert report.stopped is None

    items = {item.name: item for item in store.items("jakub")}

    assert items["Kurt"].times_logged == 2
    assert items["Kurt"].metric_amount == pytest.approx(100)  # pyright: ignore[reportUnknownMemberType]
    assert items["Tan"].per_serving.kcal == pytest.approx(31.05, abs=0.01)  # pyright: ignore[reportUnknownMemberType]
    assert store.state("jakub") is not None


async def test_a_stopped_sync_carries_on_where_it_stopped(
    fatsecret: FakeFatSecret, tmp_path: Path, user: AccessToken
) -> None:
    for day in range(1, 11):
        log(fatsecret, user, date(2026, 10, day))

    path = tmp_path / "catalog.sqlite3"

    with CatalogStore.open(path) as store:
        first = await sync_of(fatsecret, store, user, budget=5).sync(TODAY)

    with CatalogStore.open(path) as store:
        state = store.state("jakub")
        second = await sync_of(fatsecret, store, user).sync(TODAY)
        items = store.items("jakub")

    assert first.stopped is not None
    assert first.requests == 5
    assert state is not None
    assert state.phase is SyncPhase.MONTHS
    assert second.resumed
    # Each month and day is read once across both runs: November back to April, then ten days.
    assert first.months_read + second.months_read == 8
    assert first.days_read + second.days_read == 10
    assert items[0].times_logged == 10


async def test_an_incremental_sync_rereads_days_since_three_days_before_the_last(
    fatsecret: FakeFatSecret, store: CatalogStore, user: AccessToken
) -> None:
    log(fatsecret, user, date(2026, 6, 1))
    await sync_of(fatsecret, store, user).sync(date(2026, 10, 20))

    log(fatsecret, user, date(2026, 10, 18), "Tan", kcal=163, grams=525, units=5.25)
    log(fatsecret, user, date(2026, 10, 30))
    report = await sync_of(fatsecret, store, user).sync(TODAY)

    assert report.mode is SyncMode.INCREMENTAL
    # November and October; days from 17 October on.
    assert report.months_read == 2
    assert report.days_read == 2
    assert {item.name: item.times_logged for item in store.items("jakub")} == {"Kurt": 2, "Tan": 1}


async def test_a_day_cleared_in_the_app_leaves_the_catalog(
    fatsecret: FakeFatSecret, store: CatalogStore, user: AccessToken
) -> None:
    log(fatsecret, user, date(2026, 10, 30))
    await sync_of(fatsecret, store, user).sync(TODAY)

    fatsecret.delete_food_entries(user, date(2026, 10, 30))
    await sync_of(fatsecret, store, user).sync(TODAY, full=True)

    assert store.items("jakub") == []


async def test_error_12_is_waited_out_with_growing_pauses(
    fatsecret: FakeFatSecret, store: CatalogStore, user: AccessToken
) -> None:
    log(fatsecret, user, date(2026, 10, 30))
    fatsecret.limit_requests_to(1)
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

        if len(sleeps) == 3:
            fatsecret.limit_requests_to(None)

    report = await CatalogSync(fatsecret, store, "jakub", user, sleep=sleep).sync(TODAY)

    assert sleeps == [FIRST_BACKOFF_SECONDS, 2 * FIRST_BACKOFF_SECONDS, 4 * FIRST_BACKOFF_SECONDS]
    assert report.throttled == 3
    assert report.stopped is None
    assert len(store.items("jakub")) == 1


async def test_endless_error_12_stops_the_run_and_keeps_its_progress(
    fatsecret: FakeFatSecret, store: CatalogStore, user: AccessToken
) -> None:
    log(fatsecret, user, date(2026, 10, 30))
    fatsecret.limit_requests_to(2)

    report = await sync_of(fatsecret, store, user).sync(TODAY)
    state = store.state("jakub")

    assert report.stopped is not None
    assert report.throttled == THROTTLE_RETRIES + 1
    assert state is not None
    assert state.next_month == date(2026, 9, 1)


async def test_classify_marks_foods_the_catalog_cant_return_once(
    fatsecret: FakeFatSecret, store: CatalogStore, user: AccessToken
) -> None:
    log(fatsecret, user, date(2026, 10, 30))
    log(fatsecret, user, date(2026, 10, 30), "Banana", kcal=105, grams=118, units=1)
    fatsecret.add_food(Food(id="food-Banana", name="Banana", brand=None, servings=list[Serving]()))

    sync = sync_of(fatsecret, store, user)
    await sync.sync(TODAY)
    first = await sync.classify(TODAY)
    again = await sync_of(fatsecret, store, user).classify(TODAY)

    assert {item.name: item.is_regional for item in store.items("jakub")} == {"Kurt": True, "Banana": False}
    assert (first.checked, first.regional) == (2, 1)
    assert again.checked == 0


async def test_classify_reads_error_106_from_the_real_adapter(store: CatalogStore) -> None:
    recorded = RecordedFatSecret()
    recorded.serve("error_food_get_non_us.json")

    async with recorded.client() as http:
        clock = FakeRequestClock()
        api = FatSecretApi(CONSUMER, http, request_limits=RequestLimits(now=clock, sleep=clock.sleep))
        sync = CatalogSync(api, store, "jakub", USER, sleep=Sleeps())

        _with_logged_food(store, "51467582")
        report = await sync.classify(TODAY)

    assert (report.checked, report.regional) == (1, 1)
    assert store.items("jakub")[0].is_regional is True


def _with_logged_food(store: CatalogStore, food_id: str) -> None:
    entry = food_entry("Skyr Waniliowy", Meal.LUNCH, date(2026, 10, 13), kcal=148, food_id=food_id)
    logged = observation(entry)

    assert logged is not None

    store.replace_day("jakub", entry.diary_day, [logged])
    store.rebuild_items("jakub")
