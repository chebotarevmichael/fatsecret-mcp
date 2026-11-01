"""A catalog Food fetched only when it's needed, remembering the Foods FatSecret's catalog can't return.

Logging from the catalog needs no `food.get`. When a tool wants more, such as another Serving, the
Food is fetched once: error 106 marks it regional in `food_checks`, so it's never requested again,
and a Food that is returned is marked as not regional. This is the lazy form of `sync-catalog
--classify`, and both share the same checks.
"""

from __future__ import annotations

from datetime import date

from fatsecret_mcp.catalog.store import CatalogStore
from fatsecret_mcp.domain.models import Food
from fatsecret_mcp.fatsecret.errors import NotFound
from fatsecret_mcp.fatsecret.gateway import FatSecretGateway


async def catalog_food(
    food_id: str, fatsecret: FatSecretGateway, store: CatalogStore, today: date
) -> Food | None:
    """The Food with its Servings, or None for a regional Food, without a request once that's known."""
    if store.is_regional(food_id):
        return None

    try:
        food = await fatsecret.get_food(food_id)
    except NotFound:
        store.record_check(food_id, is_regional=True, checked_on=today)

        return None

    if store.is_regional(food_id) is None:
        store.record_check(food_id, is_regional=False, checked_on=today)

    return food
