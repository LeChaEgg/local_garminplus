"""Static HTML dashboard generator.

Produces a single self-contained `data/reports/dashboard.html` file from the
latest SQLite + reports/* state. No JavaScript, no external assets, no server
— just hand-written HTML/CSS, inline SVG, and Unicode glyphs. The `health
dashboard` CLI command calls `build_and_write_dashboard(...)` and then opens
the file in the default browser.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

import pandas as pd

from .config import Goals, Settings, load_goals
from .storage import (
    connect,
    read_activities,
    read_body_metrics,
    read_recommendations,
    read_wellness,
)
from .workout_library import Workout, load_workouts

# ---------- public entry point ----------


def build_dashboard_html(settings: Settings, *, with_refresh: bool = False) -> str:
    """Render the dashboard HTML as a string.

    `with_refresh=True` injects a small refresh button (top-right) whose JS
    POSTs to `/api/refresh` and reloads on success. Only meaningful when the
    page is served from the local HTTP server (`health dashboard --serve`);
    leave False when writing to disk for file:// opening.
    """
    goals: Goals | None = None
    if settings.goals_path.exists():
        goals = load_goals(settings.goals_path)

    wellness = pd.DataFrame()
    activities = pd.DataFrame()
    body = pd.DataFrame()
    recs = pd.DataFrame()
    if settings.sqlite_path.exists():
        with connect(settings.sqlite_path) as conn:
            wellness = read_wellness(conn)
            activities = read_activities(conn)
            body = read_body_metrics(conn)
            recs = read_recommendations(conn)

    workouts = (
        load_workouts(settings.workouts_dir) if settings.workouts_dir.exists() else []
    )

    return _build_html(
        settings, goals, wellness, activities, body, recs, workouts,
        with_refresh=with_refresh,
    )


def build_and_write_dashboard(settings: Settings) -> Path:
    """Render the dashboard HTML and write it to `data/reports/dashboard.html`."""
    html_doc = build_dashboard_html(settings, with_refresh=False)
    out = settings.reports_dir / "dashboard.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_doc, encoding="utf-8")
    return out


# ---------- CSS ----------

_CSS = """
:root {
  --bg: #f8fafc;
  --card: #ffffff;
  --card-2: #f1f5f9;
  --text: #0f172a;
  --muted: #64748b;
  --border: #e2e8f0;
  --accent: #2563eb;
  --accent-2: #1d4ed8;
  --green: #16a34a;
  --green-soft: rgba(22,163,74,.14);
  --green_yellow: #65a30d;
  --green_yellow-soft: rgba(101,163,13,.14);
  --yellow: #ca8a04;
  --yellow-soft: rgba(202,138,4,.16);
  --orange: #ea580c;
  --orange-soft: rgba(234,88,12,.16);
  --red: #dc2626;
  --red-soft: rgba(220,38,38,.16);
  --shadow: 0 1px 2px rgba(15,23,42,.04), 0 4px 12px rgba(15,23,42,.05);
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0b1220;
    --card: #131c2e;
    --card-2: #1b2742;
    --text: #f1f5f9;
    --muted: #94a3b8;
    --border: #243049;
    --accent: #60a5fa;
    --accent-2: #93c5fd;
    --green: #4ade80;
    --green-soft: rgba(74,222,128,.18);
    --green_yellow: #a3e635;
    --green_yellow-soft: rgba(163,230,53,.18);
    --yellow: #facc15;
    --yellow-soft: rgba(250,204,21,.18);
    --orange: #fb923c;
    --orange-soft: rgba(251,146,60,.20);
    --red: #f87171;
    --red-soft: rgba(248,113,113,.20);
    --shadow: 0 1px 2px rgba(0,0,0,.4), 0 6px 18px rgba(0,0,0,.35);
  }
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Inter", "SF Pro Text",
               "Helvetica Neue", Arial, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.5;
  font-size: 14px;
  -webkit-font-smoothing: antialiased;
}
header.app {
  background: linear-gradient(135deg, var(--accent) 0%, var(--accent-2) 100%);
  color: #ffffff;
  padding: 22px 28px;
}
header.app h1 { margin: 0; font-size: 22px; font-weight: 700; letter-spacing: -0.2px; }
header.app .meta { font-size: 12px; opacity: 0.92; margin-top: 4px; }
main {
  max-width: 1200px;
  margin: 0 auto;
  padding: 24px;
  display: grid;
  gap: 20px;
  grid-template-columns: 1fr;
}
@media (min-width: 960px) {
  main { grid-template-columns: minmax(0, 2fr) minmax(0, 1fr); }
  .span-2 { grid-column: 1 / -1; }
}
section {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 22px 24px;
  box-shadow: var(--shadow);
  min-width: 0;
}
section h2 {
  margin: 0 0 14px 0;
  font-size: 16px;
  font-weight: 700;
  letter-spacing: 0.2px;
  text-transform: uppercase;
  color: var(--muted);
  display: flex;
  align-items: center;
  gap: 8px;
}
section h2 .accent-dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--accent);
}
section h3 {
  font-size: 13px;
  font-weight: 600;
  color: var(--muted);
  margin: 18px 0 8px 0;
  text-transform: uppercase;
  letter-spacing: 0.4px;
}
.muted { color: var(--muted); }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; }

/* ---- Today section ---- */
.today-grid {
  display: grid;
  grid-template-columns: 220px 1fr;
  gap: 24px;
  align-items: center;
}
@media (max-width: 720px) { .today-grid { grid-template-columns: 1fr; justify-items: center; } }
.donut {
  --score: 0;
  --color: var(--green);
  position: relative;
  width: 200px; height: 200px;
  border-radius: 50%;
  background: conic-gradient(var(--color) calc(var(--score) * 1%), var(--border) 0);
  display: flex; align-items: center; justify-content: center;
}
.donut::after {
  content: "";
  position: absolute; inset: 14px;
  background: var(--card);
  border-radius: 50%;
}
.donut .inner {
  position: relative; z-index: 1;
  text-align: center;
}
.donut .score { font-size: 44px; font-weight: 700; line-height: 1; letter-spacing: -1px; }
.donut .label { font-size: 12px; text-transform: uppercase; letter-spacing: 0.6px;
                color: var(--color); font-weight: 700; margin-top: 4px; }
.hero-card {
  background: var(--card-2);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 18px 20px;
  display: flex; gap: 14px; align-items: center;
  min-width: 0;
}
.hero-card .glyph { font-size: 44px; line-height: 1; flex: 0 0 auto; }
.hero-card .body { min-width: 0; flex: 1; }
.hero-card .name { font-size: 18px; font-weight: 700; }
.hero-card .sub { color: var(--muted); font-size: 13px; margin-top: 2px; }
.hero-card .id { display: inline-block; background: var(--bg); border-radius: 6px;
                 padding: 2px 8px; font-size: 11px; margin-top: 6px;
                 font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
                 color: var(--muted); }
.no-rec { color: var(--muted); font-style: italic; }
.alt-row { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
.alt {
  display: inline-flex; align-items: center; gap: 6px;
  background: var(--bg); border: 1px solid var(--border);
  border-radius: 999px; padding: 4px 10px; font-size: 12px;
}
.alt .kind { color: var(--muted); text-transform: uppercase; letter-spacing: 0.4px;
             font-size: 10px; font-weight: 700; }
.subscores {
  display: flex; flex-wrap: wrap; gap: 10px; margin-top: 14px;
}
.subscore {
  background: var(--card-2); border: 1px solid var(--border);
  border-radius: 10px; padding: 8px 14px; min-width: 110px;
}
.subscore .label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px;
                   color: var(--muted); font-weight: 700; }
.subscore .value { font-size: 18px; font-weight: 700; margin-top: 2px;
                   font-variant-numeric: tabular-nums; }
.subscore .sub { font-size: 11px; color: var(--muted); margin-top: 0; }
.alerts { margin-top: 12px; }
.alert {
  display: flex; align-items: flex-start; gap: 8px;
  border-left: 3px solid var(--orange);
  background: var(--orange-soft);
  color: var(--text);
  padding: 8px 12px; border-radius: 6px;
  margin: 6px 0; font-size: 13px;
}
.alert .icon { font-size: 16px; line-height: 1.2; }
.alert.red { border-color: var(--red); background: var(--red-soft); }
.alert.yellow { border-color: var(--yellow); background: var(--yellow-soft); }
.reasons {
  margin-top: 8px;
  columns: 2;
  column-gap: 22px;
  font-size: 13px;
  list-style: none;
  padding: 0;
}
@media (max-width: 760px) { .reasons { columns: 1; } }
.reasons li { break-inside: avoid; margin: 3px 0; padding-left: 14px; position: relative; }
.reasons li::before {
  content: "›"; color: var(--muted); position: absolute; left: 0; font-weight: 700;
}
details.not-rec { margin-top: 12px; }
details.not-rec > summary {
  cursor: pointer; font-size: 13px; color: var(--muted);
  padding: 4px 0; list-style: none;
}
details.not-rec > summary::-webkit-details-marker { display: none; }
details.not-rec > summary::before {
  content: "▸ "; color: var(--muted);
}
details.not-rec[open] > summary::before { content: "▾ "; }
.not-rec table { margin-top: 6px; }

/* ---- Tables ---- */
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td {
  text-align: left; padding: 7px 10px;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
}
th { background: var(--card-2); color: var(--muted); font-size: 11px;
     text-transform: uppercase; letter-spacing: 0.4px; font-weight: 700; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }

/* ---- Pills & chips ---- */
.chip {
  display: inline-block; background: var(--card-2);
  color: var(--text); border: 1px solid var(--border);
  border-radius: 999px; padding: 2px 8px; font-size: 11px; margin: 1px 2px 1px 0;
}
.chip.accent { color: var(--accent); border-color: color-mix(in oklab, var(--accent) 40%, var(--border)); }

/* ---- Level helpers ---- */
.lvl-green { color: var(--green); }
.lvl-green_yellow { color: var(--green_yellow); }
.lvl-yellow { color: var(--yellow); }
.lvl-orange { color: var(--orange); }
.lvl-red { color: var(--red); }

/* ---- Trends ---- */
.trends-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 14px;
}
.trend {
  background: var(--card-2); border: 1px solid var(--border);
  border-radius: 10px; padding: 12px 14px;
}
.trend .label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;
                color: var(--muted); font-weight: 700; }
.trend .latest { font-size: 22px; font-weight: 700; margin-top: 2px;
                 font-variant-numeric: tabular-nums; }
.trend .delta { font-size: 12px; font-variant-numeric: tabular-nums; margin-top: 2px; }
.trend .range { font-size: 11px; color: var(--muted); margin-top: 6px;
                font-variant-numeric: tabular-nums; }
.spark { display: block; margin-top: 6px; width: 100%; height: auto; }

/* ---- Bars (weekly goals, sport minutes) ---- */
.bar-row {
  display: grid; grid-template-columns: 160px 1fr 90px;
  gap: 10px; align-items: center;
  margin: 5px 0; font-size: 13px;
}
.bar { background: var(--card-2); border-radius: 999px; height: 10px; overflow: hidden;
       border: 1px solid var(--border); position: relative; }
.bar-fill { height: 100%; border-radius: 999px;
            background: var(--accent); transition: width .2s ease; }
.bar-fill.green { background: var(--green); }
.bar-fill.green_yellow { background: var(--green_yellow); }
.bar-fill.yellow { background: var(--yellow); }
.bar-fill.red { background: var(--red); }
.bar-fill.blue { background: var(--accent); }
.bar-val { text-align: right; color: var(--muted); font-variant-numeric: tabular-nums; }

/* ---- Workout library cards ---- */
.wk-group { margin-top: 10px; }
.wk-group .gh {
  display: flex; align-items: center; gap: 10px;
  font-size: 14px; font-weight: 700; color: var(--text);
  margin: 14px 0 8px 0;
}
.wk-group .gh .count { color: var(--muted); font-size: 12px; font-weight: 500; }
.wk-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 12px;
}
.wk-card {
  background: var(--card-2); border: 1px solid var(--border);
  border-radius: 10px; padding: 12px 14px;
}
.wk-card .top { display: flex; gap: 10px; align-items: center; }
.wk-card .glyph { font-size: 24px; }
.wk-card .name { font-weight: 700; font-size: 14px; }
.wk-card .meta { color: var(--muted); font-size: 12px; margin-top: 4px; }
.wk-card .stats { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 8px;
                  font-size: 11px; color: var(--muted); }
.wk-card .stats .label { text-transform: uppercase; letter-spacing: 0.4px;
                         margin-right: 4px; }
.dots { font-family: ui-monospace, SFMono-Regular, monospace; letter-spacing: 1px; }
.dots .on { color: var(--accent); }
.dots .off { color: var(--border); }
.wk-card .tags { margin-top: 8px; }

/* ---- Data quality ---- */
.coverage-bar { background: var(--card-2); border-radius: 6px; height: 6px;
                 border: 1px solid var(--border); overflow: hidden;
                 width: 120px; display: inline-block; vertical-align: middle; }
.coverage-fill { height: 100%; background: var(--green); }
.coverage-fill.partial { background: var(--yellow); }
.coverage-fill.low { background: var(--red); }

/* ---- Refresh toolbar (only rendered when served via health dashboard --serve) ---- */
#refresh-toolbar {
  position: fixed; top: 14px; right: 18px; z-index: 100;
  display: flex; align-items: center; gap: 10px;
  font-family: inherit;
}
#refresh-btn {
  display: inline-flex; align-items: center; gap: 6px;
  border: 1px solid rgba(255,255,255,.4);
  background: rgba(255,255,255,.18);
  color: #ffffff;
  border-radius: 999px;
  padding: 6px 14px;
  font-size: 13px; font-weight: 600;
  cursor: pointer;
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  box-shadow: 0 1px 4px rgba(0,0,0,.15);
  transition: background-color .15s, transform .15s;
}
#refresh-btn:hover:not(:disabled) {
  background: rgba(255,255,255,.30);
  transform: translateY(-1px);
}
#refresh-btn:active:not(:disabled) { transform: translateY(0); }
#refresh-btn:disabled { opacity: 0.7; cursor: progress; }
#refresh-btn .rb-glyph { font-size: 15px; line-height: 1; }
#refresh-btn.spinning .rb-glyph { animation: rb-spin 0.9s linear infinite; }
@keyframes rb-spin { from { transform: rotate(0); } to { transform: rotate(360deg); } }
#refresh-status {
  font-size: 12px; color: #ffffff; opacity: 0.95;
  background: rgba(0,0,0,.32);
  border-radius: 999px; padding: 4px 10px;
  max-width: 360px; min-height: 22px;
}
#refresh-status:empty { display: none; }
#refresh-status.ok  { background: rgba(22,163,74,.55); }
#refresh-status.err { background: rgba(220,38,38,.55); }

/* ---- UI polish additions ---- */
.subscore .bar {
  margin-top: 6px; height: 6px; background: var(--bg);
}
.subscore .bar-fill { background: var(--accent); }
.trend .row { display: flex; align-items: baseline; gap: 8px; }
.trend .arrow {
  font-size: 16px; font-weight: 700; line-height: 1; font-variant-numeric: tabular-nums;
}
.trend .arrow.up   { color: var(--green); }
.trend .arrow.down { color: var(--red); }
.trend .arrow.flat { color: var(--muted); }
.wk-card.recommended {
  border-color: var(--green);
  box-shadow: 0 0 0 2px var(--green-soft);
  position: relative;
}
.wk-card.recommended::before {
  content: "Today's pick";
  position: absolute;
  top: -8px; right: 10px;
  background: var(--green); color: #fff;
  font-size: 10px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.5px;
  padding: 2px 8px; border-radius: 999px;
}
.bar-val.over { color: var(--orange); font-weight: 600; }

/* ---- Goals tables ---- */
.goals-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 14px;
}
.goal-card {
  background: var(--card-2); border: 1px solid var(--border);
  border-radius: 10px; padding: 12px 14px;
}
.goal-card h4 {
  margin: 0 0 8px 0; font-size: 12px; text-transform: uppercase;
  letter-spacing: 0.5px; color: var(--muted); font-weight: 700;
}
.goal-card .kv {
  display: grid; grid-template-columns: 1fr auto; gap: 4px 12px;
  font-size: 13px;
}
.goal-card .kv .k { color: var(--muted); }
.goal-card .kv .v { text-align: right; font-variant-numeric: tabular-nums; }
.goal-card .narrative {
  margin-top: 10px; padding-top: 10px; border-top: 1px dashed var(--border);
  font-size: 13px; color: var(--text);
}
.goal-card .narrative p { margin: 4px 0; }

footer.app {
  max-width: 1200px; margin: 0 auto; padding: 18px 28px 32px;
  color: var(--muted); font-size: 11px; text-align: center;
}
"""


# ---------- glyphs & visual helpers ----------


_SPORT_GLYPHS = {
    "run": "🏃",
    "bike": "🚴",
    "ride": "🚴",
    "strength": "🏋️",
    "weighttraining": "🏋️",
    "recovery": "🧘",
    "walk": "🚶",
    "swim": "🏊",
    "hike": "🥾",
    "ebikeride": "🚴",
    "virtualride": "🚴",
    "virtualrun": "🏃",
    "rockclimbing": "🧗",
}


def _sport_glyph(sport: str | None) -> str:
    if not sport:
        return "●"
    return _SPORT_GLYPHS.get(sport.strip().lower(), "●")


def _dots(filled: int, total: int = 5) -> str:
    filled = max(0, min(total, int(filled)))
    on = "●" * filled
    off = "○" * (total - filled)
    return f'<span class="dots"><span class="on">{on}</span><span class="off">{off}</span></span>'


def _bar(pct: float, color_class: str = "blue") -> str:
    pct = max(0.0, min(100.0, pct))
    return (
        f'<div class="bar"><div class="bar-fill {color_class}" '
        f'style="width:{pct:.0f}%"></div></div>'
    )


def _bar_color_for_min(actual: float, target: float) -> str:
    """For 'meet at least' goals."""
    if target <= 0:
        return "blue"
    pct = (actual / target) * 100
    if pct >= 85:
        return "green"
    if pct >= 50:
        return "yellow"
    return "red"


def _bar_color_for_cap(actual: float, cap: float) -> str:
    """For 'stay below cap' goals (e.g. hard sessions)."""
    if cap <= 0:
        return "blue"
    pct = (actual / cap) * 100
    if pct <= 100:
        return "green"
    return "red"


# ---------- top-level HTML ----------


def _build_html(
    settings: Settings,
    goals: Goals | None,
    wellness: pd.DataFrame,
    activities: pd.DataFrame,
    body: pd.DataFrame,
    recs: pd.DataFrame,
    workouts: list[Workout],
    with_refresh: bool = False,
) -> str:
    daily_payload = _latest_json(settings.reports_dir / "daily")
    weekly_payload = _latest_json(settings.reports_dir / "weekly")
    monthly_payload = _latest_json(settings.reports_dir / "monthly")

    generated_at = (
        pd.Timestamp.now(tz=settings.local_timezone or "UTC")
        .strftime("%Y-%m-%d %H:%M %Z")
    )

    recommended_id: str | None = None
    if daily_payload:
        main = (daily_payload.get("recommendation") or {}).get("main") or {}
        recommended_id = main.get("workout_id")

    body_html = "\n".join(
        [
            _section_today(daily_payload),
            _section_trends(wellness, activities),
            _section_weekly(weekly_payload),
            _section_monthly(monthly_payload),
            _section_goals(goals),
            _section_workouts(workouts, recommended_id=recommended_id),
            _section_data_quality(wellness, activities, body, recs),
        ]
    )

    refresh_html = _REFRESH_BUTTON_HTML if with_refresh else ""
    refresh_script = _REFRESH_SCRIPT if with_refresh else ""
    footer_text = (
        "health-manager · click ↻ to re-sync and rebuild"
        if with_refresh
        else "health-manager · open this file directly in your browser at any time"
    )

    return (
        "<!doctype html>\n"
        '<html lang="en"><head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>Health Manager — {html.escape(generated_at)}</title>\n"
        f"<style>{_CSS}</style>\n"
        "</head><body>\n"
        f"{refresh_html}"
        '<header class="app">\n'
        "  <h1>Health Manager</h1>\n"
        f'  <div class="meta">Local dashboard · generated {html.escape(generated_at)} · '
        f"data stays on this machine</div>\n"
        "</header>\n"
        "<main>\n"
        f"{body_html}\n"
        "</main>\n"
        f'<footer class="app">{footer_text}</footer>\n'
        f"{refresh_script}"
        "</body></html>\n"
    )


# Inline refresh button + JS. Only rendered when the page is served via the
# local HTTP server (i.e. when fetch('/api/refresh') has a peer to talk to).
_REFRESH_BUTTON_HTML = """
<div id="refresh-toolbar" aria-live="polite">
  <button id="refresh-btn" type="button" title="Re-sync and rebuild this page">
    <span class="rb-glyph">↻</span><span class="rb-label">Refresh</span>
  </button>
  <span id="refresh-status"></span>
</div>
"""

_REFRESH_SCRIPT = """
<script>
(function () {
  const btn = document.getElementById('refresh-btn');
  const status = document.getElementById('refresh-status');
  if (!btn) return;
  btn.addEventListener('click', async function () {
    btn.disabled = true;
    btn.classList.add('spinning');
    status.textContent = 'syncing… (≤ 30s on warm cache)';
    status.className = '';
    const t0 = performance.now();
    try {
      const resp = await fetch('/api/refresh', { method: 'POST' });
      const data = await resp.json();
      if (!resp.ok || !data.ok) throw new Error(data.error || ('HTTP ' + resp.status));
      const dt = ((performance.now() - t0) / 1000).toFixed(1);
      status.textContent = 'done in ' + dt + 's — reloading';
      status.className = 'ok';
      setTimeout(function () { location.reload(); }, 250);
    } catch (e) {
      status.textContent = 'error: ' + e.message;
      status.className = 'err';
      btn.disabled = false;
      btn.classList.remove('spinning');
    }
  });
})();
</script>
"""


# ---------- section: today ----------


def _section_today(p: dict[str, Any] | None) -> str:
    if not p:
        return _wrap_section(
            "Today",
            '<p class="muted">No daily report yet. Run <code>health today</code>.</p>',
            classes=["span-2"],
        )
    r = p["readiness"]
    rec = p["recommendation"]
    conf = p["confidence"]
    level = r["level"]
    score = float(r["score"])

    main = rec.get("main")
    if main:
        glyph = _sport_glyph(main.get("sport"))
        rec_html = (
            '<div class="hero-card">'
            f'<div class="glyph">{glyph}</div>'
            f'<div class="body">'
            f'<div class="name">{html.escape(main["name"])}</div>'
            f'<div class="sub">{html.escape(main["sport"])} · '
            f'{html.escape(main["category"])} · {main["duration_min"]} min</div>'
            f'<div class="id">{html.escape(main["workout_id"])}</div>'
            f"</div></div>"
        )
    else:
        rec_html = (
            '<div class="hero-card"><div class="glyph">●</div>'
            '<div class="body"><div class="name">No workout passed all filters</div>'
            '<div class="sub">Loosen guardrails or add lower-intensity options to your library.</div>'
            "</div></div>"
        )

    cons = rec.get("conservative")
    prog = rec.get("progressive")
    alt_parts: list[str] = []
    if cons:
        alt_parts.append(
            f'<span class="alt"><span class="kind">Conservative</span>'
            f'{_sport_glyph(cons.get("sport"))} {html.escape(cons["name"])} · '
            f'{cons["duration_min"]} min</span>'
        )
    if prog:
        alt_parts.append(
            f'<span class="alt"><span class="kind">Progressive</span>'
            f'{_sport_glyph(prog.get("sport"))} {html.escape(prog["name"])} · '
            f'{prog["duration_min"]} min</span>'
        )
    alt_html = f'<div class="alt-row">{"".join(alt_parts)}</div>' if alt_parts else ""

    conf_pct = min(100.0, max(0.0, float(conf["score"]) * 100))
    subscores_html = (
        '<div class="subscores">'
        f'<div class="subscore"><div class="label">Sleep &amp; Recovery</div>'
        f'<div class="value">{r["sleep_recovery_score"]:.0f}</div></div>'
        f'<div class="subscore"><div class="label">Load Balance</div>'
        f'<div class="value">{r["load_balance_score"]:.0f}</div></div>'
        f'<div class="subscore"><div class="label">Confidence</div>'
        f'<div class="value">{html.escape(conf["level"])}</div>'
        f'<div class="bar" style="margin-top:6px;">'
        f'<div class="bar-fill" style="width:{conf_pct:.0f}%"></div></div></div>'
        + (
            f'<div class="subscore"><div class="label">Risk penalty</div>'
            f'<div class="value">-{r["risk_penalty"]:.0f}</div></div>'
            if r.get("risk_penalty")
            else ""
        )
        + "</div>"
    )

    risk_html = ""
    if r.get("risk_flags"):
        risk_html = '<div class="alerts">' + "".join(
            f'<div class="alert"><span class="icon">⚠</span>'
            f"<span>{html.escape(f)}</span></div>"
            for f in r["risk_flags"]
        ) + "</div>"

    reasons_html = ""
    if r.get("reasons"):
        items = "".join(
            f"<li>{html.escape(reason)}</li>" for reason in r["reasons"]
        )
        reasons_html = f'<h3>Why</h3><ul class="reasons">{items}</ul>'

    nr = rec.get("not_recommended", []) or []
    not_rec_html = ""
    if nr:
        rows = "".join(
            f'<tr><td>{_sport_glyph(item["sport"])} {html.escape(item["name"])}</td>'
            f"<td>{html.escape('; '.join(item['reasons']))}</td></tr>"
            for item in nr
        )
        not_rec_html = (
            f'<details class="not-rec"><summary>{len(nr)} workout(s) filtered out</summary>'
            '<table><thead><tr><th>Workout</th><th>Reasons</th></tr></thead>'
            f"<tbody>{rows}</tbody></table>"
            "</details>"
        )

    body_html = (
        '<div class="today-grid">'
        f'<div class="donut" style="--score:{score:.0f}; --color: var(--{level});">'
        f'<div class="inner"><div class="score">{score:.0f}</div>'
        f'<div class="label" style="color: var(--{level});">'
        f"{html.escape(level.replace('_', '/'))}</div></div></div>"
        f"<div>{rec_html}{alt_html}</div>"
        "</div>"
        f"{subscores_html}{risk_html}{reasons_html}{not_rec_html}"
    )
    return _wrap_section(
        f'Today · <span class="muted" style="font-weight:500;text-transform:none;letter-spacing:0;">{html.escape(p["date"])}</span>',
        body_html,
        classes=["span-2"],
        accent_var=level,
    )


# ---------- section: trends ----------


def _section_trends(wellness: pd.DataFrame, activities: pd.DataFrame) -> str:
    if wellness.empty and activities.empty:
        return _wrap_section(
            "Trends",
            '<p class="muted">No data yet. Run <code>health sync</code>.</p>',
            classes=["span-2"],
        )

    cards: list[str] = []
    if not wellness.empty:
        w = wellness.copy()
        w["date"] = pd.to_datetime(w["date"])
        w = w.set_index("date").sort_index().tail(60)
        for col, label, lower_is_better in (
            ("hrv", "HRV", False),
            ("rhr", "Resting HR", True),
            ("sleep_duration_min", "Sleep (min)", False),
            ("garmin_sleep_score", "Sleep score", False),
            ("ctl", "CTL (fitness)", False),
            ("atl", "ATL (fatigue)", True),
        ):
            if col in w.columns:
                series = pd.to_numeric(w[col], errors="coerce").dropna()
                if len(series) >= 3:
                    cards.append(
                        _trend_card(label, [float(v) for v in series.values], lower_is_better)
                    )

    if not activities.empty:
        a = activities.copy()
        a["date"] = pd.to_datetime(a["date"])
        a["load"] = pd.to_numeric(a["load"], errors="coerce").fillna(0)
        daily_load = a.groupby(a["date"].dt.date)["load"].sum()
        if not daily_load.empty:
            idx = pd.date_range(end=daily_load.index.max(), periods=60).date
            daily_load = daily_load.reindex(idx, fill_value=0)
            cards.append(
                _trend_card("Daily load", [float(v) for v in daily_load.values], False)
            )

    body_html = (
        '<p class="muted" style="margin-top:0;">Last 60 days. Right-most point is most recent.</p>'
        f'<div class="trends-grid">{"".join(cards) if cards else ""}</div>'
    )
    return _wrap_section("Trends", body_html, classes=["span-2"])


def _trend_card(label: str, values: list[float], lower_is_better: bool) -> str:
    latest = values[-1]
    spread = (max(values) - min(values)) or 1.0

    delta_html = ""
    arrow_html = ""
    metric_direction: str = "neutral"   # 'good' | 'bad' | 'neutral'

    if len(values) >= 14:
        baseline_window = values[-29:-1] if len(values) > 28 else values[:-1]
        if baseline_window:
            mean = sum(baseline_window) / len(baseline_window)
            delta = latest - mean
            sign = "+" if delta >= 0 else ""
            good = (delta < 0) if lower_is_better else (delta > 0)
            color = "var(--green)" if good else "var(--red)"
            if abs(delta) < 0.2 * spread:
                color = "var(--muted)"
                metric_direction = "neutral"
            else:
                metric_direction = "good" if good else "bad"
            delta_html = (
                f'<div class="delta" style="color:{color}">'
                f"{sign}{delta:.1f} vs 28d mean {mean:.1f}</div>"
            )

    # 7-day vs 28-day arrow.
    if len(values) >= 14:
        recent_n = min(7, len(values))
        baseline_n = min(28, max(7, len(values) - recent_n))
        recent = values[-recent_n:]
        prior = values[-(recent_n + baseline_n) : -recent_n] if len(values) > recent_n else []
        if prior:
            r_mean = sum(recent) / len(recent)
            p_mean = sum(prior) / len(prior)
            shift = r_mean - p_mean
            if abs(shift) < 0.1 * spread:
                arrow_html = '<span class="arrow flat" title="flat vs 28d">→</span>'
            else:
                going_good = (shift < 0) if lower_is_better else (shift > 0)
                cls = "up" if going_good else "down"
                glyph = "↗" if shift > 0 else "↘"
                arrow_html = (
                    f'<span class="arrow {cls}" '
                    f'title="7d mean {r_mean:.1f} vs 28d mean {p_mean:.1f}">{glyph}</span>'
                )

    spark = _sparkline_svg(values, direction=metric_direction)
    lo, hi = min(values), max(values)
    return (
        '<div class="trend">'
        f'<div class="label">{html.escape(label)}</div>'
        f'<div class="row"><div class="latest">{latest:.1f}</div>{arrow_html}</div>'
        f"{delta_html}"
        f"{spark}"
        f'<div class="range">range {lo:.1f} – {hi:.1f}</div>'
        "</div>"
    )


def _sparkline_svg(
    values: list[float],
    width: int = 320,
    height: int = 60,
    direction: str = "neutral",
) -> str:
    """Render a sparkline as inline SVG.

    `direction` tints the line and the area fill:
      - "good"    → green (the metric is trending the right way)
      - "bad"     → red   (trending the wrong way)
      - "neutral" → accent blue (default)
    """
    if len(values) < 2:
        return ""
    color = {
        "good": "var(--green)",
        "bad": "var(--red)",
    }.get(direction, "var(--accent)")
    lo, hi = min(values), max(values)
    span = hi - lo if hi > lo else 1.0
    n = len(values)
    pts: list[tuple[float, float]] = []
    for i, v in enumerate(values):
        x = (i / (n - 1)) * (width - 4) + 2
        y = height - 4 - ((v - lo) / span) * (height - 10)
        pts.append((x, y))
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    first_x, _ = pts[0]
    last_x, _ = pts[-1]
    area_path = (
        f"M {first_x:.1f} {height - 1} "
        + " ".join(f"L {x:.1f} {y:.1f}" for x, y in pts)
        + f" L {last_x:.1f} {height - 1} Z"
    )
    last_x_s, last_y_s = f"{pts[-1][0]:.1f}", f"{pts[-1][1]:.1f}"
    return (
        f'<svg class="spark" viewBox="0 0 {width} {height}" preserveAspectRatio="none" '
        f'xmlns="http://www.w3.org/2000/svg" aria-hidden="true">'
        f'<path d="{area_path}" fill="{color}" opacity="0.14"/>'
        f'<polyline points="{poly}" fill="none" stroke="{color}" '
        f'stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{last_x_s}" cy="{last_y_s}" r="2.6" fill="{color}"/>'
        "</svg>"
    )


# ---------- section: weekly / monthly ----------


def _section_weekly(p: dict[str, Any] | None) -> str:
    if not p:
        return _wrap_section(
            "Weekly (rolling 7 d)",
            '<p class="muted">No weekly report yet. Run <code>health report weekly</code>.</p>',
            classes=["span-2"],
        )
    g = p.get("goal_progress", {}) or {}
    t = p.get("training", {}) or {}

    header_bits = [
        f'<span class="chip">{html.escape(p["week_start"])} → {html.escape(p["week_end"])}</span>',
        f'<span class="chip">{p["sleep"]["avg_hours"]:.2f} h avg sleep</span>',
    ]
    if p.get("hrv") and p["hrv"].get("latest") is not None:
        h = p["hrv"]
        header_bits.append(
            f'<span class="chip">HRV {h["latest"]:.1f} '
            f'(mean {_fmt(h.get("mean"))}, n={h.get("n", 0)})</span>'
        )
    if p.get("rhr") and p["rhr"].get("latest") is not None:
        h = p["rhr"]
        header_bits.append(
            f'<span class="chip">RHR {h["latest"]:.1f} '
            f'(mean {_fmt(h.get("mean"))}, n={h.get("n", 0)})</span>'
        )
    header_html = '<div style="margin-bottom:14px;">' + " ".join(header_bits) + "</div>"

    # Goal progress bars
    goal_rows: list[tuple[str, float, float, str, str]] = [
        (
            "Aerobic minutes",
            float(g.get("weekly_minutes_actual", 0)),
            float(g.get("weekly_minutes_min", 0) or 1),
            f"{float(g.get('weekly_minutes_actual', 0)):.0f} / {g.get('weekly_minutes_min', 0):.0f}",
            "min",
        ),
        (
            "Z2 minutes",
            float(g.get("weekly_z2_minutes_actual", 0)),
            float(g.get("weekly_z2_minutes_min", 0) or 1),
            f"{float(g.get('weekly_z2_minutes_actual', 0)):.0f} / {g.get('weekly_z2_minutes_min', 0):.0f}",
            "min",
        ),
        (
            "Strength sessions",
            float(g.get("weekly_strength_sessions_actual", 0)),
            float(g.get("weekly_strength_sessions_min", 0) or 1),
            f"{int(g.get('weekly_strength_sessions_actual', 0))} / {int(g.get('weekly_strength_sessions_min', 0))}",
            "cap",
        ),
        (
            "Hard sessions",
            float(g.get("weekly_hard_sessions_actual", 0)),
            float(g.get("weekly_hard_sessions_max", 0) or 1),
            f"{int(g.get('weekly_hard_sessions_actual', 0))} / ≤ {int(g.get('weekly_hard_sessions_max', 0))}",
            "cap",
        ),
    ]
    bar_html_parts: list[str] = []
    for label, actual, target, val_text, kind in goal_rows:
        pct = (actual / target) * 100 if target > 0 else 0.0
        if label == "Hard sessions":
            color = _bar_color_for_cap(actual, target)
        elif kind == "cap":
            color = _bar_color_for_min(actual, target)
        else:
            color = _bar_color_for_min(actual, target)
        over_cls = " over" if pct > 100 else ""
        suffix = f" ({pct:.0f}%)" if pct > 100 else ""
        bar_html_parts.append(
            '<div class="bar-row">'
            f"<span>{html.escape(label)}</span>"
            f"{_bar(min(pct, 100), color)}"
            f'<span class="bar-val{over_cls}">{html.escape(val_text)}{suffix}</span>'
            "</div>"
        )
    goals_html = (
        "<h3>Goal progress</h3>" + "".join(bar_html_parts) if bar_html_parts else ""
    )

    # Minutes by sport — normalized to max
    minutes = t.get("minutes_by_sport", {}) or {}
    sport_html = ""
    if minutes:
        max_v = max(minutes.values())
        rows = []
        for sport, mins in sorted(minutes.items(), key=lambda kv: -kv[1]):
            pct = (mins / max_v) * 100 if max_v > 0 else 0.0
            rows.append(
                '<div class="bar-row">'
                f"<span>{_sport_glyph(sport)} {html.escape(str(sport))}</span>"
                f"{_bar(pct, 'blue')}"
                f'<span class="bar-val">{mins:.0f} min</span>'
                "</div>"
            )
        sport_html = "<h3>Minutes by sport</h3>" + "".join(rows)

    return _wrap_section(
        "Weekly (rolling 7 d)",
        f"{header_html}{goals_html}{sport_html}",
        classes=["span-2"],
    )


def _section_monthly(p: dict[str, Any] | None) -> str:
    if not p:
        return _wrap_section(
            "Monthly",
            '<p class="muted">No monthly report yet. Run <code>health report monthly</code>.</p>',
            classes=["span-2"],
        )

    chips = [
        f'<span class="chip">{html.escape(p.get("month", ""))}</span>',
        f'<span class="chip">{p.get("sleep_avg_hours", 0):.2f} h avg sleep</span>',
        f'<span class="chip">{p.get("total_minutes", 0):.0f} total min</span>',
    ]
    if p.get("body", {}).get("weight_kg_delta") is not None:
        delta = p["body"]["weight_kg_delta"]
        chips.append(f'<span class="chip">Weight Δ {delta:+.2f} kg</span>')
    header_html = '<div style="margin-bottom:14px;">' + " ".join(chips) + "</div>"

    minutes = p.get("minutes_by_sport", {}) or {}
    sport_html = ""
    if minutes:
        max_v = max(minutes.values())
        rows = []
        for sport, mins in sorted(minutes.items(), key=lambda kv: -kv[1]):
            pct = (mins / max_v) * 100 if max_v > 0 else 0.0
            rows.append(
                '<div class="bar-row">'
                f"<span>{_sport_glyph(sport)} {html.escape(str(sport))}</span>"
                f"{_bar(pct, 'blue')}"
                f'<span class="bar-val">{mins:.0f} min</span>'
                "</div>"
            )
        sport_html = "<h3>Minutes by sport</h3>" + "".join(rows)

    return _wrap_section("Monthly", f"{header_html}{sport_html}", classes=["span-2"])


# ---------- section: goals ----------


def _section_goals(goals: Goals | None) -> str:
    if not goals:
        return _wrap_section(
            "Goals",
            '<p class="muted">No goals.md yet. Run <code>health init</code>.</p>',
        )

    p = goals.profile
    sr = goals.sleep_recovery
    ae = goals.aerobic
    st = goals.strength
    bs = goals.body_shape
    gd = goals.guardrails
    gw = goals.goal_weights

    def card(title: str, rows: list[tuple[str, Any]]) -> str:
        kv = "".join(
            f'<div class="k">{html.escape(k)}</div>'
            f'<div class="v">{_fmt_v(v)}</div>'
            for k, v in rows
        )
        return f'<div class="goal-card"><h4>{html.escape(title)}</h4><div class="kv">{kv}</div></div>'

    cards: list[str] = [
        card(
            "Profile",
            [
                ("Name", p.name),
                ("Timezone", p.timezone),
                ("Sex", p.sex or "—"),
                ("Height (cm)", p.height_cm or "—"),
                ("Birth year", p.birth_year or "—"),
            ],
        ),
        card(
            "Goal weights",
            [
                ("Sleep & recovery", gw.sleep_recovery),
                ("Aerobic", gw.aerobic),
                ("Strength", gw.strength),
                ("Body shape", gw.body_shape),
            ],
        ),
        card(
            "Sleep & recovery",
            [
                (
                    "Sleep duration target (h)",
                    f"{sr.sleep_duration_range_h[0]:.1f}–{sr.sleep_duration_range_h[1]:.1f}"
                    if len(sr.sleep_duration_range_h) >= 2
                    else "—",
                ),
                ("Garmin sleep score min", sr.garmin_sleep_score_min),
                ("HRV baseline window (d)", sr.hrv_baseline_window_days),
                ("RHR baseline window (d)", sr.rhr_baseline_window_days),
                ("Sleep latency target (min)", sr.sleep_latency_target_min),
            ],
        ),
        card(
            "Aerobic",
            [
                ("Weekly minutes min", ae.weekly_minutes_min),
                ("Weekly Z2 minutes min", ae.weekly_z2_minutes_min),
                ("Weekly hard sessions max", ae.weekly_hard_sessions_max),
                ("Long run every (d)", ae.long_run_every_n_days),
                ("Include cycling", ae.include_cycling),
            ],
        ),
        card(
            "Strength",
            [
                ("Weekly sessions min", st.weekly_sessions_min),
                ("Patterns", st.movement_patterns),
                ("Progressive overload", st.progressive_overload),
            ],
        ),
        card(
            "Body shape",
            [
                ("Weight trend", bs.weight_trend),
                ("Waist cm target", bs.waist_cm_target or "—"),
                ("Monthly photo check", bs.monthly_photo_check),
                ("Subjective score min", bs.subjective_score_min),
            ],
        ),
        card(
            "Guardrails",
            [
                ("No hard if readiness <", gd.never_hard_training_if_readiness_below),
                ("Avoid hard after bad sleep", gd.avoid_hard_training_after_bad_sleep),
                ("Max consecutive training days", gd.max_consecutive_training_days),
            ],
        ),
    ]

    narrative_html = ""
    if goals.narrative_notes.strip():
        paragraphs = [
            f"<p>{html.escape(p.strip())}</p>"
            for p in goals.narrative_notes.strip().split("\n\n")
            if p.strip()
        ]
        narrative_html = (
            '<div class="goal-card" style="grid-column: 1 / -1;">'
            "<h4>Narrative</h4>"
            f'<div class="narrative">{"".join(paragraphs)}</div></div>'
        )

    return _wrap_section(
        "Goals",
        f'<div class="goals-grid">{"".join(cards)}{narrative_html}</div>',
        classes=["span-2"],
    )


def _fmt_v(v: Any) -> str:
    if isinstance(v, bool):
        return "✓" if v else "–"
    if isinstance(v, list):
        return "".join(
            f'<span class="chip">{html.escape(str(item))}</span>' for item in v
        )
    if isinstance(v, float):
        if v.is_integer():
            return str(int(v))
        return f"{v:.2f}"
    return html.escape(str(v))


# ---------- section: workouts ----------


def _section_workouts(
    workouts: list[Workout], recommended_id: str | None = None
) -> str:
    if not workouts:
        return _wrap_section(
            "Workout library",
            '<p class="muted">No workouts loaded.</p>',
            classes=["span-2"],
        )
    # Group by sport.
    by_sport: dict[str, list[Workout]] = {}
    for w in workouts:
        by_sport.setdefault(w.sport, []).append(w)

    group_order = ["run", "bike", "strength", "recovery", "walk", "swim", "hike"]
    keys = [s for s in group_order if s in by_sport] + [
        s for s in by_sport if s not in group_order
    ]

    groups_html: list[str] = []
    for sport in keys:
        items = by_sport[sport]
        cards = []
        for w in items:
            tags_html = "".join(
                f'<span class="chip">{html.escape(t)}</span>' for t in w.meta.tags
            )
            extra_cls = " recommended" if w.id == recommended_id else ""
            cards.append(
                f'<div class="wk-card{extra_cls}">'
                '<div class="top">'
                f'<div class="glyph">{_sport_glyph(sport)}</div>'
                f'<div><div class="name">{html.escape(w.meta.name)}</div>'
                f'<div class="meta">{html.escape(w.meta.category)} · {w.meta.duration_min} min</div>'
                "</div></div>"
                '<div class="stats">'
                f'<span><span class="label">Intensity</span>{_dots(w.meta.intensity)}</span>'
                f'<span><span class="label">Recovery</span>{_dots(w.meta.recovery_cost)}</span>'
                f'<span><span class="label">Min readiness</span> {w.meta.min_readiness}</span>'
                "</div>"
                f'<div class="tags">{tags_html}</div>'
                "</div>"
            )
        groups_html.append(
            '<div class="wk-group">'
            f'<div class="gh">{_sport_glyph(sport)} {html.escape(sport.capitalize())}'
            f' <span class="count">({len(items)})</span></div>'
            f'<div class="wk-grid">{"".join(cards)}</div>'
            "</div>"
        )

    return _wrap_section(
        "Workout library",
        "".join(groups_html),
        classes=["span-2"],
    )


# ---------- section: data quality ----------


def _section_data_quality(
    wellness: pd.DataFrame,
    activities: pd.DataFrame,
    body: pd.DataFrame,
    recs: pd.DataFrame,
) -> str:
    counts_rows = "".join(
        f"<tr><td>{label}</td><td class='num'>{n}</td></tr>"
        for label, n in (
            ("Wellness rows", len(wellness)),
            ("Activities", len(activities)),
            ("Manual body metrics", len(body)),
            ("Recommendations recorded", len(recs)),
        )
    )
    counts_html = (
        '<table><thead><tr><th>Table</th><th class="num">Rows</th></tr></thead>'
        f"<tbody>{counts_rows}</tbody></table>"
    )

    coverage_html = ""
    if not wellness.empty:
        w = wellness.copy()
        w["date"] = pd.to_datetime(w["date"])
        cutoff = w["date"].max() - pd.Timedelta(days=29)
        recent = w[w["date"] >= cutoff]
        fields = [
            ("hrv", "HRV"),
            ("rhr", "RHR"),
            ("garmin_sleep_score", "Sleep score"),
            ("sleep_duration_min", "Sleep duration"),
            ("vo2max", "VO2max"),
            ("steps", "Steps"),
            ("respiration", "Respiration"),
            ("spo2", "SpO₂"),
        ]
        n_days = max(1, len(recent))
        rows = []
        for col, label in fields:
            if col in recent.columns:
                filled = int(pd.to_numeric(recent[col], errors="coerce").notna().sum())
                pct = filled / n_days * 100
                cls = "low" if pct < 40 else ("partial" if pct < 80 else "")
                rows.append(
                    f"<tr><td>{html.escape(label)}</td>"
                    f"<td class='num'>{filled} / {n_days}</td>"
                    f"<td>"
                    f'<span class="coverage-bar"><span class="coverage-fill {cls}" '
                    f'style="width:{pct:.0f}%"></span></span> '
                    f'<span class="muted">{pct:.0f}%</span></td></tr>'
                )
        if rows:
            coverage_html = (
                "<h3>Wellness coverage (last 30 days)</h3>"
                '<table><thead><tr><th>Field</th><th class="num">Days filled</th><th>Coverage</th></tr></thead>'
                f"<tbody>{''.join(rows)}</tbody></table>"
            )

    return _wrap_section("Data quality", f"{counts_html}{coverage_html}")


# ---------- helpers ----------


def _wrap_section(
    title: str,
    body_html: str,
    classes: list[str] | None = None,
    accent_var: str | None = None,
) -> str:
    extra = f" {' '.join(classes)}" if classes else ""
    style = ""
    accent_dot = ""
    if accent_var:
        style = f' style="border-top: 3px solid var(--{accent_var});"'
        accent_dot = f'<span class="accent-dot" style="background: var(--{accent_var});"></span>'
    else:
        accent_dot = '<span class="accent-dot"></span>'
    return (
        f'<section class="card{extra}"{style}>'
        f"<h2>{accent_dot}{title}</h2>\n"
        f"{body_html}\n"
        "</section>"
    )


def _latest_json(dir_path: Path) -> dict[str, Any] | None:
    if not dir_path.exists():
        return None
    files = sorted(dir_path.glob("*.json"))
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.1f}"
    return str(v)


# ---------- local refresh server ----------


def serve_dashboard(
    settings: Settings,
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    """Run a tiny local HTTP server that serves the dashboard with a refresh button.

    Bound to 127.0.0.1 only — never exposed beyond the local machine. The
    server is single-threaded; a single user clicking refresh ~once per
    minute is fine. Blocks until Ctrl-C.

    Routes:
      GET  /                  -> generated HTML (fresh on every request)
      GET  /healthz           -> 200 "ok"
      POST /api/refresh       -> runs sync(7) + today + weekly + monthly;
                                 returns {"ok": true, "elapsed_s": float}
                                 or    {"ok": false, "error": str}
    """
    import contextlib
    import http.server
    import json as _json
    import logging
    import socketserver
    import time
    import webbrowser

    log = logging.getLogger(__name__)

    class Handler(http.server.BaseHTTPRequestHandler):
        # Quiet the access log — surface only warnings.
        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: N802
            log.debug("http: " + fmt, *args)

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path in ("/", "/index.html", "/dashboard.html"):
                try:
                    html_doc = build_dashboard_html(settings, with_refresh=True)
                except Exception as e:
                    log.exception("dashboard render failed")
                    self._send(
                        500,
                        f"render error: {e}".encode(),
                        "text/plain; charset=utf-8",
                    )
                    return
                self._send(200, html_doc.encode("utf-8"), "text/html; charset=utf-8")
                return
            if self.path == "/healthz":
                self._send(200, b"ok", "text/plain; charset=utf-8")
                return
            if self.path == "/favicon.ico":
                self._send(204, b"", "image/x-icon")
                return
            self._send(404, b"not found", "text/plain; charset=utf-8")

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/api/refresh":
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            t0 = time.time()
            try:
                _run_refresh_pipeline(settings)
                payload = _json.dumps(
                    {"ok": True, "elapsed_s": time.time() - t0}
                ).encode("utf-8")
                self._send(200, payload, "application/json; charset=utf-8")
            except Exception as e:
                log.exception("refresh failed")
                payload = _json.dumps(
                    {"ok": False, "error": str(e), "elapsed_s": time.time() - t0}
                ).encode("utf-8")
                self._send(500, payload, "application/json; charset=utf-8")

    addr = ("127.0.0.1", port)
    url = f"http://127.0.0.1:{port}/"
    with socketserver.TCPServer(addr, Handler) as httpd:
        httpd.allow_reuse_address = True
        print(f"Serving on {url}  (Ctrl-C to stop)")
        if open_browser:
            with contextlib.suppress(Exception):
                webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopping.")


def _run_refresh_pipeline(settings: Settings) -> None:
    """Execute the full daily pipeline (sync -> readiness -> reports -> dashboard file).

    Used by the /api/refresh endpoint. Imports are kept local so the dashboard
    module doesn't pull these into the static-HTML build path.
    """
    from datetime import date as _date

    from .config import (
        load_goals,
        load_metric_mapping,
        load_scoring,
    )
    from .ingest import sync as _sync
    from .recommender import recommend
    from .reports import write_daily, write_monthly, write_weekly
    from .scoring import compute_confidence, compute_readiness
    from .storage import (
        connect,
        init_db,
        read_activities,
        read_checkins,
        read_wellness,
        upsert_recommendation,
    )
    from .workout_library import load_workouts

    today = _date.today()

    if settings.intervals_api_key:
        mapping = load_metric_mapping(settings.metric_mapping_path)
        _sync(settings, mapping, days=7)

    goals = load_goals(settings.goals_path)
    scoring = load_scoring(settings.scoring_path)
    workouts = load_workouts(settings.workouts_dir)

    with connect(settings.sqlite_path) as conn:
        init_db(conn)
        wellness = read_wellness(conn)
        activities = read_activities(conn)
        checkins = read_checkins(conn)

    readiness = compute_readiness(today, wellness, activities, checkins, goals, scoring)
    confidence = compute_confidence(
        today, wellness, activities, goals, scoring,
        readiness.hrv_stat, readiness.rhr_stat,
    )
    rec = recommend(today, workouts, readiness, confidence, goals, activities, wellness)

    write_daily(settings, rec)
    write_weekly(settings, goals, today)
    write_monthly(settings, goals, today)

    from dataclasses import asdict

    with connect(settings.sqlite_path) as conn:
        upsert_recommendation(
            conn,
            date=today.isoformat(),
            workout_id=rec.main.workout_id if rec.main else None,
            readiness=readiness.score,
            readiness_level=readiness.level,
            confidence=confidence.level,
            payload={"main": asdict(rec.main) if rec.main else None},
        )

    # Also write the static disk version so file:// still works after exit.
    build_and_write_dashboard(settings)
