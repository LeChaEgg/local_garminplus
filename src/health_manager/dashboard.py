"""Static HTML dashboard generator.

Produces a single self-contained `data/reports/dashboard.html` file from the
latest SQLite + reports/* state. No JavaScript, no external assets, no server
— just hand-written HTML/CSS and a few inline SVG sparklines. The `health
dashboard` CLI command calls `build_and_write_dashboard(...)` and then opens
the file in the default browser.
"""

from __future__ import annotations

import html
import json
from datetime import date, timedelta
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


def build_and_write_dashboard(settings: Settings) -> Path:
    """Render the dashboard HTML and write it to `data/reports/dashboard.html`."""
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

    html_doc = _build_html(settings, goals, wellness, activities, body, recs, workouts)
    out = settings.reports_dir / "dashboard.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_doc, encoding="utf-8")
    return out


# ---------- top-level HTML ----------


_CSS = """
* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue",
               Arial, sans-serif;
  margin: 0;
  background: #fafafa;
  color: #1f1f1f;
  line-height: 1.45;
}
header {
  background: #1f2933;
  color: #f5f7fa;
  padding: 18px 28px;
}
header h1 { margin: 0; font-size: 22px; }
header .meta { font-size: 12px; color: #cbd2d9; margin-top: 4px; }
main { max-width: 1100px; margin: 0 auto; padding: 24px; }
section { background: #fff; border: 1px solid #e4e7eb; border-radius: 8px;
          padding: 18px 22px; margin-bottom: 20px; }
section h2 { margin-top: 0; font-size: 17px; border-bottom: 1px solid #eef0f3;
             padding-bottom: 6px; }
section h3 { font-size: 14px; color: #52606d; margin-top: 18px; margin-bottom: 6px;
             text-transform: uppercase; letter-spacing: 0.5px; }
.kv { display: grid; grid-template-columns: 220px 1fr; row-gap: 4px; column-gap: 12px;
      font-size: 14px; }
.kv .k { color: #52606d; }
.metric-row { display: flex; gap: 24px; flex-wrap: wrap; margin: 12px 0; }
.metric { background: #f5f7fa; border-radius: 6px; padding: 10px 16px; min-width: 140px; }
.metric .label { font-size: 11px; color: #52606d; text-transform: uppercase;
                 letter-spacing: 0.5px; }
.metric .value { font-size: 22px; font-weight: 600; margin-top: 2px; }
.metric .sub { font-size: 12px; color: #7b8794; margin-top: 2px; }
table { border-collapse: collapse; width: 100%; font-size: 13px; margin-top: 8px; }
th, td { border-bottom: 1px solid #eef0f3; padding: 6px 8px; text-align: left; }
th { background: #f5f7fa; font-weight: 600; color: #52606d; font-size: 12px; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.level-green       { color: #0a7d3b; font-weight: 600; }
.level-green_yellow{ color: #6c8500; font-weight: 600; }
.level-yellow      { color: #b07d00; font-weight: 600; }
.level-orange      { color: #c25300; font-weight: 600; }
.level-red         { color: #b3261e; font-weight: 600; }
.tag { display: inline-block; background: #f0f4f8; color: #334e68;
       border-radius: 4px; padding: 2px 6px; font-size: 11px; margin-right: 3px; }
.warn { background: #fff7e6; border-left: 3px solid #d9a300; padding: 6px 10px;
        margin: 6px 0; font-size: 13px; }
.muted { color: #7b8794; font-style: italic; }
pre { background: #f5f7fa; padding: 10px; border-radius: 4px; font-size: 12px;
      overflow-x: auto; }
.spark { vertical-align: middle; }
.spark-row { display: flex; align-items: center; gap: 10px; margin: 4px 0; font-size: 13px; }
.spark-row .label { width: 80px; color: #52606d; font-size: 12px; }
.spark-row .value { font-variant-numeric: tabular-nums; font-weight: 600; min-width: 70px; }
.spark-row .sub { color: #7b8794; font-size: 12px; }
footer { text-align: center; color: #7b8794; font-size: 11px; padding: 18px; }
"""


def _build_html(
    settings: Settings,
    goals: Goals | None,
    wellness: pd.DataFrame,
    activities: pd.DataFrame,
    body: pd.DataFrame,
    recs: pd.DataFrame,
    workouts: list[Workout],
) -> str:
    daily_payload = _latest_json(settings.reports_dir / "daily")
    weekly_payload = _latest_json(settings.reports_dir / "weekly")
    monthly_payload = _latest_json(settings.reports_dir / "monthly")

    generated_at = (
        pd.Timestamp.now(tz=settings.local_timezone or "UTC")
        .strftime("%Y-%m-%d %H:%M %Z")
    )

    sections = [
        _section_today(daily_payload),
        _section_trends(wellness, activities),
        _section_weekly(weekly_payload),
        _section_monthly(monthly_payload),
        _section_goals(goals),
        _section_workouts(workouts),
        _section_data_quality(wellness, activities, body, recs),
    ]

    return (
        "<!doctype html>\n"
        "<html lang=\"en\"><head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>Health Manager — {html.escape(generated_at)}</title>\n"
        f"<style>{_CSS}</style>\n"
        "</head><body>\n"
        '<header>\n'
        '  <h1>Health Manager</h1>\n'
        f'  <div class="meta">Local dashboard · generated {html.escape(generated_at)} · '
        f'data stays on this machine</div>\n'
        "</header>\n"
        "<main>\n"
        + "\n".join(sections)
        + "\n</main>\n"
        '<footer>health-manager · open this file directly in your browser at any time</footer>\n'
        "</body></html>\n"
    )


# ---------- section: today ----------


def _section_today(p: dict[str, Any] | None) -> str:
    if not p:
        return _wrap_section(
            "Today",
            '<p class="muted">No daily report yet. Run <code>health today</code>.</p>',
        )
    r = p["readiness"]
    rec = p["recommendation"]
    conf = p["confidence"]
    level_cls = f"level-{r['level']}"

    main = rec.get("main")
    cons = rec.get("conservative")
    prog = rec.get("progressive")

    main_html = (
        f"<strong>{html.escape(main['name'])}</strong> "
        f"({html.escape(main['sport'])}, {main['duration_min']} min) "
        f"<span class='muted'>{html.escape(main['workout_id'])}</span>"
        if main
        else "<span class='muted'>(no workout passed filters)</span>"
    )

    risk_html = ""
    if r.get("risk_flags"):
        risk_html = "".join(
            f"<div class='warn'>⚠ {html.escape(f)}</div>" for f in r["risk_flags"]
        )

    reasons_html = "".join(
        f"<li>{html.escape(reason)}</li>" for reason in r.get("reasons", [])
    )

    nr_rows = "".join(
        f"<tr><td>{html.escape(item['name'])}</td>"
        f"<td>{html.escape(item['sport'])}</td>"
        f"<td>{html.escape('; '.join(item['reasons']))}</td></tr>"
        for item in rec.get("not_recommended", [])
    )
    nr_html = (
        '<h3>Not recommended today</h3>'
        '<table><thead><tr><th>Workout</th><th>Sport</th><th>Reasons</th></tr></thead>'
        f"<tbody>{nr_rows}</tbody></table>"
        if nr_rows
        else ""
    )

    body_html = f"""
<div class="metric-row">
  <div class="metric"><div class="label">Readiness</div>
    <div class="value {level_cls}">{r['score']:.0f}</div>
    <div class="sub {level_cls}">{html.escape(r['level'])}</div></div>
  <div class="metric"><div class="label">Sleep &amp; Recovery</div>
    <div class="value">{r['sleep_recovery_score']:.0f}</div></div>
  <div class="metric"><div class="label">Load Balance</div>
    <div class="value">{r['load_balance_score']:.0f}</div></div>
  <div class="metric"><div class="label">Confidence</div>
    <div class="value">{html.escape(conf['level'])}</div>
    <div class="sub">{conf['score']:.2f}</div></div>
</div>
<h3>Recommendation for {html.escape(p['date'])}</h3>
<div>Main: {main_html}</div>
{f"<div class='muted'>Conservative: {html.escape(cons['name'])} ({cons['duration_min']} min)</div>" if cons else ""}
{f"<div class='muted'>Progressive: {html.escape(prog['name'])} ({prog['duration_min']} min)</div>" if prog else ""}
{risk_html}
<h3>Reasons</h3>
<ul>{reasons_html}</ul>
{nr_html}
"""
    return _wrap_section("Today", body_html)


# ---------- section: trends ----------


def _section_trends(wellness: pd.DataFrame, activities: pd.DataFrame) -> str:
    if wellness.empty and activities.empty:
        return _wrap_section(
            "Trends",
            '<p class="muted">No data yet. Run <code>health sync</code>.</p>',
        )

    rows: list[str] = []
    if not wellness.empty:
        w = wellness.copy()
        w["date"] = pd.to_datetime(w["date"])
        w = w.set_index("date").sort_index().tail(60)
        for col, label, lower_is_better in (
            ("hrv", "HRV", False),
            ("rhr", "RHR", True),
            ("sleep_duration_min", "Sleep min", False),
            ("garmin_sleep_score", "Sleep score", False),
            ("ctl", "CTL (fitness)", False),
            ("atl", "ATL (fatigue)", False),
        ):
            if col in w.columns:
                series = pd.to_numeric(w[col], errors="coerce").dropna()
                if len(series) >= 3:
                    rows.append(_spark_row(label, series, lower_is_better))

    if not activities.empty:
        a = activities.copy()
        a["date"] = pd.to_datetime(a["date"])
        a["load"] = pd.to_numeric(a["load"], errors="coerce").fillna(0)
        daily_load = a.groupby(a["date"].dt.date)["load"].sum()
        # ensure continuous range over last 60 days
        if not daily_load.empty:
            idx = pd.date_range(end=daily_load.index.max(), periods=60).date
            daily_load = daily_load.reindex(idx, fill_value=0)
            rows.append(_spark_row("Daily load", pd.Series(daily_load.values), False))

    body_html = "<p class='muted'>Last 60 days. Right-most point is the most recent.</p>"
    body_html += "".join(rows) if rows else "<p class='muted'>Not enough data.</p>"
    return _wrap_section("Trends", body_html)


def _spark_row(label: str, series: pd.Series, lower_is_better: bool) -> str:
    values = [float(v) for v in series.values]
    latest = values[-1]
    sub = ""
    if len(values) >= 14:
        baseline_window = values[-29:-1] if len(values) > 28 else values[:-1]
        if baseline_window:
            mean = sum(baseline_window) / len(baseline_window)
            delta = latest - mean
            sign = "+" if delta >= 0 else ""
            good = (delta < 0) if lower_is_better else (delta > 0)
            color = "#0a7d3b" if good else "#b3261e"
            sub = (
                f"<span class='sub' style='color:{color}'>"
                f"{sign}{delta:.1f} vs 28d mean {mean:.1f}</span>"
            )
    svg = _sparkline_svg(values)
    return (
        f"<div class='spark-row'>"
        f"<span class='label'>{html.escape(label)}</span>"
        f"{svg}"
        f"<span class='value'>{latest:.1f}</span>"
        f"{sub}"
        f"</div>"
    )


def _sparkline_svg(values: list[float], width: int = 200, height: int = 28) -> str:
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    span = hi - lo if hi > lo else 1.0
    n = len(values)
    pts = []
    for i, v in enumerate(values):
        x = (i / (n - 1)) * (width - 4) + 2
        y = height - 2 - ((v - lo) / span) * (height - 4)
        pts.append(f"{x:.1f},{y:.1f}")
    polyline = " ".join(pts)
    last_x, last_y = pts[-1].split(",")
    return (
        f'<svg class="spark" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">'
        f'<polyline points="{polyline}" fill="none" stroke="#3e5879" '
        f'stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{last_x}" cy="{last_y}" r="2.4" fill="#b3261e"/>'
        f'</svg>'
    )


# ---------- section: weekly / monthly ----------


def _section_weekly(p: dict[str, Any] | None) -> str:
    if not p:
        return _wrap_section(
            "Weekly (rolling 7d)",
            '<p class="muted">No weekly report yet. Run <code>health report weekly</code>.</p>',
        )
    g = p.get("goal_progress", {})
    rows = [
        ("Window", f"{p['week_start']} → {p['week_end']}"),
        ("Avg sleep", f"{p['sleep']['avg_hours']:.2f} h"),
    ]
    if p.get("hrv"):
        h = p["hrv"]
        rows.append(
            (
                "HRV",
                f"latest {_fmt(h.get('latest'))}, baseline mean "
                f"{_fmt(h.get('mean'))} (n={h.get('n', 0)})",
            )
        )
    if p.get("rhr"):
        h = p["rhr"]
        rows.append(
            (
                "RHR",
                f"latest {_fmt(h.get('latest'))}, baseline mean "
                f"{_fmt(h.get('mean'))} (n={h.get('n', 0)})",
            )
        )

    kv = "".join(
        f"<div class='k'>{html.escape(k)}</div><div>{html.escape(str(v))}</div>"
        for k, v in rows
    )

    minutes = p.get("training", {}).get("minutes_by_sport", {}) or {}
    minutes_rows = "".join(
        f"<tr><td>{html.escape(sport)}</td><td class='num'>{mins:.0f}</td></tr>"
        for sport, mins in sorted(minutes.items(), key=lambda kv: -kv[1])
    )
    minutes_html = (
        '<h3>Minutes by sport</h3>'
        '<table><thead><tr><th>Sport</th><th>min</th></tr></thead>'
        f"<tbody>{minutes_rows}</tbody></table>"
        if minutes_rows
        else ""
    )

    goal_rows = "".join(
        f"<tr><td>{html.escape(label)}</td>"
        f"<td class='num'>{actual:.0f}</td>"
        f"<td class='num muted'>{target}</td></tr>"
        for label, actual, target in [
            ("Aerobic minutes", g.get("weekly_minutes_actual", 0), g.get("weekly_minutes_min", 0)),
            ("Z2 minutes", g.get("weekly_z2_minutes_actual", 0), g.get("weekly_z2_minutes_min", 0)),
            ("Hard sessions", g.get("weekly_hard_sessions_actual", 0), f"≤ {g.get('weekly_hard_sessions_max', 0)}"),
            ("Strength sessions", g.get("weekly_strength_sessions_actual", 0), g.get("weekly_strength_sessions_min", 0)),
        ]
    )
    goal_html = (
        '<h3>Goal progress</h3>'
        '<table><thead><tr><th>Goal</th><th>Actual</th><th>Target</th></tr></thead>'
        f"<tbody>{goal_rows}</tbody></table>"
    )

    return _wrap_section(
        "Weekly (rolling 7d)",
        f'<div class="kv">{kv}</div>{minutes_html}{goal_html}',
    )


def _section_monthly(p: dict[str, Any] | None) -> str:
    if not p:
        return _wrap_section(
            "Monthly",
            '<p class="muted">No monthly report yet. Run <code>health report monthly</code>.</p>',
        )
    rows = [
        ("Month", p.get("month", "")),
        ("Avg sleep", f"{p.get('sleep_avg_hours', 0):.2f} h"),
        ("Total training", f"{p.get('total_minutes', 0):.0f} min"),
    ]
    if p.get("body", {}).get("weight_kg_delta") is not None:
        rows.append(("Weight Δ", f"{p['body']['weight_kg_delta']:+.2f} kg"))
    kv = "".join(
        f"<div class='k'>{html.escape(k)}</div><div>{html.escape(str(v))}</div>"
        for k, v in rows
    )

    minutes = p.get("minutes_by_sport", {}) or {}
    minutes_rows = "".join(
        f"<tr><td>{html.escape(sport)}</td><td class='num'>{mins:.0f}</td></tr>"
        for sport, mins in sorted(minutes.items(), key=lambda kv: -kv[1])
    )
    minutes_html = (
        '<h3>Minutes by sport</h3>'
        '<table><thead><tr><th>Sport</th><th>min</th></tr></thead>'
        f"<tbody>{minutes_rows}</tbody></table>"
        if minutes_rows
        else ""
    )
    return _wrap_section("Monthly", f'<div class="kv">{kv}</div>{minutes_html}')


# ---------- section: goals ----------


def _section_goals(goals: Goals | None) -> str:
    if not goals:
        return _wrap_section(
            "Goals",
            '<p class="muted">No goals.md yet. Run <code>health init</code>.</p>',
        )
    pretty = json.dumps(goals.model_dump(mode="json"), indent=2, default=str)
    narrative = (
        f"<h3>Narrative</h3><pre>{html.escape(goals.narrative_notes)}</pre>"
        if goals.narrative_notes
        else ""
    )
    return _wrap_section(
        "Goals",
        f"<pre>{html.escape(pretty)}</pre>{narrative}",
    )


# ---------- section: workouts ----------


def _section_workouts(workouts: list[Workout]) -> str:
    if not workouts:
        return _wrap_section(
            "Workout library",
            '<p class="muted">No workouts loaded.</p>',
        )
    rows = "".join(
        f"<tr>"
        f"<td>{html.escape(w.id)}</td>"
        f"<td>{html.escape(w.meta.name)}</td>"
        f"<td>{html.escape(w.sport)}</td>"
        f"<td>{html.escape(w.meta.category)}</td>"
        f"<td class='num'>{w.meta.duration_min}</td>"
        f"<td class='num'>{w.meta.intensity}</td>"
        f"<td class='num'>{w.meta.recovery_cost}</td>"
        f"<td class='num'>{w.meta.min_readiness}</td>"
        f"<td>{''.join(f'<span class=tag>{html.escape(t)}</span>' for t in w.meta.tags)}</td>"
        f"</tr>"
        for w in workouts
    )
    table = (
        '<table><thead><tr>'
        '<th>ID</th><th>Name</th><th>Sport</th><th>Category</th>'
        '<th>Duration</th><th>Intensity</th><th>Recovery cost</th>'
        '<th>Min readiness</th><th>Tags</th>'
        '</tr></thead>'
        f"<tbody>{rows}</tbody></table>"
    )
    return _wrap_section("Workout library", table)


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
        '<table><thead><tr><th>Table</th><th>Rows</th></tr></thead>'
        f"<tbody>{counts_rows}</tbody></table>"
    )

    coverage_html = ""
    if not wellness.empty:
        w = wellness.copy()
        w["date"] = pd.to_datetime(w["date"])
        cutoff = w["date"].max() - pd.Timedelta(days=29)
        recent = w[w["date"] >= cutoff]
        fields = [
            "hrv", "rhr", "garmin_sleep_score", "sleep_duration_min",
            "vo2max", "steps", "respiration", "spo2",
        ]
        rows = []
        for f in fields:
            if f in recent.columns:
                filled = int(pd.to_numeric(recent[f], errors="coerce").notna().sum())
                rows.append(
                    f"<tr><td>{html.escape(f)}</td>"
                    f"<td class='num'>{filled}</td>"
                    f"<td class='num muted'>/ {len(recent)}</td></tr>"
                )
        if rows:
            coverage_html = (
                '<h3>Wellness coverage (last 30 days)</h3>'
                '<table><thead><tr><th>Field</th><th>Filled</th><th>Days</th></tr></thead>'
                f"<tbody>{''.join(rows)}</tbody></table>"
            )

    return _wrap_section("Data quality", f"{counts_html}{coverage_html}")


# ---------- helpers ----------


def _wrap_section(title: str, body_html: str) -> str:
    return f'<section><h2>{html.escape(title)}</h2>\n{body_html}\n</section>'


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


# Convenience for tests/manual exploration; not auto-executed when imported.
_ = (date, timedelta)
