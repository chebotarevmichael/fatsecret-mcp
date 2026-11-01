"""`sync-catalog`: fill a User's catalog from their Food Diary, and optionally find its regional Foods.

It needs only the consumer key and `FATSECRET_MCP_USERS`, as `add-user` does, plus a writable
`FATSECRET_MCP_DATA_DIR` for the catalog file.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

import anyio

from fatsecret_mcp.catalog.store import CatalogStore, database_path
from fatsecret_mcp.catalog.sync import CatalogSync, RunReport
from fatsecret_mcp.cli.console import Console
from fatsecret_mcp.config.loader import USERS_VAR, HouseholdConfigError, parse_users
from fatsecret_mcp.config.models import User
from fatsecret_mcp.domain.diary_day import Clock, system_clock, today
from fatsecret_mcp.fatsecret.gateway import FatSecretGateway


async def sync_catalog(
    console: Console,
    environ: Mapping[str, str],
    fatsecret: FatSecretGateway,
    *,
    user_id: str | None,
    full: bool,
    budget: int,
    classify: bool,
    clock: Clock = system_clock,
    sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
) -> RunReport:
    user = _user(environ, user_id)
    diary_day = today(clock(), user.timezone)
    path = database_path(environ)

    with CatalogStore.open(path) as store:
        sync = CatalogSync(
            fatsecret, store, user.id, user.fatsecret, budget=budget, sleep=sleep, progress=console.tell
        )
        report = await sync.sync(diary_day, full=full)

        if classify and report.stopped is None:
            report = await sync.classify(diary_day)

        items = store.items(user.id)

    regional = sum(1 for item in items if item.is_regional)
    unknown = sum(1 for item in items if item.is_regional is None)

    console.tell(
        f"{user.id}: {len(items)} Servings in {path}; {regional} regional, {unknown} not checked. "
        f"{report.requests} requests, error 12 {report.throttled} times, "
        f"{len(report.spreads)} Servings with disagreeing entries."
    )

    if report.stopped is not None:
        console.tell(f"Stopped early: {report.stopped}. Run the command again to carry on.")

    return report


def _user(environ: Mapping[str, str], user_id: str | None) -> User:
    value = environ.get(USERS_VAR, "").strip()

    if not value:
        raise HouseholdConfigError(f"{USERS_VAR} is not set")

    users = parse_users(value)

    if user_id is None:
        if len(users) == 1:
            return users[0]

        raise HouseholdConfigError(f"--user must name one of: {', '.join(user.id for user in users)}")

    for user in users:
        if user.id == user_id:
            return user

    raise HouseholdConfigError(f"--user names no User in {USERS_VAR}")
