"""My Foods: finding what the User logged before, drafting it in grams, and settling a review.

Everything goes through an in-memory MCP client against the real server, with a catalog filled from
the User's Food Diary as `sync-catalog` fills it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import date
from typing import Any

import pytest
from mcp import Client

from fatsecret_mcp.catalog.items import observation
from fatsecret_mcp.catalog.store import CatalogStore
from fatsecret_mcp.config.models import Household
from fatsecret_mcp.domain.models import FoodEntry, Meal
from fatsecret_mcp.fatsecret.budget import daily_budget
from fatsecret_mcp.fatsecret.food_cache import FoodCache
from fatsecret_mcp.server import build_server
from tests.conftest import FakeClock
from tests.fatsecret.fake import FakeFatSecret, food_entry

pytestmark = pytest.mark.anyio
TODAY = date(2026, 10, 14)
KURT = {"food_id": "90000102", "serving_id": "90000202"}
GRUDKA = {"food_id": "90000105", "serving_id": "90000205"}


@pytest.fixture
def catalog(fatsecret: FakeFatSecret, household: Household) -> CatalogStore:
    """Jakub's catalog: kurt in grams, chicken breast whose data changed, and tea by the mug."""
    store = CatalogStore(sqlite3.connect(":memory:"))
    user = household.user("jakub").fatsecret
    entries = [
        _entry("Домашний Курт", date(2026, 10, 1), kcal=34, grams=13, **KURT),
        _entry("Домашний Курт", date(2026, 10, 5), kcal=52, grams=20, **KURT),
        _entry("Фермерская Грудка", date(2026, 5, 1), kcal=113, grams=100, **GRUDKA),
        _entry("Фермерская Грудка", date(2026, 5, 2), kcal=113, grams=100, **GRUDKA),
        _entry("Фермерская Грудка", date(2026, 10, 10), kcal=169, grams=100, **GRUDKA),
        food_entry(
            "Чай с Сахаром",
            Meal.BREAKFAST,
            date(2026, 10, 12),
            kcal=40,
            amount="1 mug",
            food_id="tea",
            serving_id="mug",
            number_of_units=1,
        ),
    ]

    for entry in entries:
        fatsecret.add_food_entry(user, entry)
        logged = observation(entry)

        assert logged is not None

        store.replace_day("jakub", entry.diary_day, [logged])

    store.rebuild_items("jakub")

    return store


@pytest.fixture
def connect_with(
    household: Household, fatsecret: FakeFatSecret, clock: FakeClock, catalog: CatalogStore
) -> Callable[[], Client]:
    def connect() -> Client:
        server = build_server(
            user=household.user("jakub"),
            fatsecret=fatsecret,
            clock=clock,
            signing_key=household.signing_key,
            daily_requests=daily_budget(len(household.users)),
            food_cache=FoodCache(clock=clock),
            catalog=catalog,
        )

        return Client(server)

    return connect


async def test_my_foods_are_found_in_any_case_and_cost_no_request(
    connect_with: Callable[[], Client], fatsecret: FakeFatSecret
) -> None:
    async with connect_with() as client:
        found = await _call(client, "search_my_foods", {"description": "КУРТ"})
        missing = await _call(client, "search_my_foods", {"description": "bibimbap"})

    match = found["matches"][0]

    assert found["status"] == "found"
    assert (match["name"], match["serving"], match["metric_amount"]) == ("Домашний Курт", "100 g", 100)
    assert match["per_100"]["kcal"] == 261
    assert match["times_logged"] == 2
    assert missing == {"status": "not_found", "matches": []}
    assert fatsecret.requests == 0


async def test_a_my_food_is_drafted_in_grams_and_confirmed_by_its_ids(
    connect_with: Callable[[], Client], fatsecret: FakeFatSecret, household: Household
) -> None:
    async with connect_with() as client:
        proposed = await _call(
            client, "draft_entries", {"items": [{"from_my_foods": True, **KURT, "metric_amount": 50}]}
        )
        await _call(client, "confirm_draft", {"draft_id": proposed["draft_id"]})

    item = proposed["items"][0]
    written = await fatsecret.list_food_entries(household.user("jakub").fatsecret, TODAY)

    assert (item["name"], item["amount"], item["number_of_units"], item["kcal"]) == (
        "Домашний Курт",
        "50 g",
        0.5,
        130,
    )
    assert item["my_food"]["needs_review"] is False
    assert [(entry.food_id, entry.serving_id, entry.number_of_units) for entry in written] == [
        (KURT["food_id"], KURT["serving_id"], 0.5)
    ]


async def test_a_serving_without_a_weight_is_drafted_in_servings(connect_with: Callable[[], Client]) -> None:
    tea = {"from_my_foods": True, "food_id": "tea", "serving_id": "mug"}

    async with connect_with() as client:
        refused = await client.call_tool("draft_entries", {"items": [{**tea, "metric_amount": 300}]})
        proposed = await _call(client, "draft_entries", {"items": [{**tea, "number_of_units": 2}]})

    assert refused.is_error
    assert "number_of_units" in str(refused.content)
    assert (proposed["items"][0]["amount"], proposed["items"][0]["kcal"]) == ("2 x mug", 80)


async def test_changed_data_asks_for_the_label_and_shows_it_beside_fatsecrets_values(
    connect_with: Callable[[], Client],
) -> None:
    grudka = {"from_my_foods": True, **GRUDKA, "metric_amount": 200}
    label = {"kcal": 150, "protein": 30, "fat": 3, "carbohydrate": 0}

    async with connect_with() as client:
        before = await _call(client, "draft_entries", {"items": [grudka]})
        saved = await _call(client, "set_my_food_nutrition", {**GRUDKA, "per_100": label})
        after = await _call(client, "draft_entries", {"items": [grudka]})

    assert before["items"][0]["kcal"] == 338
    assert before["items"][0]["my_food"]["needs_review"] is True
    assert saved["needs_review"] is False
    assert saved["label_per_100"]["kcal"] == 150
    assert after["items"][0]["kcal"] == 338
    assert after["items"][0]["my_food"]["label"]["kcal"] == 300
    assert after["items"][0]["my_food"]["difference"]["kcal"] == 38


async def test_keeping_current_values_settles_the_review(connect_with: Callable[[], Client]) -> None:
    async with connect_with() as client:
        saved = await _call(client, "set_my_food_nutrition", {**GRUDKA, "keep_current": True})

    assert saved["needs_review"] is False
    assert saved["label_per_100"]["kcal"] == 169


async def test_unknown_ids_are_refused(connect_with: Callable[[], Client]) -> None:
    unknown = {"from_my_foods": True, "food_id": "1", "serving_id": "2", "metric_amount": 10}

    async with connect_with() as client:
        result = await client.call_tool("draft_entries", {"items": [unknown]})

    assert result.is_error
    assert "search_my_foods" in str(result.content)


async def test_without_a_catalog_my_foods_tools_are_not_offered(connect: Callable[[str], Client]) -> None:
    async with connect("jakub") as client:
        names = {tool.name for tool in (await client.list_tools()).tools}

    assert "search_my_foods" not in names
    assert "set_my_food_nutrition" not in names


async def _call(client: Client, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await client.call_tool(tool, arguments)

    assert not result.is_error, result.content
    assert result.structured_content is not None

    return result.structured_content


def _entry(name: str, day: date, *, kcal: float, grams: float, food_id: str, serving_id: str) -> FoodEntry:
    """An entry in a 100 g Serving, as FatSecret writes a weight typed in grams."""
    return food_entry(
        name,
        Meal.SNACKS,
        day,
        kcal=kcal,
        amount=f"{grams:g} g",
        food_id=food_id,
        serving_id=serving_id,
        number_of_units=grams / 100,
    )
