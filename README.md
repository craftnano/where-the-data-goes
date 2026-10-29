# where-the-data-goes

A public data-art project mapping the sub-processor food chain across every tech company with a public sub-processor list we can find. Read one list and it's a page of vendor names. Read a thousand and a shape appears: the same infrastructure, communications, and AI providers recurring everywhere, each relying on others in turn, several hops deep. The piece draws what the companies disclosed and lets the shape speak. See `where-the-data-goes-brief.md` for the full brief.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
cp .env.example .env    # then paste your keys in
```

`.env` needs:

- `ANTHROPIC_API_KEY`: model calls
- `BRAVE_API_KEY`: the `search` tool

Model IDs, prices, per-run spend cap, Phase 1 cumulative ceiling, and polite-crawling defaults live in `config.py`.

## Running the agent

One company:

```bash
.venv/bin/python agent.py --company "Zoom" --website "https://zoom.us"
```

A seed set (10 concurrent):

```bash
.venv/bin/python agent.py --seeds data/nasdaq100_seeds.csv --concurrent 10
```

Options: `--offset N` (skip the first N rows), `--limit N`, `--hint "..."` (extra context appended to the per-company prompt; useful when you know the exact URL or need to override a default rule like "skip affiliates"), `--run-id <id>` (resume an existing run; already-completed companies are skipped).

Each run writes:

- `data/runs/<run_id>/state.json`: resume state and per-run cost
- `data/transcripts/<run_id>/<company>.json`: the agent's full tool-call history and the final `report` (gitignored)
- `data/snapshots/<run_id>/<hash>.{html,pdf,meta.json}`: raw copies of every page the agent opened (gitignored)

Cumulative spend across every run is persisted in `data/spend_ledger.json`; the agent refuses to start a new run once Phase 1's `$50` ceiling is reached.

Three seed sets ship with the repo:

- `data/nasdaq100_seeds.csv`: Nasdaq 100 constituents (Alphabet dual-class collapsed to one row)
- `data/sp100_seeds.csv`: S&P 100 constituents (33 overlap with Nasdaq)
- `data/cloud100_2025_seeds.csv`: Forbes Cloud 100 (2025)

`data/manual/` holds human-curated observations (one JSON file per company) that get merged into the export as `source: "manual"` edges. Useful when a live page blocks and no acceptable archive capture exists.

## Exporting `graph.json`

```bash
.venv/bin/python scripts/export_json.py --run-ids run-A,run-B,run-C
```

The exporter walks every transcript across the listed runs, picks the latest per `(seed, product_scope)`, applies the alias table in `data/aliases.yaml` to collapse name variants, loads manual observations from `data/manual/`, and writes `web/graph.json` (so the prototype auto-loads it) with:

- `nodes[]`: `id`, `name`, `kind` (`seed` / `sub-processor` / `affiliate-group` / `unnamed`), `seed_sets` (`"nasdaq100"`, `"sp100"`, `"cloud100_2025"`, or empty for snowball-only)
- `edges[]`: `from`, `to`, `purpose_category`, `purpose_text`, `first_seen`, `last_seen`, `source` (`"live"` / `"archive"` / `"manual"`), `seed_sets` (inherited from the source node), optional `product_scope`, optional `purpose_paraphrased` (manual edges), optional `to_detail` (Meta affiliate rows that collapse to a shared node but retain their raw entity name)

For a batch-run report (disclosure breakdown per seed set, Haiku-vs-Sonnet hit rates, top-20 most-named sub-processors, miss transcripts):

```bash
.venv/bin/python scripts/report.py \
  --run-ids run-A,run-B \
  --sets nasdaq100=data/nasdaq100_seeds.csv,sp100=data/sp100_seeds.csv,cloud100_2025=data/cloud100_2025_seeds.csv
```

## Viewing the prototype

`web/index.html` is a self-contained three.js prototype. It auto-fetches `./graph.json` from the same directory on load, so any web host serves it with no build step. To view locally, serve the `web/` folder (e.g. `python3 -m http.server --directory web 8000` → open `http://localhost:8000/`); browsers block `fetch()` from `file://` URLs, so double-clicking the HTML won't load the dataset.

For a Netlify deploy, point the site at this repo with publish directory `web/` and no build command. `web/index.html` and `web/graph.json` are the two files it serves.
