"""Catalog Items as data: one Food and Serving a User has logged, read from their Food Entries.

A Food Entry holds nutrition for its number of units of a Serving, so one Serving's nutrition is the
entry's divided by its units. FatSecret rounds each entry's kcal, so entries of a few grams disagree
by a few kcal per Serving; the median of all entries is kept, and wider spreads are reported.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from statistics import median

from fatsecret_mcp.domain.entry_amounts import positive
from fatsecret_mcp.domain.models import FoodEntry, Nutrition

# Grams and millilitres as FatSecret writes them in English and in Russian (g, gr, ml in Cyrillic).
METRIC_UNITS = {"g": "g", "\u0433": "g", "\u0433\u0440": "g", "ml": "ml", "\u043c\u043b": "ml"}
METRIC_AMOUNT = re.compile(rf"(?P<amount>\d+(?:\.\d+)?) ?(?P<unit>{'|'.join(METRIC_UNITS)})\.?")
LEADING_NUMBER = re.compile(r"(?P<number>\d+(?:\.\d+)?)\s*(?P<rest>.*)")

SPREAD_SHARE = 0.05
"""Per-Serving kcal are reported when they differ by this share of their median beyond rounding."""
ROUNDING_KCAL = 0.5
"""FatSecret rounds an entry's kcal to a whole number, so one Serving's kcal are off by up to this / units."""


@dataclass(frozen=True)
class Observation:
    """One Food Entry, as one logging of a Serving."""

    food_entry_id: str
    food_id: str
    serving_id: str
    diary_day: date
    name: str
    number_of_units: float
    serving_description: str | None
    metric_amount: float | None
    """Grams or millilitres in one Serving, when the entry's amount was written in them."""
    metric_unit: str | None
    per_serving: Nutrition


@dataclass(frozen=True)
class CatalogItem:
    food_id: str
    serving_id: str
    name: str
    """The most recent entry name, the one to log the Food under again."""
    brand: str | None
    serving_description: str | None
    metric_amount: float | None
    metric_unit: str | None
    per_serving: Nutrition
    times_logged: int
    first_seen: date
    last_seen: date
    is_regional: bool | None = None
    """True when FatSecret's catalog can't return the Food to this API key; None until checked."""


@dataclass(frozen=True)
class Spread:
    """Entries of one Serving whose per-Serving kcal disagree more than rounding explains."""

    food_id: str
    serving_id: str
    name: str
    lowest_kcal: float
    highest_kcal: float
    median_kcal: float
    entries: int


def observation(entry: FoodEntry) -> Observation | None:
    """The entry as one logging of its Serving, or None when its units can't give a per-Serving value."""
    if not positive(entry.number_of_units):
        return None

    per_serving = entry.nutrition.scaled(1 / entry.number_of_units)
    metric = _metric(entry.amount)

    if metric is None:
        description = _serving_description(entry.amount, entry.number_of_units)
        metric_amount = metric_unit = None
    else:
        amount, metric_unit = metric
        metric_amount = amount / entry.number_of_units
        description = f"{metric_amount:g} {metric_unit}"

    return Observation(
        food_entry_id=entry.id,
        food_id=entry.food_id,
        serving_id=entry.serving_id,
        diary_day=entry.diary_day,
        name=entry.name,
        number_of_units=entry.number_of_units,
        serving_description=description,
        metric_amount=metric_amount,
        metric_unit=metric_unit,
        per_serving=per_serving,
    )


def catalog_item(observations: Sequence[Observation]) -> tuple[CatalogItem, Spread | None]:
    """One Serving's Catalog Item from all its loggings, and their spread when it's wider than rounding."""
    if not observations:
        raise ValueError("A Catalog Item needs at least one logging.")

    by_day = sorted(observations, key=lambda logged: (logged.diary_day, logged.food_entry_id))
    latest = by_day[-1]
    per_serving = Nutrition(
        kcal=median(logged.per_serving.kcal for logged in by_day),
        protein=median(logged.per_serving.protein for logged in by_day),
        fat=median(logged.per_serving.fat for logged in by_day),
        carbohydrate=median(logged.per_serving.carbohydrate for logged in by_day),
    )
    metric = [logged for logged in by_day if logged.metric_amount is not None]
    amounts = [logged.metric_amount for logged in metric if logged.metric_amount is not None]
    metric_amount = median(amounts) if amounts else None
    metric_unit = metric[-1].metric_unit if metric else None
    description = f"{metric_amount:g} {metric_unit}" if metric_amount else latest.serving_description

    item = CatalogItem(
        food_id=latest.food_id,
        serving_id=latest.serving_id,
        name=latest.name,
        brand=brand(latest.name),
        serving_description=description,
        metric_amount=metric_amount,
        metric_unit=metric_unit,
        per_serving=per_serving,
        times_logged=len(by_day),
        first_seen=by_day[0].diary_day,
        last_seen=latest.diary_day,
    )

    return item, _spread(item, by_day)


def brand(name: str) -> str | None:
    """The brand when the entry name starts with it in capitals, "ULKER Albeni"; otherwise unknown.

    FatSecret names a Brand Food's entry "<brand> <food>", but only capitals show where the brand ends.
    """
    words = name.split()
    leading = 0

    while leading < len(words) - 1 and _capitalised(words[leading]):
        leading += 1

    return " ".join(words[:leading]) if leading else None


def _capitalised(word: str) -> bool:
    letters = [character for character in word if character.isalpha()]

    return len(letters) >= 2 and all(letter.isupper() for letter in letters)


def _metric(amount: str) -> tuple[float, str] | None:
    match = METRIC_AMOUNT.fullmatch(amount.strip().lower())

    if match is None or not positive(float(match["amount"])):
        return None

    return float(match["amount"]), METRIC_UNITS[match["unit"]]


def _serving_description(amount: str, number_of_units: float) -> str | None:
    """One Serving of a non-metric amount: "2 servings" is "serving", "1 cup, diced" is "cup, diced"."""
    match = LEADING_NUMBER.fullmatch(amount.strip())

    if match is None:
        return amount.strip() or None

    rest = match["rest"].strip()

    if not rest:
        return None

    if number_of_units != 1 and rest.endswith("s") and float(match["number"]) == number_of_units:
        rest = rest.removesuffix("s")

    return rest


def _spread(item: CatalogItem, observations: Sequence[Observation]) -> Spread | None:
    lowest = min(observations, key=lambda logged: logged.per_serving.kcal)
    highest = max(observations, key=lambda logged: logged.per_serving.kcal)
    width = highest.per_serving.kcal - lowest.per_serving.kcal
    rounding = ROUNDING_KCAL / lowest.number_of_units + ROUNDING_KCAL / highest.number_of_units

    if width <= rounding + SPREAD_SHARE * item.per_serving.kcal:
        return None

    return Spread(
        food_id=item.food_id,
        serving_id=item.serving_id,
        name=item.name,
        lowest_kcal=lowest.per_serving.kcal,
        highest_kcal=highest.per_serving.kcal,
        median_kcal=item.per_serving.kcal,
        entries=len(observations),
    )
