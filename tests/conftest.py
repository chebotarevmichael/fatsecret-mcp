from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime

import anyio.lowlevel
import pytest
from mcp import Client

from fatsecret_mcp.config.loader import load_household
from fatsecret_mcp.config.models import Household
from fatsecret_mcp.fatsecret.budget import daily_budget
from fatsecret_mcp.fatsecret.food_cache import FoodCache
from fatsecret_mcp.fatsecret.request_limits import RequestLimits
from fatsecret_mcp.server import build_server
from tests.fatsecret.fake import FakeFatSecret

HOUSEHOLD_USERS = [
    {
        "id": "jakub",
        "display_name": "Jakub",
        "fatsecret_access_token": "jakub-token",
        "fatsecret_access_secret": "jakub-secret",
        "daily_targets": {"kcal": 2500, "protein": 160, "fat": 80, "carbohydrate": 280},
    },
    {
        "id": "ola",
        "display_name": "Ola",
        "fatsecret_access_token": "ola-token",
        "fatsecret_access_secret": "ola-secret",
        "daily_targets": {"kcal": 1900, "protein": 110, "fat": 65, "carbohydrate": 210},
        "timezone": "Europe/Warsaw",
    },
]


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FakeRequestClock:
    """Monotonic time and cancellable pacing waits, without waiting in real time."""

    def __init__(self) -> None:
        self.elapsed = 0.0

    def __call__(self) -> float:
        return self.elapsed

    async def sleep(self, seconds: float) -> None:
        await anyio.lowlevel.checkpoint()

        self.elapsed += seconds


@pytest.fixture
def request_clock() -> FakeRequestClock:
    return FakeRequestClock()


@pytest.fixture
def request_limits(request_clock: FakeRequestClock) -> RequestLimits:
    return RequestLimits(
        now=request_clock,
        sleep=request_clock.sleep,
    )


@pytest.fixture(autouse=True)
def no_operator_catalog(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test outside the checkout, so a catalog the Operator synced in ./data isn't read."""
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def household() -> Household:
    return load_household(
        {
            "FATSECRET_CONSUMER_KEY": "consumer-key",
            "FATSECRET_CONSUMER_SECRET": "consumer-secret",
            "FATSECRET_MCP_SIGNING_KEY": "test-signing-key-0123456789abcdef",
            "FATSECRET_MCP_USERS": json.dumps(HOUSEHOLD_USERS),
        }
    )


@pytest.fixture
def fatsecret(household: Household) -> FakeFatSecret:
    fake = FakeFatSecret()

    for user in household.users:
        fake.add_account(user.fatsecret)
    return fake


@pytest.fixture
def clock() -> FakeClock:
    """The server's clock runs in UTC; noon on 2026-10-14 in Warsaw."""
    return FakeClock(datetime(2026, 10, 14, 10, 0, tzinfo=UTC))


@pytest.fixture
def food_cache(clock: FakeClock) -> FoodCache:
    """The Food cache every User's server of one test shares, as on a running server."""
    return FoodCache(clock=clock)


@pytest.fixture
def connect(
    household: Household, fatsecret: FakeFatSecret, clock: FakeClock, food_cache: FoodCache
) -> Callable[[str], Client]:
    """An in-memory MCP client session with the real server, acting as one User of the Household."""

    def connect(user_id: str) -> Client:
        server = build_server(
            user=household.user(user_id),
            fatsecret=fatsecret,
            clock=clock,
            signing_key=household.signing_key,
            daily_requests=daily_budget(len(household.users)),
            food_cache=food_cache,
        )

        return Client(server)

    return connect
