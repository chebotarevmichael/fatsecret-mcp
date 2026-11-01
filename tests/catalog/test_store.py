"""The catalog file: the User's label values settle a review and outlast a rebuild."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from fatsecret_mcp.catalog.items import NutritionSource, observation
from fatsecret_mcp.catalog.store import CatalogStore
from fatsecret_mcp.domain.models import Meal, Nutrition
from tests.fatsecret.fake import food_entry

DAY = date(2026, 10, 1)


def test_the_users_label_values_are_kept_beside_the_logged_ones(tmp_path: Path) -> None:
    store = CatalogStore(sqlite3.connect(tmp_path / "catalog.sqlite3"))

    for day, kcal in ((date(2026, 5, 1), 113), (date(2026, 5, 2), 113), (DAY, 169)):
        entry = food_entry("Grudka", Meal.LUNCH, day, kcal=kcal, food_id="90000105", serving_id="90000205")
        logged = observation(entry)

        assert logged is not None

        store.replace_day("michael", day, [logged])

    store.rebuild_items("michael")
    before = store.items("michael")[0]

    given = Nutrition(kcal=150, protein=30, fat=3, carbohydrate=1)
    store.set_override("michael", "90000105", "90000205", given, DAY)
    store.rebuild_items("michael")
    after = store.items("michael")[0]

    assert (before.per_serving.kcal, before.source, before.needs_review) == (
        169,
        NutritionSource.LATEST,
        True,
    )
    # FatSecret still logs its own values; the User's are kept beside them and settle the review.
    assert after.per_serving == before.per_serving
    assert after.label_per_serving == given
    assert not after.needs_review


def test_items_from_an_earlier_schema_are_rebuilt(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE catalog_items (user TEXT, food_id TEXT, serving_id TEXT, is_regional INTEGER)"
    )
    connection.commit()

    store = CatalogStore(connection)

    assert store.items("michael") == []
