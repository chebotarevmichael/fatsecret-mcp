"""The catalog on disk: one SQLite file in `FATSECRET_MCP_DATA_DIR`, by default `./data`.

- `catalog_entries`: every Food Entry read from a User's Food Diary, as one logging of a Serving.
  A Diary Day read again replaces its entries, so edits and deletions in the app carry over.
- `catalog_items`: one row per User, Food and Serving, rebuilt from `catalog_entries`.
- `food_checks`: whether FatSecret's catalog returns a Food to this API key. Shared by Users, since the
  catalog is the same for all of them; each Food is checked once.
- `sync_days` and `sync_state`: how far a sync got, so an interrupted one carries on where it stopped.

Each write commits at once, so a run stopped at any point keeps what it read.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Generator, Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any

from fatsecret_mcp.catalog.items import CatalogItem, Observation, Spread, catalog_item
from fatsecret_mcp.domain.models import Nutrition

DATA_DIR_VAR = "FATSECRET_MCP_DATA_DIR"
DEFAULT_DATA_DIR = Path("data")
DATABASE_NAME = "catalog.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog_entries (
    user TEXT NOT NULL,
    food_entry_id TEXT NOT NULL,
    diary_day TEXT NOT NULL,
    food_id TEXT NOT NULL,
    serving_id TEXT NOT NULL,
    name TEXT NOT NULL,
    number_of_units REAL NOT NULL,
    serving_description TEXT,
    metric_amount REAL,
    metric_unit TEXT,
    kcal REAL NOT NULL,
    protein REAL NOT NULL,
    fat REAL NOT NULL,
    carbohydrate REAL NOT NULL,
    PRIMARY KEY (user, food_entry_id)
);
CREATE INDEX IF NOT EXISTS catalog_entries_by_day ON catalog_entries (user, diary_day);

CREATE TABLE IF NOT EXISTS catalog_items (
    user TEXT NOT NULL,
    food_id TEXT NOT NULL,
    serving_id TEXT NOT NULL,
    name TEXT NOT NULL,
    brand TEXT,
    serving_description TEXT,
    metric_amount REAL,
    metric_unit TEXT,
    kcal REAL NOT NULL,
    protein REAL NOT NULL,
    fat REAL NOT NULL,
    carbohydrate REAL NOT NULL,
    times_logged INTEGER NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    is_regional INTEGER,
    PRIMARY KEY (user, food_id, serving_id)
);

CREATE TABLE IF NOT EXISTS food_checks (
    food_id TEXT PRIMARY KEY,
    is_regional INTEGER NOT NULL,
    checked_on TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_days (
    user TEXT NOT NULL,
    diary_day TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user, diary_day)
);

CREATE TABLE IF NOT EXISTS sync_state (
    user TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    phase TEXT NOT NULL,
    next_month TEXT NOT NULL,
    stop_month TEXT,
    empty_months INTEGER NOT NULL,
    since TEXT,
    started_on TEXT NOT NULL,
    last_completed_on TEXT,
    full_completed INTEGER NOT NULL DEFAULT 0
);
"""


class SyncMode(StrEnum):
    FULL = "full"
    INCREMENTAL = "incremental"


class SyncPhase(StrEnum):
    MONTHS = "months"
    """Reading month totals backwards to find the Diary Days that have Food Entries."""
    DAYS = "days"
    """Reading the Food Entries of each Diary Day found."""
    DONE = "done"


@dataclass(frozen=True)
class SyncState:
    mode: SyncMode
    phase: SyncPhase
    next_month: date
    """The first day of the next month to read totals for."""
    stop_month: date | None
    """An incremental sync reads months back to this one; a full sync reads until enough are empty."""
    empty_months: int
    """Consecutive months without Food Entries so far, for a full sync."""
    since: date | None
    """An incremental sync re-reads Diary Days from this one on."""
    started_on: date
    last_completed_on: date | None
    """The Diary Day the last finished sync ran on, from which the next incremental sync starts."""
    full_completed: bool


def database_path(environ: Mapping[str, str]) -> Path:
    data_dir = environ.get(DATA_DIR_VAR, "").strip()

    return (Path(data_dir) if data_dir else DEFAULT_DATA_DIR) / DATABASE_NAME


class CatalogStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)

    @classmethod
    @contextmanager
    def open(cls, path: Path) -> Generator[CatalogStore]:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)

        try:
            yield cls(connection)
        finally:
            connection.close()

    def state(self, user: str) -> SyncState | None:
        row = self._db.execute("SELECT * FROM sync_state WHERE user = ?", (user,)).fetchone()

        if row is None:
            return None

        return SyncState(
            mode=SyncMode(row["mode"]),
            phase=SyncPhase(row["phase"]),
            next_month=date.fromisoformat(row["next_month"]),
            stop_month=_optional_day(row["stop_month"]),
            empty_months=row["empty_months"],
            since=_optional_day(row["since"]),
            started_on=date.fromisoformat(row["started_on"]),
            last_completed_on=_optional_day(row["last_completed_on"]),
            full_completed=bool(row["full_completed"]),
        )

    def save_state(self, user: str, state: SyncState) -> None:
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO sync_state VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user,
                    state.mode.value,
                    state.phase.value,
                    state.next_month.isoformat(),
                    _optional_text(state.stop_month),
                    state.empty_months,
                    _optional_text(state.since),
                    state.started_on.isoformat(),
                    _optional_text(state.last_completed_on),
                    int(state.full_completed),
                ),
            )

    def start_days(self, user: str) -> None:
        """Forget the Diary Days of an earlier sync before a new one finds its own."""
        with self._db:
            self._db.execute("DELETE FROM sync_days WHERE user = ?", (user,))

    def add_days(self, user: str, days: Iterable[date]) -> None:
        with self._db:
            self._db.executemany(
                "INSERT OR IGNORE INTO sync_days (user, diary_day) VALUES (?, ?)",
                [(user, day.isoformat()) for day in days],
            )

    def pending_days(self, user: str) -> list[date]:
        """The Diary Days still to read, newest first."""
        rows = self._db.execute(
            "SELECT diary_day FROM sync_days WHERE user = ? AND done = 0 ORDER BY diary_day DESC", (user,)
        )

        return [date.fromisoformat(row["diary_day"]) for row in rows]

    def found_days(self, user: str) -> int:
        row = self._db.execute("SELECT COUNT(*) FROM sync_days WHERE user = ?", (user,)).fetchone()

        return int(row[0])

    def replace_day(self, user: str, day: date, observations: Iterable[Observation]) -> None:
        """Keep exactly these loggings for the Diary Day, and mark it read."""
        rows = [
            (
                user,
                logged.food_entry_id,
                day.isoformat(),
                logged.food_id,
                logged.serving_id,
                logged.name,
                logged.number_of_units,
                logged.serving_description,
                logged.metric_amount,
                logged.metric_unit,
                logged.per_serving.kcal,
                logged.per_serving.protein,
                logged.per_serving.fat,
                logged.per_serving.carbohydrate,
            )
            for logged in observations
        ]

        with self._db:
            self._db.execute(
                "DELETE FROM catalog_entries WHERE user = ? AND diary_day = ?", (user, day.isoformat())
            )
            self._db.executemany(
                "INSERT OR REPLACE INTO catalog_entries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            self._db.execute(
                "UPDATE sync_days SET done = 1 WHERE user = ? AND diary_day = ?", (user, day.isoformat())
            )

    def drop_unfound_days(self, user: str) -> None:
        """After a full sync, forget loggings on Diary Days it didn't find: they were cleared in the app."""
        with self._db:
            self._db.execute(
                """
                DELETE FROM catalog_entries WHERE user = ?
                AND diary_day NOT IN (SELECT diary_day FROM sync_days WHERE user = ?)
                """,
                (user, user),
            )

    def days_with_entries(self, user: str, since: date) -> set[date]:
        rows = self._db.execute(
            "SELECT DISTINCT diary_day FROM catalog_entries WHERE user = ? AND diary_day >= ?",
            (user, since.isoformat()),
        )

        return {date.fromisoformat(row["diary_day"]) for row in rows}

    def rebuild_items(self, user: str) -> list[Spread]:
        """Catalog Items from every logging kept for the User, and the Servings whose loggings disagree."""
        rows = self._db.execute(
            "SELECT * FROM catalog_entries WHERE user = ? ORDER BY food_id, serving_id", (user,)
        ).fetchall()
        by_serving: dict[tuple[str, str], list[Observation]] = {}

        for row in rows:
            by_serving.setdefault((row["food_id"], row["serving_id"]), []).append(_observation(row))

        checks = self._checks()
        items: list[CatalogItem] = []
        spreads: list[Spread] = []

        for observations in by_serving.values():
            item, spread = catalog_item(observations)
            items.append(item)

            if spread is not None:
                spreads.append(spread)

        with self._db:
            self._db.execute("DELETE FROM catalog_items WHERE user = ?", (user,))
            self._db.executemany(
                "INSERT INTO catalog_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [_item_row(user, item, checks.get(item.food_id)) for item in items],
            )

        return spreads

    def items(self, user: str) -> list[CatalogItem]:
        """The User's Catalog Items, most logged first."""
        rows = self._db.execute(
            "SELECT * FROM catalog_items WHERE user = ? ORDER BY times_logged DESC, last_seen DESC, name",
            (user,),
        )

        return [_item(row) for row in rows]

    def unchecked_food_ids(self, user: str) -> list[str]:
        """The User's Foods not yet checked against FatSecret's catalog, most logged first."""
        rows = self._db.execute(
            """
            SELECT food_id FROM catalog_items
            WHERE user = ? AND food_id NOT IN (SELECT food_id FROM food_checks)
            GROUP BY food_id ORDER BY SUM(times_logged) DESC, food_id
            """,
            (user,),
        )

        return [row["food_id"] for row in rows]

    def record_check(self, food_id: str, *, is_regional: bool, checked_on: date) -> None:
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO food_checks VALUES (?, ?, ?)",
                (food_id, int(is_regional), checked_on.isoformat()),
            )
            self._db.execute(
                "UPDATE catalog_items SET is_regional = ? WHERE food_id = ?", (int(is_regional), food_id)
            )

    def _checks(self) -> dict[str, bool]:
        rows = self._db.execute("SELECT food_id, is_regional FROM food_checks")

        return {row["food_id"]: bool(row["is_regional"]) for row in rows}


def _observation(row: sqlite3.Row) -> Observation:
    return Observation(
        food_entry_id=row["food_entry_id"],
        food_id=row["food_id"],
        serving_id=row["serving_id"],
        diary_day=date.fromisoformat(row["diary_day"]),
        name=row["name"],
        number_of_units=row["number_of_units"],
        serving_description=row["serving_description"],
        metric_amount=row["metric_amount"],
        metric_unit=row["metric_unit"],
        per_serving=_nutrition(row),
    )


def _item(row: sqlite3.Row) -> CatalogItem:
    regional = row["is_regional"]

    return CatalogItem(
        food_id=row["food_id"],
        serving_id=row["serving_id"],
        name=row["name"],
        brand=row["brand"],
        serving_description=row["serving_description"],
        metric_amount=row["metric_amount"],
        metric_unit=row["metric_unit"],
        per_serving=_nutrition(row),
        times_logged=row["times_logged"],
        first_seen=date.fromisoformat(row["first_seen"]),
        last_seen=date.fromisoformat(row["last_seen"]),
        is_regional=None if regional is None else bool(regional),
    )


def _item_row(user: str, item: CatalogItem, is_regional: bool | None) -> tuple[Any, ...]:
    return (
        user,
        item.food_id,
        item.serving_id,
        item.name,
        item.brand,
        item.serving_description,
        item.metric_amount,
        item.metric_unit,
        item.per_serving.kcal,
        item.per_serving.protein,
        item.per_serving.fat,
        item.per_serving.carbohydrate,
        item.times_logged,
        item.first_seen.isoformat(),
        item.last_seen.isoformat(),
        None if is_regional is None else int(is_regional),
    )


def _nutrition(row: sqlite3.Row) -> Nutrition:
    return Nutrition(
        kcal=row["kcal"],
        protein=row["protein"],
        fat=row["fat"],
        carbohydrate=row["carbohydrate"],
    )


def _optional_day(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value)


def _optional_text(value: date | None) -> str | None:
    return None if value is None else value.isoformat()
