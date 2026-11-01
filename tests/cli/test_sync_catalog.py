"""`sync-catalog`: the User from `FATSECRET_MCP_USERS`, the catalog file in the data directory, a summary."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from fatsecret_mcp.catalog.store import CatalogStore
from fatsecret_mcp.cli.sync_catalog import sync_catalog
from fatsecret_mcp.config.loader import HouseholdConfigError
from fatsecret_mcp.config.models import Household
from fatsecret_mcp.domain.models import Meal
from tests.cli.conftest import ENVIRON, ScriptedConsole
from tests.conftest import FakeClock
from tests.fatsecret.fake import FakeFatSecret, food_entry

pytestmark = pytest.mark.anyio


async def test_the_catalog_is_written_to_the_data_directory(
    fatsecret: FakeFatSecret, household: Household, clock: FakeClock, tmp_path: Path
) -> None:
    user = household.user("ola").fatsecret
    fatsecret.add_food_entry(user, food_entry("Kurt", Meal.SNACKS, date(2026, 10, 13), kcal=34))
    console = ScriptedConsole([])
    environ = ENVIRON | {"FATSECRET_MCP_DATA_DIR": str(tmp_path)}

    report = await sync_catalog(
        console, environ, fatsecret, user_id="ola", full=False, budget=100, classify=True, clock=clock
    )

    with CatalogStore.open(tmp_path / "catalog.sqlite3") as store:
        items = store.items("ola")

    assert [item.name for item in items] == ["Kurt"]
    assert items[0].is_regional is True
    assert report.requests == fatsecret.requests
    assert "ola: 1 Servings" in console.text
    assert "1 regional, 0 not checked" in console.text


async def test_a_household_of_several_needs_the_user_named(fatsecret: FakeFatSecret, tmp_path: Path) -> None:
    environ = ENVIRON | {"FATSECRET_MCP_DATA_DIR": str(tmp_path)}

    with pytest.raises(HouseholdConfigError, match="--user must name one of: jakub, ola"):
        await sync_catalog(
            ScriptedConsole([]), environ, fatsecret, user_id=None, full=False, budget=10, classify=False
        )

    assert fatsecret.requests == 0
