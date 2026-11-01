"""The catalog file: the User's own values win over their entries and outlast a rebuild."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from fatsecret_mcp.catalog.items import NutritionSource, observation
from fatsecret_mcp.catalog.store import CatalogStore
from fatsecret_mcp.domain.models import Meal, Nutrition
from tests.fatsecret.fake import food_entry

DAY = date(2026, 10, 1)


def test_the_users_values_override_the_entries_until_they_change_them(tmp_path: Path) -> None:
    store = CatalogStore(sqlite3.connect(tmp_path / "catalog.sqlite3"))

    for day, kcal in ((date(2026, 5, 1), 113), (date(2026, 5, 2), 113), (DAY, 169)):
        entry = food_entry("Grudka", Meal.LUNCH, day, kcal=kcal, food_id="90000105", serving_id="90000205")
        logged = observation(entry)

        assert logged is not None

        store.replace_day("michael", day, [logged])

    store.rebuild_items("michael")
    before = store.items("michael")[0]

    store.set_override(
        "michael", "90000105", "90000205", Nutrition(kcal=150, protein=30, fat=3, carbohydrate=1), DAY
    )
    store.rebuild_items("michael")
    after = store.items("michael")[0]

    assert (before.per_serving.kcal, before.source, before.needs_review) == (
        169,
        NutritionSource.LATEST,
        True,
    )
    assert after.per_serving == Nutrition(kcal=150, protein=30, fat=3, carbohydrate=1)
    assert (after.source, after.needs_review) == (NutritionSource.OVERRIDE, False)


def test_items_from_an_earlier_schema_are_rebuilt(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE catalog_items (user TEXT, food_id TEXT, serving_id TEXT, is_regional INTEGER)"
    )
    connection.commit()

    store = CatalogStore(connection)

    assert store.items("michael") == []
