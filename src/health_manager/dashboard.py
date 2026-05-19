"""Local Streamlit dashboard.

Read-only view over SQLite + reports/ + workouts/. Launch with `health dashboard`
(which runs `streamlit run` on this file).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from .config import load_goals, load_settings
from .storage import (
    connect,
    read_activities,
    read_body_metrics,
    read_recommendations,
    read_wellness,
)
from .workout_library import load_workouts


@st.cache_data(show_spinner=False)
def _load_state(root: str | None = None):
    settings = load_settings(Path(root) if root else None)
    goals = None
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
    workouts = load_workouts(settings.workouts_dir) if settings.workouts_dir.exists() else []
    return settings, goals, wellness, activities, body, recs, workouts


def _latest_daily_json(reports_dir: Path) -> dict | None:
    daily = reports_dir / "daily"
    if not daily.exists():
        return None
    files = sorted(daily.glob("*.json"))
    if not files:
        return None
    return json.loads(files[-1].read_text(encoding="utf-8"))


def run() -> None:
    st.set_page_config(page_title="Health Manager", layout="wide")
    st.title("Health Manager")

    with st.sidebar:
        st.caption("Local dashboard. No data leaves this machine.")
        if st.button("Refresh data"):
            _load_state.clear()

    settings, goals, wellness, activities, body, recs, workouts = _load_state()

    if not settings.sqlite_path.exists():
        st.warning("SQLite database not found. Run `health init` and `health sync`.")
        return

    tabs = st.tabs(["Today", "Weekly", "Monthly", "Goals", "Workout Library", "Data Quality"])

    with tabs[0]:
        _render_today(settings.reports_dir)

    with tabs[1]:
        _render_weekly(settings.reports_dir, wellness, activities)

    with tabs[2]:
        _render_monthly(settings.reports_dir, wellness, activities)

    with tabs[3]:
        _render_goals(goals)

    with tabs[4]:
        _render_workout_library(workouts)

    with tabs[5]:
        _render_data_quality(wellness, activities, body, recs)


def _render_today(reports_dir: Path) -> None:
    st.header("Today")
    payload = _latest_daily_json(reports_dir)
    if not payload:
        st.info("No daily report yet. Run `health today`.")
        return
    r = payload["readiness"]
    rec = payload["recommendation"]

    c1, c2, c3 = st.columns(3)
    c1.metric("Readiness", f"{r['score']:.0f}", help=f"Level: {r['level']}")
    c2.metric("Sleep & Recovery", f"{r['sleep_recovery_score']:.0f}")
    c3.metric("Load Balance", f"{r['load_balance_score']:.0f}")

    st.subheader("Recommendation")
    if rec["main"]:
        m = rec["main"]
        st.markdown(f"**{m['name']}** ({m['sport']}, {m['category']}, {m['duration_min']} min)")
    if rec["conservative"]:
        st.markdown(f"_Conservative:_ {rec['conservative']['name']}")
    if rec["progressive"]:
        st.markdown(f"_Progressive:_ {rec['progressive']['name']}")
    st.markdown(f"Confidence: **{payload['confidence']['level']}** "
                f"({payload['confidence']['score']:.2f})")

    if r.get("risk_flags"):
        st.warning("\n".join(f"- {f}" for f in r["risk_flags"]))

    with st.expander("Reasons"):
        for reason in r["reasons"]:
            st.markdown(f"- {reason}")

    with st.expander("Not recommended"):
        for item in rec["not_recommended"]:
            st.markdown(f"- **{item['name']}** ({item['sport']}): {'; '.join(item['reasons'])}")


def _render_weekly(reports_dir: Path, wellness: pd.DataFrame, activities: pd.DataFrame) -> None:
    st.header("Weekly")
    weekly_dir = reports_dir / "weekly"
    files = sorted(weekly_dir.glob("*.json")) if weekly_dir.exists() else []
    if files:
        sel = st.selectbox("Week", [f.stem for f in files], index=len(files) - 1)
        payload = json.loads((weekly_dir / f"{sel}.json").read_text(encoding="utf-8"))
        st.json(payload)
    else:
        st.info("No weekly reports yet. Run `health report weekly`.")

    if not activities.empty:
        st.subheader("Activity volume (last 12 weeks)")
        df = activities.copy()
        df["date"] = pd.to_datetime(df["date"])
        df["duration_min"] = pd.to_numeric(df["duration_min"], errors="coerce").fillna(0)
        weekly = df.set_index("date").resample("W")["duration_min"].sum().tail(12)
        st.bar_chart(weekly)


def _render_monthly(reports_dir: Path, wellness: pd.DataFrame, activities: pd.DataFrame) -> None:
    st.header("Monthly")
    monthly_dir = reports_dir / "monthly"
    files = sorted(monthly_dir.glob("*.json")) if monthly_dir.exists() else []
    if files:
        sel = st.selectbox("Month", [f.stem for f in files], index=len(files) - 1, key="monthly")
        payload = json.loads((monthly_dir / f"{sel}.json").read_text(encoding="utf-8"))
        st.json(payload)
    else:
        st.info("No monthly reports yet. Run `health report monthly`.")

    if not wellness.empty:
        st.subheader("HRV & RHR trend")
        df = wellness.copy()
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")[["hrv", "rhr"]].dropna(how="all").tail(180)
        if not df.empty:
            st.line_chart(df)


def _render_goals(goals) -> None:
    st.header("Goals")
    if not goals:
        st.info("No goals.md found. Run `health init`.")
        return
    st.json(goals.model_dump(mode="json"))
    if goals.narrative_notes:
        st.subheader("Narrative")
        st.markdown(goals.narrative_notes)


def _render_workout_library(workouts) -> None:
    st.header("Workout library")
    if not workouts:
        st.info("No workouts loaded.")
        return
    rows = []
    for wk in workouts:
        rows.append(
            {
                "id": wk.id,
                "name": wk.meta.name,
                "sport": wk.sport,
                "category": wk.meta.category,
                "duration_min": wk.meta.duration_min,
                "intensity": wk.meta.intensity,
                "recovery_cost": wk.meta.recovery_cost,
                "min_readiness": wk.meta.min_readiness,
                "tags": ", ".join(wk.meta.tags),
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True)
    sel = st.selectbox("View workout", [w.id for w in workouts])
    chosen = next((w for w in workouts if w.id == sel), None)
    if chosen:
        st.code(chosen.body)


def _render_data_quality(
    wellness: pd.DataFrame,
    activities: pd.DataFrame,
    body: pd.DataFrame,
    recs: pd.DataFrame,
) -> None:
    st.header("Data quality")
    st.write(f"Wellness rows: {len(wellness)}")
    st.write(f"Activities: {len(activities)}")
    st.write(f"Manual body metrics: {len(body)}")
    st.write(f"Daily recommendations recorded: {len(recs)}")
    if not wellness.empty:
        st.subheader("Wellness coverage (last 60 days)")
        df = wellness.copy()
        df["date"] = pd.to_datetime(df["date"])
        cov = (
            df.set_index("date").tail(60)[
                ["hrv", "rhr", "garmin_sleep_score", "sleep_duration_min"]
            ]
            .notna()
            .astype(int)
        )
        st.dataframe(cov)


run()
