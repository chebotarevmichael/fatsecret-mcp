"""Searching the catalog by name: letter case, the letter ё, word starts, and the most logged first."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from fatsecret_mcp.catalog.items import CatalogItem
from fatsecret_mcp.catalog.search import search
from fatsecret_mcp.domain.models import Nutrition

ITEM = CatalogItem(
    food_id="1",
    serving_id="1",
    name="",
    brand=None,
    serving_description="100 g",
    metric_amount=100,
    metric_unit="g",
    per_serving=Nutrition(kcal=100),
    times_logged=1,
    first_seen=date(2026, 1, 1),
    last_seen=date(2026, 1, 1),
)


def item(name: str, times_logged: int = 1, brand: str | None = None) -> CatalogItem:
    return replace(ITEM, food_id=name, name=name, times_logged=times_logged, brand=brand)


def test_case_and_yo_dont_matter() -> None:
    items = [item("Джинн Семечки Солёные")]

    assert search(items, "семечки соленые") == items
    assert search(items, "СЕМЕЧКИ") == items


def test_every_word_must_match() -> None:
    items = [item("Тартин Злаковый"), item("Тартин Пшеничный")]

    assert [found.name for found in search(items, "тартин злак")] == ["Тартин Злаковый"]


def test_word_starts_rank_above_words_inside_then_the_most_logged() -> None:
    items = [item("Сникерс Мини", 10), item("Луговой Мини Курт", 2), item("Домашний Курт", 5)]

    assert [found.name for found in search(items, "курт")] == ["Домашний Курт", "Луговой Мини Курт"]
    assert [found.name for found in search(items, "ини")] == ["Сникерс Мини", "Луговой Мини Курт"]


def test_a_brand_is_searched_too() -> None:
    items = [item("Огурцы Маринованные", brand="Bonduelle")]

    assert search(items, "bonduelle") == items


def test_a_blank_query_finds_nothing() -> None:
    assert search([item("Курт")], "  ") == []
