"""search_my_foods: the Foods and Servings the User has logged before, from their own catalog."""

from __future__ import annotations

from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, ToolAnnotations

from fatsecret_mcp.catalog.search import search
from fatsecret_mcp.catalog.store import CatalogStore
from fatsecret_mcp.config.models import User
from fatsecret_mcp.tools.schemas import FoodDescription, MyFoodMatch, MyFoodSearch, as_tool_result


def register(server: MCPServer, user: User, catalog: CatalogStore) -> None:
    annotations = ToolAnnotations(
        title="Search My Foods",
        read_only_hint=True,
    )

    @server.tool(annotations=annotations)
    async def search_my_foods(description: FoodDescription) -> Annotated[CallToolResult, MyFoodSearch]:
        """Find Foods the User has logged before, by name, in the User's own language.

        Search here first: it costs no FatSecret request and finds regional products search_foods
        can't, such as local brands. Use a few words of the name or brand as the User would say
        them, e.g. "курт" or "тартин злаковый". Matches come with the Serving they were logged in,
        what FatSecret logs per Serving and per 100 g or ml, and how often and when they were
        logged. Draft a match as a My Food item with draft_entries. A not_found result means the
        User hasn't logged it; then use search_foods. This tool does not write to the Food Diary.
        """
        if not description.strip():
            raise ToolError("description must not be blank.")

        matches = [MyFoodMatch.of(item) for item in search(catalog.items(user.id), description)]

        return as_tool_result(MyFoodSearch(status="found" if matches else "not_found", matches=matches))
