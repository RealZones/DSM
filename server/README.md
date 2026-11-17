# DSM server

The market service behind [dsmnet.xyz](https://dsmnet.xyz/). It replays every recorded auction
and bazaar trade to build prices, then follows new ones as they arrive, and serves the site from
`site/`. Python 3.10+, standard library only.

| File | What it does |
| --- | --- |
| `run.py` | Starts it: builds the market from history, follows new sales, serves the site. |
| `market/engine.py` | Prices an item from recent sales of copies like it (see `NOTICE` for what is adapted). |
| `market/bazaar.py` | Bazaar prices read from the order book, since Dragsim never publishes sale prices. |
| `market/crafts.py` | What a recipe really costs, walked through live supply cheapest first. |
| `market/tracker.py` | Pairs a player's sales with their purchases into flips. |
| `market/lore.py` | The price lines the mod shows under an item. |
| `market/web.py` | The site, the mod's endpoints and account linking. |
| `market/service.py` | Holds it all in memory and keeps it current. |
| `tools/` | The backtest, log import for trade history, daily database backups, an uptime alert. |

## Running it

```bash
python run.py --source /path/to/prices.db --data ./data --owner YourMinecraftName --key-file ./market.key
```

- `--source` is the collector's SQLite database of auction sales and listings and bazaar quotes
  (`sales`, `listings` and `quotes` tables). The collector itself is a separate program and is not
  part of this repository.
- `--data` is where the service keeps its own files: the item catalog and recipes, the trades the
  mod sends (`own.db`), linked accounts (`link.json`, `linked-auth.db`), reported trades and
  settings. **These are private and never belong in this repository.** Only
  `data/recipe_overrides.json`, plain game recipe data, is included.
- `--owner` (or `DSM_OWNER`) is the Minecraft name whose own trades become tracked flips on the
  owner's site.
- `--key-file` holds the owner site's key: the owner view lives at `/market/<key>/`, and everyone
  else uses `/market/link/`.

## Tests

```bash
python -m unittest discover -s tests
```

They build small fake collector databases and drive the real service, so they need no network
and no live data.
