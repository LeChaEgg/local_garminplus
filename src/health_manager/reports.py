"""Daily / weekly / monthly report generation.

For each report we build a JSON payload first (the authoritative form), then
render Markdown from it. JSON files share the same stem as the Markdown
file. The `reports` table records every generation.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from .config import Goals, Settings, canonical_sport, load_sport_aliases
from .metrics import BaselineStat, baseline
from .recommender import Recommendation
from .storage import (
    connect,
    read_activities,
    read_body_metrics,
    read_checkins,
    read_wellness,
    record_report,
)

# ---------- daily ----------


def write_daily(settings: Settings, recommendation: Recommendation) -> tuple[Path, Path]:
    out_dir = settings.reports_dir / "daily"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = recommendation.date.isoformat()
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"

    payload = _daily_payload(recommendation)
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    md_path.write_text(_render_daily_md(payload), encoding="utf-8")

    with connect(settings.sqlite_path) as conn:
        record_report(conn, "daily", stem, md_path)
    return md_path, json_path


def _daily_payload(rec: Recommendation) -> dict[str, Any]:
    r = rec.readiness
    return {
        "date": rec.date.isoformat(),
        "readiness": {
            "score": round(r.score, 1),
            "level": r.level,
            "sleep_recovery_score": round(r.sleep_recovery_score, 1),
            "load_balance_score": round(r.load_balance_score, 1),
            "risk_penalty": round(r.risk_penalty, 1),
            "sub_scores": {
                "sleep_duration": round(r.sub_scores.sleep_duration, 1),
                "garmin_sleep_score": round(r.sub_scores.garmin_sleep_score, 1),
                "hrv_vs_baseline": round(r.sub_scores.hrv_vs_baseline, 1),
                "rhr_vs_baseline": round(r.sub_scores.rhr_vs_baseline, 1),
                "subjective": round(r.sub_scores.subjective, 1),
            },
            "load_balance": {
                "acwr_fit": round(r.load_balance.acwr_fit, 1),
                "days_since_hard": round(r.load_balance.days_since_hard, 1),
                "consecutive_days": round(r.load_balance.consecutive_days, 1),
                "soreness": round(r.load_balance.soreness, 1),
                "raw_acwr": r.load_balance.raw_acwr,
                "raw_days_since_hard": r.load_balance.raw_days_since_hard,
                "raw_consecutive_days": r.load_balance.raw_consecutive_days,
            },
            "reasons": r.reasons,
            "risk_flags": r.risk_flags,
            "hrv": _baseline_dict(r.hrv_stat),
            "rhr": _baseline_dict(r.rhr_stat),
        },
        "confidence": {
            "level": rec.confidence.level,
            "score": round(rec.confidence.score, 3),
            "present": rec.confidence.present,
            "baseline_stability": {k: round(v, 3) for k, v in rec.confidence.baseline_stability.items()},
        },
        "recommendation": {
            "main": _pick_dict(rec.main),
            "conservative": _pick_dict(rec.conservative),
            "progressive": _pick_dict(rec.progressive),
            "not_recommended": [
                {
                    "workout_id": r.workout_id,
                    "name": r.name,
                    "sport": r.sport,
                    "reasons": r.reasons,
                }
                for r in rec.not_recommended
            ],
        },
        "weekly_state": _serialize_weekly_state(rec.weekly_state),
    }


def _baseline_dict(b: BaselineStat | None) -> dict[str, Any] | None:
    if b is None:
        return None
    return {
        "mean": b.mean,
        "std": b.std,
        "latest": b.latest,
        "z": b.z,
        "n": b.n,
    }


def _fmt_baseline_line(d: dict[str, Any]) -> str:
    latest = "—" if d.get("latest") is None else f"{d['latest']:.1f}"
    mean = "—" if d.get("mean") is None else f"{d['mean']:.1f}"
    z = "—" if d.get("z") is None else f"{d['z']:+.2f}"
    return f"latest {latest}, baseline mean {mean} (n={d.get('n', 0)}, z={z})"


def _pick_dict(p) -> dict[str, Any] | None:
    if p is None:
        return None
    return asdict(p)


def _serialize_weekly_state(ws: dict) -> dict:
    """Convert pandas/native types to JSON-safe."""
    out: dict[str, Any] = {}
    for k, v in ws.items():
        if isinstance(v, dict):
            out[k] = {str(kk): float(vv) for kk, vv in v.items()}
        else:
            out[k] = v
    return out


def _render_daily_md(p: dict[str, Any]) -> str:
    r = p["readiness"]
    rec = p["recommendation"]
    conf = p["confidence"]
    main = rec["main"]
    cons = rec["conservative"]
    prog = rec["progressive"]

    lines: list[str] = []
    lines.append(f"# Daily report — {p['date']}")
    lines.append("")
    lines.append(f"**Readiness:** {r['score']:.0f}/100 ({r['level']})")
    lines.append(f"Sleep & recovery sub-score: {r['sleep_recovery_score']:.0f}")
    lines.append(f"Load balance sub-score: {r['load_balance_score']:.0f}")
    if r["risk_penalty"]:
        lines.append(f"Risk penalty: -{r['risk_penalty']:.0f}")
    lines.append("")

    lines.append("## Recommendation")
    lines.append("")
    if main:
        lines.append(
            f"- **Main:** {main['name']} ({main['sport']}, {main['category']}, "
            f"{main['duration_min']} min) — id: `{main['workout_id']}`"
        )
    else:
        lines.append("- **Main:** (none — no workout passed all filters)")
    if cons:
        lines.append(
            f"- Conservative alternative: {cons['name']} ({cons['sport']}, {cons['duration_min']} min)"
        )
    if prog:
        lines.append(
            f"- Progressive alternative: {prog['name']} ({prog['sport']}, {prog['duration_min']} min)"
        )
    lines.append(f"- Confidence: **{conf['level']}** (score {conf['score']:.2f})")
    lines.append("")

    if r["reasons"]:
        lines.append("## Reasons")
        for reason in r["reasons"]:
            lines.append(f"- {reason}")
        lines.append("")

    if r["risk_flags"]:
        lines.append("## Risk flags")
        for flag in r["risk_flags"]:
            lines.append(f"- :warning: {flag}")
        lines.append("")

    if rec["not_recommended"]:
        lines.append("## Not recommended today")
        for item in rec["not_recommended"]:
            joined = "; ".join(item["reasons"])
            lines.append(f"- {item['name']} ({item['sport']}) — {joined}")
        lines.append("")

    ws = p["weekly_state"]
    lines.append("## Weekly state")
    minutes = ws.get("minutes_by_sport") or {}
    if minutes:
        for sport, mins in minutes.items():
            lines.append(f"- {sport}: {mins:.0f} min")
    lines.append(f"- Z2 minutes this week: {ws.get('weekly_z2_minutes', 0):.0f}")
    lines.append(f"- Hard sessions this week: {ws.get('weekly_hard_sessions', 0)}")
    lines.append(f"- Strength sessions this week: {ws.get('weekly_strength_sessions', 0)}")
    lines.append(f"- Consecutive training days: {ws.get('consecutive_training_days', 0)}")
    if ws.get("days_since_last_hard") is not None:
        lines.append(f"- Days since last hard: {ws.get('days_since_last_hard')}")
    lines.append("")

    lines.append("## Data quality")
    for k, v in conf["present"].items():
        lines.append(f"- {k}: {'ok' if v else 'missing'}")
    for k, v in conf["baseline_stability"].items():
        lines.append(f"- baseline_{k}_stability: {v:.2f}")
    return "\n".join(lines) + "\n"


# ---------- weekly ----------


def write_weekly(settings: Settings, goals: Goals, as_of: date) -> tuple[Path, Path]:
    # Rolling 7-day window ending on as_of (inclusive).
    window_end = as_of
    window_start = as_of - timedelta(days=6)
    stem = window_end.isoformat()
    out_dir = settings.reports_dir / "weekly"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"

    with connect(settings.sqlite_path) as conn:
        wellness = read_wellness(conn)
        activities = read_activities(conn)
        body = read_body_metrics(conn)

    sport_aliases = load_sport_aliases(settings.sport_aliases_path)
    payload = _weekly_payload(
        window_start, window_end, wellness, activities, body, goals, sport_aliases
    )
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    md_path.write_text(_render_weekly_md(payload, goals), encoding="utf-8")

    with connect(settings.sqlite_path) as conn:
        record_report(conn, "weekly", stem, md_path)
    return md_path, json_path


def _weekly_payload(
    start: date,
    end: date,
    wellness: pd.DataFrame,
    activities: pd.DataFrame,
    body: pd.DataFrame,
    goals: Goals,
    sport_aliases: dict[str, str] | None = None,
) -> dict[str, Any]:
    if wellness.empty:
        wk_well = wellness
    else:
        wk_well = wellness[(wellness["date"] >= start) & (wellness["date"] <= end)]

    sleep_avg = (
        float(
            pd.to_numeric(
                wk_well.get("sleep_duration_min", pd.Series(dtype="float")),
                errors="coerce",
            ).mean()
            or 0
        )
        / 60.0
        if not wk_well.empty
        else 0.0
    )
    # Use `end` (the most recent day in the window) as the as-of date for baselines
    # so `latest` reflects the most recent observation rather than a future date.
    hrv_stat = (
        baseline(wellness, end, "hrv", goals.sleep_recovery.hrv_baseline_window_days)
        if not wellness.empty
        else None
    )
    rhr_stat = (
        baseline(wellness, end, "rhr", goals.sleep_recovery.rhr_baseline_window_days)
        if not wellness.empty
        else None
    )

    aliases = sport_aliases or load_sport_aliases(None)
    minutes_by_sport = _minutes_by_sport_window(activities, start, end, aliases)
    z2 = _z2_minutes_window(activities, start, end, aliases)
    hard = _hard_sessions_window(activities, start, end)
    strength = _strength_sessions_window(activities, start, end, aliases)

    body_delta = None
    if not body.empty:
        bw = body[(body["date"] >= start) & (body["date"] <= end)]
        if len(bw) >= 2 and "weight_kg" in bw.columns:
            body_delta = float(bw["weight_kg"].iloc[-1] - bw["weight_kg"].iloc[0])

    return {
        "period": "weekly",
        "window": "rolling_7d",
        "week_start": start.isoformat(),
        "week_end": end.isoformat(),
        "sleep": {
            "avg_hours": round(sleep_avg, 2),
        },
        "hrv": _baseline_dict(hrv_stat) if hrv_stat else None,
        "rhr": _baseline_dict(rhr_stat) if rhr_stat else None,
        "training": {
            "minutes_by_sport": {str(k): float(v) for k, v in minutes_by_sport.items()},
            "z2_minutes": z2,
            "hard_sessions": hard,
            "strength_sessions": strength,
        },
        "goal_progress": {
            "weekly_minutes_min": goals.aerobic.weekly_minutes_min,
            "weekly_minutes_actual": float(sum(minutes_by_sport.values())) if minutes_by_sport else 0.0,
            "weekly_z2_minutes_min": goals.aerobic.weekly_z2_minutes_min,
            "weekly_z2_minutes_actual": z2,
            "weekly_hard_sessions_max": goals.aerobic.weekly_hard_sessions_max,
            "weekly_hard_sessions_actual": hard,
            "weekly_strength_sessions_min": goals.strength.weekly_sessions_min,
            "weekly_strength_sessions_actual": strength,
        },
        "body": {"weight_kg_delta": body_delta},
    }


# Inline windowed aggregations for the rolling 7-day weekly report. The
# `metrics.weekly_*` helpers assume an ISO Mon-Sun window keyed off as_of and
# aren't reused here.

def _minutes_by_sport_window(
    activities: pd.DataFrame,
    start: date,
    end: date,
    sport_aliases: dict[str, str] | None = None,
) -> dict[str, float]:
    if activities.empty:
        return {}
    df = activities[(activities["date"] >= start) & (activities["date"] <= end)].copy()
    if df.empty:
        return {}
    df["duration_min"] = pd.to_numeric(df["duration_min"], errors="coerce").fillna(0)
    df["sport_key"] = _sport_key_series(df, sport_aliases)
    return df.groupby("sport_key")["duration_min"].sum().to_dict()


def _z2_minutes_window(
    activities: pd.DataFrame,
    start: date,
    end: date,
    sport_aliases: dict[str, str] | None = None,
) -> float:
    if activities.empty:
        return 0.0
    df = activities[(activities["date"] >= start) & (activities["date"] <= end)].copy()
    if df.empty:
        return 0.0
    df["duration_min"] = pd.to_numeric(df["duration_min"], errors="coerce").fillna(0)
    df["intensity"] = pd.to_numeric(df["intensity"], errors="coerce")
    sport_ok = _sport_key_series(df, sport_aliases).isin(["run", "bike", "walk", "hike"])
    z2_mask = (df["intensity"].isna() | (df["intensity"] < 70)) & sport_ok
    return float(df.loc[z2_mask, "duration_min"].sum())


def _hard_sessions_window(activities: pd.DataFrame, start: date, end: date) -> int:
    if activities.empty or "is_hard" not in activities.columns:
        return 0
    mask = (
        (activities["date"] >= start)
        & (activities["date"] <= end)
        & (activities["is_hard"] == 1)
    )
    return int(mask.sum())


def _strength_sessions_window(
    activities: pd.DataFrame,
    start: date,
    end: date,
    sport_aliases: dict[str, str] | None = None,
) -> int:
    if activities.empty:
        return 0
    df = activities[(activities["date"] >= start) & (activities["date"] <= end)]
    if df.empty:
        return 0
    return int((_sport_key_series(df, sport_aliases) == "strength").sum())


def _sport_key_series(
    activities: pd.DataFrame, sport_aliases: dict[str, str] | None = None
) -> pd.Series:
    aliases = sport_aliases or load_sport_aliases(None)
    raw = pd.Series("", index=activities.index, dtype="object")
    if "type" in activities.columns:
        raw = activities["type"].fillna("").astype(str)
    if "sport" in activities.columns:
        raw = activities["sport"].fillna("").astype(str).where(
            activities["sport"].fillna("").astype(str) != "", raw
        )
    canonical = raw.map(lambda value: canonical_sport(value, aliases))
    if "sport_canonical" in activities.columns:
        stored = activities["sport_canonical"].fillna("").astype(str).str.strip().str.lower()
        canonical = canonical.where(stored == "", stored)
    return canonical.replace("", "other")


def _render_weekly_md(p: dict[str, Any], goals: Goals) -> str:
    lines: list[str] = []
    lines.append(
        f"# Weekly report (rolling 7 days) — {p['week_start']} to {p['week_end']}"
    )
    lines.append("")
    lines.append(f"- Avg sleep: {p['sleep']['avg_hours']:.2f} h")
    if p["hrv"]:
        lines.append(f"- HRV: {_fmt_baseline_line(p['hrv'])}")
    if p["rhr"]:
        lines.append(f"- RHR: {_fmt_baseline_line(p['rhr'])}")
    lines.append("")
    lines.append("## Training volume")
    t = p["training"]
    for sport, mins in t["minutes_by_sport"].items():
        lines.append(f"- {sport}: {mins:.0f} min")
    lines.append(f"- Z2 minutes: {t['z2_minutes']:.0f}")
    lines.append(f"- Hard sessions: {t['hard_sessions']}")
    lines.append(f"- Strength sessions: {t['strength_sessions']}")
    lines.append("")
    g = p["goal_progress"]
    lines.append("## Goal progress")
    lines.append(
        f"- Aerobic minutes: {g['weekly_minutes_actual']:.0f} / {g['weekly_minutes_min']}"
    )
    lines.append(
        f"- Z2 minutes: {g['weekly_z2_minutes_actual']:.0f} / {g['weekly_z2_minutes_min']}"
    )
    lines.append(
        f"- Hard sessions: {g['weekly_hard_sessions_actual']} (cap {g['weekly_hard_sessions_max']})"
    )
    lines.append(
        f"- Strength sessions: {g['weekly_strength_sessions_actual']} / {g['weekly_strength_sessions_min']}"
    )
    if p["body"]["weight_kg_delta"] is not None:
        lines.append(f"- Weight change this week: {p['body']['weight_kg_delta']:+.2f} kg")
    lines.append("")
    lines.append("## Next week")
    needs = []
    if g["weekly_strength_sessions_actual"] < g["weekly_strength_sessions_min"]:
        needs.append("more strength sessions")
    if g["weekly_z2_minutes_actual"] < g["weekly_z2_minutes_min"]:
        needs.append("more Z2 aerobic volume")
    if needs:
        lines.append(f"- Focus on: {', '.join(needs)}")
    else:
        lines.append("- On track; maintain consistency.")
    return "\n".join(lines) + "\n"


# ---------- monthly ----------


def write_monthly(settings: Settings, goals: Goals, as_of: date) -> tuple[Path, Path]:
    month_start = as_of.replace(day=1)
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    month_end = next_month - timedelta(days=1)
    stem = month_start.strftime("%Y-%m")
    out_dir = settings.reports_dir / "monthly"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"

    with connect(settings.sqlite_path) as conn:
        wellness = read_wellness(conn)
        activities = read_activities(conn)
        body = read_body_metrics(conn)
        _checkins = read_checkins(conn)  # unused for now, reserved for future

    sport_aliases = load_sport_aliases(settings.sport_aliases_path)
    payload = _monthly_payload(
        month_start, month_end, wellness, activities, body, goals, sport_aliases
    )
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    md_path.write_text(_render_monthly_md(payload, goals), encoding="utf-8")

    with connect(settings.sqlite_path) as conn:
        record_report(conn, "monthly", stem, md_path)
    return md_path, json_path


def _monthly_payload(
    month_start: date,
    month_end: date,
    wellness: pd.DataFrame,
    activities: pd.DataFrame,
    body: pd.DataFrame,
    goals: Goals,
    sport_aliases: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not wellness.empty:
        mw = wellness[(wellness["date"] >= month_start) & (wellness["date"] <= month_end)]
    else:
        mw = wellness
    if not activities.empty:
        ma = activities[(activities["date"] >= month_start) & (activities["date"] <= month_end)]
    else:
        ma = activities

    sleep_avg = (
        float(pd.to_numeric(mw["sleep_duration_min"], errors="coerce").mean() or 0) / 60.0
        if not mw.empty and "sleep_duration_min" in mw.columns
        else 0.0
    )
    minutes_by_sport: dict[str, float] = {}
    if not ma.empty:
        ma2 = ma.copy()
        ma2["duration_min"] = pd.to_numeric(ma2["duration_min"], errors="coerce").fillna(0)
        ma2["sport_key"] = _sport_key_series(ma2, sport_aliases)
        minutes_by_sport = ma2.groupby("sport_key")["duration_min"].sum().to_dict()

    body_delta = None
    if not body.empty:
        bm = body[(body["date"] >= month_start) & (body["date"] <= month_end)]
        if len(bm) >= 2 and "weight_kg" in bm.columns:
            body_delta = float(bm["weight_kg"].iloc[-1] - bm["weight_kg"].iloc[0])

    return {
        "period": "monthly",
        "month": month_start.strftime("%Y-%m"),
        "sleep_avg_hours": round(sleep_avg, 2),
        "minutes_by_sport": {str(k): float(v) for k, v in minutes_by_sport.items()},
        "total_minutes": float(sum(minutes_by_sport.values())) if minutes_by_sport else 0.0,
        "body": {"weight_kg_delta": body_delta},
    }


def _render_monthly_md(p: dict[str, Any], goals: Goals) -> str:
    lines: list[str] = []
    lines.append(f"# Monthly report — {p['month']}")
    lines.append("")
    lines.append(f"- Avg sleep: {p['sleep_avg_hours']:.2f} h")
    lines.append(f"- Total training minutes: {p['total_minutes']:.0f}")
    lines.append("")
    lines.append("## Time by sport")
    for sport, mins in p["minutes_by_sport"].items():
        lines.append(f"- {sport}: {mins:.0f} min")
    lines.append("")
    if p["body"]["weight_kg_delta"] is not None:
        lines.append(f"## Body\n\nWeight change: {p['body']['weight_kg_delta']:+.2f} kg")
    lines.append("")
    lines.append("## Suggestions")
    expected = goals.aerobic.weekly_minutes_min * 4
    if p["total_minutes"] < 0.8 * expected:
        lines.append(
            f"- Volume is well under target ({p['total_minutes']:.0f} vs ~{expected} min). "
            "Consider scheduling more easy aerobic sessions next month."
        )
    elif p["total_minutes"] > 1.3 * expected:
        lines.append(
            "- Volume is above target. Make sure sleep and HRV trends are healthy."
        )
    else:
        lines.append("- Volume is in the right ballpark. Hold the line.")
    return "\n".join(lines) + "\n"
