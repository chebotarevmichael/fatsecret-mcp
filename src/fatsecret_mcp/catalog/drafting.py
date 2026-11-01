"""Draft items from the User's own catalog: a Food and Serving logged before, in a new amount.

The entry is written with the Item's Food and Serving ids, which works for regional Foods the
catalog can't return. FatSecret computes the logged nutrition from its own Food data, which the
Item's nutrition per Serving follows; the User's label values, when given, are shown beside it.
"""

from __future__ import annotations

from fatsecret_mcp.catalog.items import CatalogItem
from fatsecret_mcp.domain.drafts import UNIT_DECIMALS, InvalidDraftItem, check_plausible
from fatsecret_mcp.domain.entry_amounts import positive
from fatsecret_mcp.domain.models import DraftEntry, DraftItem, MyFood


def my_food_item(
    item: CatalogItem,
    *,
    metric_amount: float | None,
    number_of_units: float | None,
    name: str | None,
    assumption: str | None,
) -> DraftItem:
    """The Draft item logging `metric_amount` grams or ml of the Item, or `number_of_units` Servings."""
    units = round(_units(item, metric_amount, number_of_units), UNIT_DECIMALS)

    if units <= 0:
        raise InvalidDraftItem("Amount is too small to log.")

    nutrition = item.per_serving.scaled(units)
    weight = item.metric_amount * units if item.metric_amount is not None else None

    check_plausible(nutrition, weight)

    serving = item.serving_description or "serving"
    amount = f"{weight:g} {item.metric_unit}" if weight is not None else f"{units:g} x {serving}"
    label = item.label_per_serving.scaled(units) if item.label_per_serving is not None else None

    return DraftItem(
        entry=DraftEntry(
            food_id=item.food_id,
            serving_id=item.serving_id,
            number_of_units=units,
            entry_name=name or item.name,
        ),
        serving=serving,
        amount=amount,
        nutrition=nutrition,
        assumption=assumption,
        my_food=MyFood(
            times_logged=item.times_logged,
            last_logged=item.last_seen,
            needs_review=item.needs_review,
            label_for_amount=label,
        ),
    )


def _units(item: CatalogItem, metric_amount: float | None, number_of_units: float | None) -> float:
    if (metric_amount is None) == (number_of_units is None):
        raise InvalidDraftItem("Give either metric_amount or number_of_units for a My Food item.")

    if number_of_units is not None:
        return number_of_units

    assert metric_amount is not None

    if item.metric_amount is None or not positive(item.metric_amount):
        raise InvalidDraftItem(
            f"The weight of one Serving of {item.name} ({item.serving_description}) is unknown. "
            "Give number_of_units in its Servings instead."
        )

    return metric_amount / item.metric_amount
