"""Catalog Items from Food Entries: amounts, one Serving's nutrition, the median, and brands."""

from __future__ import annotations

from datetime import date

import pytest

from fatsecret_mcp.catalog.items import NutritionSource, Observation, brand, catalog_item, observation
from fatsecret_mcp.domain.models import Meal, Nutrition
from fatsecret_mcp.fatsecret import parsing
from tests.fatsecret.fake import food_entry
from tests.fatsecret.recorded import fixture

DAY = date(2026, 11, 1)


def regional_entries() -> list[Observation]:
    body = fixture("food_entries_get_regional.json")["response"]["json"]
    loggings = [observation(entry) for entry in parsing.food_entries(body)]

    return [logged for logged in loggings if logged is not None]


def test_grams_typed_in_the_russian_app_give_one_servings_weight() -> None:
    by_name = {logged.name: logged for logged in regional_entries()}
    tan = by_name["СТЕПНОЙ АЙРАН Айран"]
    kurt = by_name["Домашний Курт"]

    # "5 1/4 :custom:525s  г": 525 g in 5.25 Servings of 100 g.
    assert (tan.metric_amount, tan.metric_unit, tan.serving_description) == (100, "g", "100 g")
    assert (kurt.metric_amount, kurt.metric_unit) == (pytest.approx(100), "g")  # pyright: ignore[reportUnknownMemberType]


def test_one_servings_nutrition_is_the_entrys_divided_by_its_units() -> None:
    by_name = {logged.name: logged for logged in regional_entries()}
    noodles = by_name["Восточная Лапша с Говядиной"]

    # 374 kcal for 0.85 Servings.
    assert noodles.per_serving.kcal == pytest.approx(440)  # pyright: ignore[reportUnknownMemberType]
    assert noodles.food_id == "90000104"
    assert noodles.serving_id == "90000204"


def test_a_serving_without_grams_keeps_its_measure() -> None:
    entry = food_entry("Banana", Meal.SNACKS, DAY, kcal=210, amount="2 mediums", number_of_units=2)
    logged = observation(entry)

    assert logged is not None
    assert (logged.serving_description, logged.metric_amount) == ("medium", None)
    assert logged.per_serving.kcal == 105


@pytest.mark.parametrize(
    ("amount", "units", "description", "grams"),
    [
        ("2 шоколадка, 23,5 г", 2, "шоколадка, 23,5 г", 23.5),
        ("1 порция (30 г)", 1, "порция (30 г)", 30),
        ("0,5 стакан (300 мл)", 0.5, "стакан (300 мл)", 300),
        ("1,5 г", 1.5, "1 g", 1),
        ("1 mug", 1, "mug", None),
    ],
)
def test_a_servings_weight_is_read_from_its_description(
    amount: str, units: float, description: str, grams: float | None
) -> None:
    entry = food_entry("Kinder Country", Meal.SNACKS, DAY, kcal=100, amount=amount, number_of_units=units)
    logged = observation(entry)

    assert logged is not None
    assert (logged.serving_description, logged.metric_amount) == (description, grams)


def test_an_entry_without_units_isnt_a_logging() -> None:
    entry = food_entry("Water", Meal.SNACKS, DAY, kcal=0, amount="0 g", number_of_units=0)

    assert observation(entry) is None


def test_loggings_that_agree_keep_their_median() -> None:
    loggings = [
        _logged("Kurt", date(2026, 11, 1), kcal=260, units=0.13),
        _logged("Kurt", date(2026, 11, 2), kcal=262, units=0.2),
        _logged("Курт Домашний", date(2026, 11, 3), kcal=261, units=1),
    ]

    item, spread = catalog_item(loggings)

    assert spread is None
    assert (item.per_serving.kcal, item.source, item.needs_review) == (261, NutritionSource.MEDIAN, False)
    assert item.name == "Курт Домашний"
    assert (item.times_logged, item.first_seen, item.last_seen) == (3, date(2026, 11, 1), date(2026, 11, 3))


def test_changed_data_keeps_the_latest_loggings_and_asks_for_review() -> None:
    # The Food's data changed on FatSecret: 113 kcal for months, then 169.
    loggings = [_logged("Grudka", date(2026, 5, day), kcal=113, units=1) for day in range(1, 8)]
    loggings += [
        _logged("Grudka", date(2026, 10, 1), kcal=169, units=1),
        _logged("Grudka", date(2026, 10, 2), kcal=170, units=1),
    ]

    item, spread = catalog_item(loggings)

    assert spread is not None
    assert (spread.median_kcal, spread.latest_kcal, spread.latest_entries) == (113, 169.5, 2)
    assert spread.doubtful
    assert (item.per_serving.kcal, item.source, item.needs_review) == (169.5, NutritionSource.LATEST, True)


def test_one_old_odd_logging_keeps_the_latest_without_review() -> None:
    loggings = [_logged("Pepsi", date(2026, 3, 1), kcal=45, units=1)]
    loggings += [_logged("Pepsi", date(2026, 6, day), kcal=28, units=1) for day in range(1, 6)]

    item, spread = catalog_item(loggings)

    assert spread is not None
    assert not spread.doubtful
    assert (item.per_serving.kcal, item.needs_review) == (28, False)


def test_rounding_of_tiny_entries_isnt_a_disagreement() -> None:
    # 34 kcal for 13 g and 26 kcal for 10 g of the same 100 g Serving: both rounded from about 261.
    loggings = [
        _logged("Kurt", DAY, kcal=34 / 0.13, units=0.13),
        _logged("Kurt", DAY, kcal=26 / 0.10, units=0.10),
    ]

    _, spread = catalog_item(loggings)

    assert spread is None


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("СТЕПНОЙ АЙРАН Айран", "СТЕПНОЙ АЙРАН"),
        ("ULKER Albeni", "ULKER"),
        ("Домашний Курт", None),
        ("KFC", None),
        ("Восточная Лапша", None),
    ],
)
def test_a_brand_is_only_read_from_capitals(name: str, expected: str | None) -> None:
    assert brand(name) == expected


def _logged(name: str, day: date, *, kcal: float, units: float) -> Observation:
    return Observation(
        food_entry_id=f"{name}-{day}-{kcal}",
        food_id="90000102",
        serving_id="90000202",
        diary_day=day,
        name=name,
        number_of_units=units,
        serving_description="100 g",
        metric_amount=100,
        metric_unit="g",
        per_serving=Nutrition(kcal=kcal),
    )
