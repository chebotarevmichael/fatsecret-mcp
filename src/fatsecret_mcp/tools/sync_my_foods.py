"""sync_my_foods: read what's new in the User's Food Diary into their catalog, now."""

from __future__ import annotations

from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, ToolAnnotations

from fatsecret_mcp.catalog.freshness import CatalogRefresher, NeverSynced
from fatsecret_mcp.tools.schemas import MyFoodMatch, MyFoodsSynced, as_tool_result


def register(server: MCPServer, refresher: CatalogRefresher) -> None:
    annotations = ToolAnnotations(
        title="Sync My Foods",
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
    )

    @server.tool(annotations=annotations)
    async def sync_my_foods() -> Annotated[CallToolResult, MyFoodsSynced]:
        """Read the Food Diary's recent days into the User's catalog, so search_my_foods finds new Foods.

        Call it when the User says they logged something new in the FatSecret app, or when
        search_my_foods doesn't find a Food they logged in the app recently. It reads the days since
        shortly before the last sync, usually a few requests, and lists the Foods and Servings that
        are new to the catalog. The server also does this once a day by itself. When FatSecret is
        throttling, it stops at once with status "stopped"; say so and offer to try again in a few
        minutes. It changes only the catalog, never the Food Diary.
        """
        try:
            refreshed = await refresher.refresh()
        except NeverSynced as error:
            raise ToolError(str(error)) from error

        report = refreshed.report
        synced = MyFoodsSynced(
            status="stopped" if report.stopped is not None else "synced",
            stopped_because=report.stopped,
            days_read=report.days_read,
            requests=report.requests,
            new=[MyFoodMatch.of(item) for item in refreshed.new_items],
        )

        return as_tool_result(synced)
