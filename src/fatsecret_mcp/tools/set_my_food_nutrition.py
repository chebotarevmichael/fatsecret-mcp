"""set_my_food_nutrition: the User's own label values for a Food in their catalog."""

from __future__ import annotations

from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, ToolAnnotations

from fatsecret_mcp.catalog.store import CatalogStore
from fatsecret_mcp.config.models import User
from fatsecret_mcp.domain.diary_day import Clock, today
from fatsecret_mcp.tools.schemas import CatalogId, MyFoodMatch, MyFoodNutritionInput, as_tool_result


def register(server: MCPServer, user: User, catalog: CatalogStore, clock: Clock) -> None:
    annotations = ToolAnnotations(
        title="Set My Food Nutrition",
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
    )

    @server.tool(annotations=annotations)
    async def set_my_food_nutrition(
        food_id: CatalogId,
        serving_id: CatalogId,
        per_100: MyFoodNutritionInput | None = None,
        keep_current: bool = False,
    ) -> Annotated[CallToolResult, MyFoodMatch]:
        """Save the label's kcal and Macros per 100 g or ml for a Food in the User's catalog.

        Use it when a My Food item needs review: pass per_100 with the values the User read from the
        label, or keep_current=true when they don't have them, which keeps what FatSecret logs. It
        settles the review, so the User isn't asked again. Only the catalog changes: FatSecret logs
        its own values, and the Draft shows the label's beside them. Then draft again.
        """
        found = catalog.item(user.id, food_id, serving_id)

        if found is None:
            raise ToolError("Unknown My Food: use the ids of a search_my_foods match.")

        if (per_100 is None) == (not keep_current):
            raise ToolError("Give either per_100 or keep_current=true.")

        if per_100 is None:
            per_serving = found.per_serving
        elif found.metric_amount is None or found.metric_amount <= 0:
            raise ToolError(
                f"The weight of one Serving of {found.name} is unknown, so values per 100 g can't be used."
            )
        else:
            per_serving = per_100.as_nutrition().scaled(found.metric_amount / 100)

        catalog.set_override(user.id, food_id, serving_id, per_serving, today(clock(), user.timezone))
        catalog.rebuild_items(user.id)
        saved = catalog.item(user.id, food_id, serving_id)

        assert saved is not None

        return as_tool_result(MyFoodMatch.of(saved))
