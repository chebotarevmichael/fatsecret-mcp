# Local catalog

A free (Basic) FatSecret key sees only the US catalog: `foods.search` doesn't find regional Foods and `food.get` answers error 106 for them. Your Food Diary still returns them, and `food_entry.create` logs them again by their `food_id` and `serving_id`, with FatSecret computing the nutrition. The local catalog remembers every Food and Serving you have logged, so they can be found and logged again.

## Filling it

```sh
fatsecret-mcp sync-catalog --user ola            # the first run reads your whole diary
fatsecret-mcp sync-catalog --user ola            # later runs re-read from three days before the last one
fatsecret-mcp sync-catalog --user ola --classify # also check which Foods the catalog can't return
```

It needs `FATSECRET_CONSUMER_KEY`, `FATSECRET_CONSUMER_SECRET` and `FATSECRET_MCP_USERS`, as `add-user` does. `--user` is optional when there's one person.

- A full run (the first, or `--full`) reads month totals backwards from this month until six months in a row are empty, then reads only the days that have entries. Days cleared in the app leave the catalog.
- Requests are paced as the server paces them. After error 12 (too many actions) the same request is sent again after one minute, then two, four, up to 32; a run stops after that and keeps what it read.
- `--budget` caps the requests in one run, 3,000 by default, out of FatSecret's 5,000 a day. A run that stops for any reason, the budget included, carries on where it stopped when started again.
- `--classify` calls `food.get` once for each Food not checked before: error 106 marks it regional. Checks are shared by everyone in the Household.

## What it keeps

One SQLite file, `catalog.sqlite3`, in `FATSECRET_MCP_DATA_DIR` (by default `./data`; `/data` in the container image, which declares it a volume).

- `catalog_entries`: each Food Entry read, with nutrition for one Serving (the entry's divided by its units).
- `catalog_items`: one row per person, Food and Serving: the latest entry name, a brand when the name starts with one in capitals, the Serving's description and its grams or ml when the entry was written in them, the nutrition per Serving, where it came from, how often and when it was logged, and whether it is regional (empty until checked).
- `catalog_overrides`: nutrition per Serving you gave yourself.
- `food_checks`, `sync_days`, `sync_state`: the regional checks and how far a sync got.

## Which nutrition is kept

FatSecret rounds each entry's kcal, so entries of a few grams disagree by a few kcal per Serving.

- When every logging agrees within that rounding, the median is kept (`source` is `median`).
- When they disagree beyond it, the Food's data most likely changed on FatSecret, so the latest loggings that agree with each other are kept (`latest`). If those differ from the median too, the Item is marked `needs_review` and logged as a warning.
- Values you give for a Serving are kept in `catalog_overrides` and win over the entries in every later rebuild (`override`).

The catalog is persistent, unlike the rest of this server, which keeps FatSecret data in memory for at most a day. It is meant for your own diary on your own server.
