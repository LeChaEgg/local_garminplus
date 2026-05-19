# health-manager

Local-first personal health management and daily training recommender. Sources wellness and activity data from [Intervals.icu](https://intervals.icu) (which already aggregates Garmin data), computes a transparent rule-based readiness score, and picks today's workout from a Markdown-based local library.

Designed for a single user. All data stays on disk; nothing is sent to any external LLM service.

> **Tested data source:** this repo has only been exercised with a **Garmin Forerunner 255** syncing into **Intervals.icu**. Other watches / data sources (Apple Watch, Wahoo, Polar, COROS, etc.) and other Garmin models are *not guaranteed* to work — sport-name labels, intensity scales, sleep-duration units, and HRV methodology can differ, which would silently degrade weekly goal accounting and the `is_hard` heuristic even though the code won't crash. Use with caution outside this tested setup.

## Stack

Python 3.12 · uv · Typer · httpx · pydantic · pandas · sqlite3 · pytest. The dashboard is a self-contained static HTML file (no JS, no server) regenerated from SQLite + reports on each run.

## Setup

```bash
uv sync --extra dev
cp .env.example .env
# edit .env and set INTERVALS_API_KEY
uv run health init       # writes sample config + workouts (idempotent)
uv run health sync --days 180
uv run health today
uv run health dashboard
```

## CLI

| Command | Purpose |
| --- | --- |
| `health init` | Create sample configs, manual CSV templates, and sample workouts. Safe to re-run. |
| `health sync --days N` | Pull recent wellness + activities from Intervals.icu into `data/raw/` and SQLite. |
| `health today` | Sync (if stale), compute readiness, pick today's workout, write `data/reports/daily/YYYY-MM-DD.{md,json}`, print summary. |
| `health report weekly` | Write the current ISO-week report. |
| `health report monthly` | Write the current calendar-month report. |
| `health dashboard` | Regenerate `data/reports/dashboard.html` from the latest data and open it in your default browser (`--no-open` to skip the auto-open). |

## Layout

```
config/             goals.md, scoring.yml, metric_mapping.yml
data/raw/           Original Intervals.icu JSON responses
data/processed/     health.sqlite
data/manual/        body_metrics.csv, daily_checkins.csv
data/reports/       daily/, weekly/, monthly/ Markdown + JSON
workouts/           run/, bike/, strength/, recovery/ Markdown files
src/health_manager/ Source code
tests/              Test suite using local fixtures
```

## Configuration

- **`config/goals.md`** — YAML front matter holds structured goals (profile, weights, guardrails, weekly aerobic/strength targets). The Markdown body can hold freeform "fuzzy" goals for future review.
- **`config/scoring.yml`** — readiness weights, thresholds, baseline windows.
- **`config/metric_mapping.yml`** — Intervals.icu field name → canonical field. Unknown fields are logged, not crashed.

## Design notes

- **Deterministic, rule-based.** No ML or LLM in the recommender. All weights live in YAML; the daily report cites the reasons each filter and ranking term fired.
- **Raw data is preserved.** Every API response is written to `data/raw/` before normalization, so the SQLite tables can always be rebuilt.
- **Inspectable.** SQLite is the source of truth for processed data; open it with `sqlite3 data/processed/health.sqlite` any time.
- **Strength workouts** use structured Markdown sections (`## Warmup` / `## Main` / `## Cooldown`) with bulleted lines; run/bike workouts use Intervals.icu's plain-text workout syntax.

## Testing

```bash
uv run pytest -q
uv run ruff check src tests
```

Tests use canned API fixtures (`tests/fixtures/`) and never call the live Intervals.icu API.
