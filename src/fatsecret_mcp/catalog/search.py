"""Finding Catalog Items by name, in any letter case and with the Cyrillic "yo" written as "ye".

The catalog of one User holds hundreds of Items, so it's searched in memory. Every word of the query
must start a word of the name or brand, or occur in it; Items logged more often and more recently
come first.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from fatsecret_mcp.catalog.items import CatalogItem

WORD = re.compile(r"\w+")
MAX_RESULTS = 10


def search(items: Sequence[CatalogItem], query: str, *, max_results: int = MAX_RESULTS) -> list[CatalogItem]:
    words = _words(query)

    if not words:
        return []

    scored: list[tuple[int, CatalogItem]] = []

    for item in items:
        score = _score(words, _words(f"{item.name} {item.brand or ''}"))

        if score is not None:
            scored.append((score, item))

    scored.sort(key=lambda pair: (-pair[0], -pair[1].times_logged, -pair[1].last_seen.toordinal()))

    return [item for _, item in scored[:max_results]]


def _score(query: list[str], name: list[str]) -> int | None:
    """How well the name matches: 2 per word it starts, 1 per word inside it; None if a word is missing."""
    score = 0

    for word in query:
        if any(part.startswith(word) for part in name):
            score += 2
        elif any(word in part for part in name):
            score += 1
        else:
            return None

    return score


def _words(text: str) -> list[str]:
    return WORD.findall(text.casefold().replace("\u0451", "\u0435"))
