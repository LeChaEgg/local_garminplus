# Dashboard Redesign With Local Chart.js

## Summary
Redesign the dashboard as a “daily cockpit”: the first screen should answer `How ready am I?`, `What should I do today?`, `Why?`, and `Is the data fresh?` without scrolling. Keep the app local-first and offline; add a chart library only as a vendored, local asset with no runtime network calls.

Chart.js is a good fit here because its official docs support plain browser/UMD usage, so it can be bundled locally and used without a framework or server dependency: [Chart.js installation](https://www.chartjs.org/docs/latest/getting-started/installation.html), [Chart.js integration](https://www.chartjs.org/docs/latest/getting-started/integration.html).

## Key Changes
- Rework `src/health_manager/dashboard.py` into a clearer hierarchy:
  - Top cockpit: readiness score, level, recommended workout, conservative/progressive alternatives, hard blockers, and freshness.
  - “Why today” section: convert raw reason bullets into grouped sleep/recovery/load explanations.
  - “Training week” section: weekly goal gaps, hard-session cap, strength-session gap, minutes by sport.
  - “Trends” section: interactive charts for HRV, resting HR, sleep, load, and daily training load.
  - Lower-priority sections: recent activities, goals, workout library, and data quality.
- Add vendored Chart.js UMD asset under the repo, inline it into generated `dashboard.html`, and include its license notice.
  - No CDN.
  - No external assets.
  - No health data leaves the machine.
  - No npm or build pipeline.
- Replace the current static sparkline-only trend cards with Chart.js-enhanced charts plus HTML fallback summaries.
  - Use category labels for dates to avoid adding a date adapter dependency.
  - Keep charts compact and scan-friendly, not full analytics dashboards.
- Update CSS to a more polished operational UI:
  - Remove the heavy blue gradient header.
  - Use neutral surfaces, tighter spacing, 8px-radius cards, better mobile stacking, and readiness-level accent colors.
  - Improve table density, sticky section rhythm, and text wrapping for mixed English/Chinese activity names.
- Preserve existing CLI behavior:
  - `health dashboard` writes a self-contained HTML file.
  - `health dashboard --serve` keeps the refresh button and local refresh endpoint.

## Interface / Behavior Changes
- Public CLI stays the same.
- Generated dashboard will now contain inline JavaScript even for the file version, because Chart.js runs client-side.
- Replace the current test expectation “file dashboard has no `<script>`” with “file dashboard has no external script URLs and remains self-contained.”
- Keep the served refresh JavaScript separate from chart rendering logic so the file version cannot call `/api/refresh`.

## Test Plan
- Update and run `uv run pytest tests/test_dashboard.py`.
- Run `uv run pytest -q`.
- Run `uv run ruff check src tests`.
- Render the dashboard locally and inspect with the browser at desktop and mobile widths.
- Verify:
  - Above-the-fold cockpit is readable.
  - Charts render.
  - HTML has no external `src="https://..."` or CDN references.
  - `--serve` refresh button still works.
  - Empty-data states still render cleanly.

## Assumptions
- The chosen direction is `Daily cockpit`.
- The chart dependency should be local and vendored, not fetched at runtime.
- It is acceptable to relax the old “no JavaScript in dashboard.html” rule because the selected chart-library option requires client-side JavaScript.
- No new backend service, UI framework, Streamlit, React, npm build, or external network call will be added.
