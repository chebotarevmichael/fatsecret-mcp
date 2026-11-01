"""Fetching a catalog Food only when needed, and never again once FatSecret's catalog can't return it."""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from fatsecret_mcp.catalog.lookup import catalog_food
from fatsecret_mcp.catalog.store import CatalogStore
from fatsecret_mcp.domain.models import Food, Serving
from tests.fatsecret.fake import FakeFatSecret

pytestmark = pytest.mark.anyio
TODAY = date(2026, 11, 1)


@pytest.fixture
def store() -> CatalogStore:
    return CatalogStore(sqlite3.connect(":memory:"))


async def test_a_regional_food_is_requested_once(fatsecret: FakeFatSecret, store: CatalogStore) -> None:
    first = await catalog_food("90000102", fatsecret, store, TODAY)
    again = await catalog_food("90000102", fatsecret, store, TODAY)

    assert (first, again) == (None, None)
    assert fatsecret.requests == 1
    assert store.is_regional("90000102") is True


async def test_a_food_the_catalog_returns_is_marked_and_fetched(
    fatsecret: FakeFatSecret, store: CatalogStore
) -> None:
    tomato = Food(id="6138", name="Tomatoes", brand=None, servings=list[Serving]())
    fatsecret.add_food(tomato)

    food = await catalog_food("6138", fatsecret, store, TODAY)

    assert food == tomato
    assert store.is_regional("6138") is False


async def test_an_unchecked_food_is_unknown(store: CatalogStore) -> None:
    assert store.is_regional("6138") is None
