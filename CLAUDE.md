# CLAUDE.md

Guidance for future Claude Code sessions working in this repo.

## What this project is

Single-user, local-first personal health & training tool. Pulls wellness +
activity data from **Intervals.icu** (which already aggregates Garmin data —
no direct Garmin Connect integration), computes a deterministic rule-based
readiness score each morning, and recommends today's workout from a Markdown-
based local library. Runs entirely offline; **no external LLM calls; health
data never leaves the machine.** Tested against a Garmin Forerunner 255 only
— other devices may need `config/sport_aliases.yml` updates.

## Common commands

```bash
uv sync --extra dev         # set up venv and install all deps incl. dev tools
uv run pytest -q            # run the test suite (uses respx fixtures, no live API)
uv run pytest tests/test_scoring.py::test_compute_readiness_green_for_healthy_user
uv run ruff check src tests
uv run ruff check src tests --fix

# Application
uv run health init                            # writes sample config + workouts (idempotent)
uv run health sync --days 30                  # pull wellness/activities from Intervals.icu
uv run health sync --force-details            # force re-fetch of cached activity intervals
uv run health today --skip-sync               # compute readiness without hitting the API
uv run health report weekly                   # rolling 7-day report
uv run health report monthly
uv run health dashboard                        # regenerate + open data/reports/dashboard.html
uv run health dashboard --no-open             # write the file but don't auto-open
uv run health doctor                          # env + config + DB + library sanity check
```

## High-level architecture

```
CLI (Typer)
   │
   ├── ingest ──→ intervals_client ──→ data/raw/intervals/**/*.json
   │                                        │
   │                                        ▼
   │                              storage (SQLite normalization, ALTER TABLE migrations)
   │                                        │
   ├── metrics ◀────────────────────────────┤   (rolling baselines, ACWR, weekly aggregates)
   │     │
   │     ▼
   ├── scoring ──→ readiness_score + sub-scores + risk flags + confidence
   │     │
   │     ▼
   ├── workout_library (Markdown → typed Workout objects)
   │     │
   │     ▼
   ├── recommender ──→ ranked picks + alternatives + reasons
   │     │
   │     ▼
   ├── reports ──→ data/reports/{daily,weekly,monthly}/*.{md,json}
   │
   └── dashboard ──→ data/reports/dashboard.html   (static, no JS, no server)
```

Each module in `src/health_manager/` maps 1:1 to a layer above. Layers are
pure functions over the layer below where possible, so they're individually
testable with local fixtures (`tests/fixtures/*.json`).

## Things worth knowing before touching code

- **Schema migrations live in `storage.py::_MIGRATIONS`** — an ordered list
  of `(from_v, to_v, callable)` tuples returning ALTER TABLE statements.
  When you change `wellness_daily` / `activities` columns: bump
  `SCHEMA_VERSION`, append a new migration function. Do **not** drop tables;
  the user's manual entries in `manual_body_metrics` / `daily_checkins`
  must survive.
- **Activity sport** is stored both raw (`sport` column, e.g. `Run`,
  `EBikeRide`) and canonical (`sport_canonical` column, e.g. `run`, `bike`).
  Downstream code keys off `sport_canonical`. Mapping lives in
  `config/sport_aliases.yml` and is loaded via `config.load_sport_aliases`.
  To support a new device source, edit the YAML — no code change needed.
- **Intervals.icu activity detail (intervals)** is cached on disk at
  `data/raw/intervals/activity_details/<id>.json`. Sync skips the network
  call when the file exists. Pass `--force-details` to bypass.
- **Auto-sync from `health today`** uses a 7-day window (not 30, not 180) to
  stay cheap. The on-disk cache keeps it almost free even repeated.
- **Readiness score** is deterministic and rule-based; weights live in
  `config/scoring.yml`. Magic numbers should not appear in `scoring.py`.
- **`Goals`** parse from `config/goals.md` YAML front matter; the body is
  free-form "narrative" notes preserved as `Goals.narrative_notes`.
- **Daily / weekly / monthly reports** write JSON first (authoritative) and
  render Markdown from it. The dashboard reads the latest `*.json` for each
  period and produces a single self-contained HTML file.
- **The static HTML dashboard** is pure HTML/CSS — no JavaScript, no
  external assets. Dark mode rides on `@media (prefers-color-scheme: dark)`.
  CSS lives inline as `_CSS` in `dashboard.py`.

## Things that are intentionally NOT here

- Direct Garmin Connect integration. Intervals.icu does it; we depend on it.
- Any external LLM / network call beyond Intervals.icu.
- Multi-user, auth, sharing.
- A real web server. The "dashboard" is a static file.
- Schema migrations that delete data. (See note above.)
- Streamlit / a runtime UI framework. (Used to be Streamlit; ripped out for
  the static HTML approach.)

## Tests

- Live in `tests/`, use `respx` to mock `httpx` for `intervals_client`.
- `tests/fixtures/*.json` hold canned API responses.
- Tests do **not** hit Intervals.icu and do **not** touch real config files
  (they construct `Settings` directly with `tmp_path`).
- Run a single test: `uv run pytest tests/test_scoring.py -k green`.

## Coding conventions

- Python 3.12. Use `from __future__ import annotations` at top of every
  module. Prefer `list[X]` over `List[X]`, `X | None` over `Optional[X]`.
- Pure functions when possible. Side effects (filesystem, SQLite, network)
  are isolated to `cli.py`, `ingest.py`, `intervals_client.py`, `storage.py`,
  `reports.py`, and `dashboard.py`.
- Pydantic for config models, validated at load time.
- Raw `sqlite3` for writes; `pandas.read_sql` for reads. No ORM.
- Inline `# Why: …` comments only when the *reason* isn't obvious from the
  code. Don't narrate what the code does.
- No `Co-Authored-By:` trailers on commits.

## Where to look for X

- **Add a new wellness field**: `config/metric_mapping.yml` (raw → canonical
  name), `storage.py::SCHEMA_STATEMENTS` (column), `storage.py::_WELLNESS_COLS`
  (upsert column list), `storage.py::_MIGRATIONS` (ALTER TABLE for existing
  DBs), `ingest.py::_KNOWN_WELLNESS` (so it's not logged as "unknown").
- **Add a new workout**: drop a Markdown file under `workouts/<sport>/`.
  Front-matter `id` must be globally unique.
- **Tweak readiness weights**: `config/scoring.yml`. Code reads it via
  `config.load_scoring`.
- **Tweak guardrails** (max consecutive days, no-hard threshold, etc.):
  `config/goals.md` front-matter.
- **Change the dashboard look**: `src/health_manager/dashboard.py::_CSS` and
  the `_section_*` renderers.
