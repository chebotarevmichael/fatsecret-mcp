"""`serve`: run the server locally over stdio, or serve the Household's connector over HTTP.

Heavy modules are imported only when needed, so the HTTP server starts quickly after a cold start.
"""

from __future__ import annotations

from collections.abc import Generator, Mapping
from contextlib import contextmanager
from typing import TYPE_CHECKING, Literal

from fatsecret_mcp.config.loader import acting_user, load_household, validate_http_household

if TYPE_CHECKING:
    from fatsecret_mcp.catalog.store import CatalogStore
    from fatsecret_mcp.config.models import Household, User

Transport = Literal["stdio", "http"]


async def serve(environ: Mapping[str, str], *, transport: Transport, host: str, port: int) -> None:
    household = load_household(environ)

    if transport == "http":
        validate_http_household(household)

    with _catalog(environ) as catalog:
        if transport == "http":
            await _serve_http(household, host, port, catalog)
        else:
            await _serve_stdio(household, acting_user(household, environ), catalog)


@contextmanager
def _catalog(environ: Mapping[str, str]) -> Generator[CatalogStore | None]:
    """The catalog `sync-catalog` filled, when there is one; the server never creates it."""
    from fatsecret_mcp.catalog.store import CatalogStore, database_path

    path = database_path(environ)

    if not path.is_file():
        yield None
        return

    with CatalogStore.open(path) as catalog:
        yield catalog


async def _serve_stdio(household: Household, user: User, catalog: CatalogStore | None) -> None:
    from fatsecret_mcp.domain.diary_day import system_clock
    from fatsecret_mcp.fatsecret.budget import daily_budget
    from fatsecret_mcp.fatsecret.client import connected_api
    from fatsecret_mcp.fatsecret.food_cache import FoodCache
    from fatsecret_mcp.server import build_server

    food_cache = FoodCache(clock=system_clock)

    async with connected_api(household.consumer) as fatsecret, food_cache.lifespan():
        server = build_server(
            user=user,
            fatsecret=fatsecret,
            signing_key=household.signing_key,
            daily_requests=daily_budget(len(household.users)),
            food_cache=food_cache,
            catalog=catalog,
            refresh_catalog_daily=True,
        )

        await server.run_stdio_async()


async def _serve_http(household: Household, host: str, port: int, catalog: CatalogStore | None) -> None:
    import uvicorn

    from fatsecret_mcp.fatsecret.client import connected_api
    from fatsecret_mcp.http import build_http_app

    async with connected_api(household.consumer) as fatsecret:
        # Without a catalog the app is built exactly as before it existed.
        if catalog is None:
            app = build_http_app(household, fatsecret)
        else:
            app = build_http_app(household, fatsecret, catalog=catalog)
        config = uvicorn.Config(
            app=app,
            host=host,
            port=port,
            access_log=False,
            log_level="warning",
        )
        server = uvicorn.Server(config)

        await server.serve()
