"""SQLite storage layer.

Uses raw `sqlite3` for writes and `pandas.read_sql` for reads. Schema lives
entirely in `SCHEMA_STATEMENTS`. A `_meta` table stores the schema version; on
mismatch we drop and recreate all tables (safe because `data/raw/` keeps the
original JSON responses).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2

SCHEMA_STATEMENTS: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS _meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS wellness_daily (
        date TEXT PRIMARY KEY,
        hrv REAL,
        hrv_sdnn REAL,
        rhr REAL,
        avg_sleeping_hr REAL,
        garmin_sleep_score REAL,
        sleep_quality_score REAL,
        sleep_duration_min REAL,
        sleep_latency_min REAL,
        respiration REAL,
        spo2 REAL,
        soreness REAL,
        fatigue REAL,
        motivation REAL,
        mood REAL,
        stress REAL,
        weight_kg REAL,
        body_fat_pct REAL,
        waist_cm REAL,
        bp_systolic REAL,
        bp_diastolic REAL,
        vo2max REAL,
        steps REAL,
        garmin_readiness REAL,
        illness INTEGER,
        injury INTEGER,
        ctl REAL,
        atl REAL,
        ctl_load REAL,
        atl_load REAL,
        ramp_rate REAL,
        comments TEXT,
        source_updated_at TEXT,
        raw_json TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS activities (
        id TEXT PRIMARY KEY,
        date TEXT NOT NULL,
        sport TEXT,
        type TEXT,
        name TEXT,
        description TEXT,
        duration_min REAL,
        elapsed_min REAL,
        distance_m REAL,
        load REAL,
        intensity REAL,
        efficiency_factor REAL,
        variability_index REAL,
        decoupling REAL,
        polarization_index REAL,
        avg_hr REAL,
        max_hr REAL,
        avg_power REAL,
        avg_cadence REAL,
        avg_speed REAL,
        max_speed REAL,
        elevation_gain_m REAL,
        calories REAL,
        kg_lifted REAL,
        feel REAL,
        perceived_exertion REAL,
        session_rpe REAL,
        ftp REAL,
        lthr REAL,
        trimp REAL,
        hr_load REAL,
        pace_load REAL,
        power_load REAL,
        source_updated_at TEXT,
        is_hard INTEGER DEFAULT 0,
        raw_json TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS activity_intervals (
        activity_id TEXT NOT NULL,
        idx INTEGER NOT NULL,
        type TEXT,
        duration_s REAL,
        avg_hr REAL,
        avg_power REAL,
        intensity REAL,
        raw_json TEXT,
        PRIMARY KEY (activity_id, idx)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS manual_body_metrics (
        date TEXT PRIMARY KEY,
        weight_kg REAL,
        waist_cm REAL,
        body_fat_pct REAL,
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS daily_checkins (
        date TEXT PRIMARY KEY,
        soreness REAL,
        motivation REAL,
        stress REAL,
        sleep_quality REAL,
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS recommendations (
        date TEXT PRIMARY KEY,
        workout_id TEXT,
        readiness REAL,
        readiness_level TEXT,
        confidence TEXT,
        payload_json TEXT NOT NULL,
        generated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS reports (
        period TEXT NOT NULL,
        period_key TEXT NOT NULL,
        generated_at TEXT NOT NULL,
        path TEXT NOT NULL,
        PRIMARY KEY (period, period_key)
    )
    """,
]


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def init_db(conn: sqlite3.Connection) -> None:
    """Create tables and ensure schema version matches. Recreates on mismatch."""
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    cur.execute("SELECT value FROM _meta WHERE key = 'schema_version'")
    row = cur.fetchone()
    current = int(row[0]) if row else None

    if current is not None and current != SCHEMA_VERSION:
        log.warning(
            "Schema version mismatch (db=%s, code=%s); recreating processed tables.",
            current,
            SCHEMA_VERSION,
        )
        for tbl in (
            "wellness_daily",
            "activities",
            "activity_intervals",
            "manual_body_metrics",
            "daily_checkins",
            "recommendations",
            "reports",
        ):
            cur.execute(f"DROP TABLE IF EXISTS {tbl}")

    for stmt in SCHEMA_STATEMENTS:
        cur.execute(stmt)

    cur.execute(
        "INSERT OR REPLACE INTO _meta(key, value) VALUES (?, ?)",
        ("schema_version", str(SCHEMA_VERSION)),
    )
    conn.commit()


# ---------- meta helpers ----------


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    cur = conn.execute("SELECT value FROM _meta WHERE key = ?", (key,))
    row = cur.fetchone()
    return row[0] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO _meta(key, value) VALUES (?, ?)",
        (key, value),
    )


# ---------- upserts ----------


_WELLNESS_COLS = (
    "date", "hrv", "hrv_sdnn", "rhr", "avg_sleeping_hr",
    "garmin_sleep_score", "sleep_quality_score",
    "sleep_duration_min", "sleep_latency_min",
    "respiration", "spo2",
    "soreness", "fatigue", "motivation", "mood", "stress",
    "weight_kg", "body_fat_pct", "waist_cm",
    "bp_systolic", "bp_diastolic",
    "vo2max", "steps", "garmin_readiness",
    "illness", "injury",
    "ctl", "atl", "ctl_load", "atl_load", "ramp_rate",
    "comments", "source_updated_at", "raw_json",
)


def upsert_wellness(conn: sqlite3.Connection, record: dict[str, Any]) -> None:
    payload: dict[str, Any] = {c: record.get(c) for c in _WELLNESS_COLS}
    # Coerce sleep duration: spec stores in minutes; raw API may have seconds.
    if payload.get("sleep_duration_min") is None and record.get("sleep_duration_sec"):
        payload["sleep_duration_min"] = float(record["sleep_duration_sec"]) / 60.0
    if "raw_json" not in record:
        payload["raw_json"] = json.dumps(record, default=str)
    if payload.get("date") is None:
        raise ValueError("wellness record missing 'date'")
    cols = ", ".join(_WELLNESS_COLS)
    placeholders = ", ".join(["?"] * len(_WELLNESS_COLS))
    conn.execute(
        f"INSERT OR REPLACE INTO wellness_daily ({cols}) VALUES ({placeholders})",
        tuple(payload[c] for c in _WELLNESS_COLS),
    )


_ACTIVITY_COLS = (
    "id", "date", "sport", "type", "name", "description",
    "duration_min", "elapsed_min", "distance_m", "load", "intensity",
    "efficiency_factor", "variability_index", "decoupling", "polarization_index",
    "avg_hr", "max_hr", "avg_power", "avg_cadence", "avg_speed", "max_speed",
    "elevation_gain_m", "calories", "kg_lifted",
    "feel", "perceived_exertion", "session_rpe",
    "ftp", "lthr", "trimp", "hr_load", "pace_load", "power_load",
    "source_updated_at", "is_hard", "raw_json",
)


def upsert_activity(conn: sqlite3.Connection, record: dict[str, Any]) -> None:
    payload: dict[str, Any] = {c: record.get(c) for c in _ACTIVITY_COLS}
    if payload.get("duration_min") is None and record.get("duration_sec"):
        payload["duration_min"] = float(record["duration_sec"]) / 60.0
    if payload.get("elapsed_min") is None and record.get("elapsed_sec"):
        payload["elapsed_min"] = float(record["elapsed_sec"]) / 60.0
    if "raw_json" not in record:
        payload["raw_json"] = json.dumps(record, default=str)
    if payload.get("id") is None or payload.get("date") is None:
        raise ValueError("activity record missing 'id' or 'date'")
    if payload.get("is_hard") is None:
        payload["is_hard"] = 0
    cols = ", ".join(_ACTIVITY_COLS)
    placeholders = ", ".join(["?"] * len(_ACTIVITY_COLS))
    conn.execute(
        f"INSERT OR REPLACE INTO activities ({cols}) VALUES ({placeholders})",
        tuple(payload[c] for c in _ACTIVITY_COLS),
    )


def replace_activity_intervals(
    conn: sqlite3.Connection, activity_id: str, intervals: Iterable[dict[str, Any]]
) -> None:
    conn.execute("DELETE FROM activity_intervals WHERE activity_id = ?", (activity_id,))
    rows = []
    for idx, rec in enumerate(intervals):
        rows.append(
            (
                activity_id,
                idx,
                rec.get("type"),
                rec.get("duration_s"),
                rec.get("avg_hr"),
                rec.get("avg_power"),
                rec.get("intensity"),
                rec.get("raw_json") or json.dumps(rec, default=str),
            )
        )
    if rows:
        conn.executemany(
            "INSERT INTO activity_intervals "
            "(activity_id, idx, type, duration_s, avg_hr, avg_power, intensity, raw_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )


def upsert_recommendation(
    conn: sqlite3.Connection,
    date: str,
    workout_id: str | None,
    readiness: float,
    readiness_level: str,
    confidence: str,
    payload: dict[str, Any],
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO recommendations
        (date, workout_id, readiness, readiness_level, confidence, payload_json, generated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            date,
            workout_id,
            readiness,
            readiness_level,
            confidence,
            json.dumps(payload, default=str),
            _now_iso(),
        ),
    )


def record_report(conn: sqlite3.Connection, period: str, period_key: str, path: Path) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO reports (period, period_key, generated_at, path)
        VALUES (?, ?, ?, ?)""",
        (period, period_key, _now_iso(), str(path)),
    )


def import_manual_csv(conn: sqlite3.Connection, manual_dir: Path) -> dict[str, int]:
    """Read body_metrics.csv and daily_checkins.csv if present; upsert rows."""
    counts: dict[str, int] = {"body_metrics": 0, "checkins": 0}

    bm = manual_dir / "body_metrics.csv"
    if bm.exists():
        df = pd.read_csv(bm)
        for _, row in df.iterrows():
            conn.execute(
                """INSERT OR REPLACE INTO manual_body_metrics
                (date, weight_kg, waist_cm, body_fat_pct, notes) VALUES (?, ?, ?, ?, ?)""",
                (
                    str(row.get("date")),
                    _maybe_float(row.get("weight_kg")),
                    _maybe_float(row.get("waist_cm")),
                    _maybe_float(row.get("body_fat_pct")),
                    _maybe_str(row.get("notes")),
                ),
            )
            counts["body_metrics"] += 1

    ck = manual_dir / "daily_checkins.csv"
    if ck.exists():
        df = pd.read_csv(ck)
        for _, row in df.iterrows():
            conn.execute(
                """INSERT OR REPLACE INTO daily_checkins
                (date, soreness, motivation, stress, sleep_quality, notes)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    str(row.get("date")),
                    _maybe_float(row.get("soreness")),
                    _maybe_float(row.get("motivation")),
                    _maybe_float(row.get("stress")),
                    _maybe_float(row.get("sleep_quality")),
                    _maybe_str(row.get("notes")),
                ),
            )
            counts["checkins"] += 1

    return counts


def _maybe_float(v: Any) -> float | None:
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _maybe_str(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    return str(v)


# ---------- read helpers (pandas) ----------


def read_wellness(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql("SELECT * FROM wellness_daily ORDER BY date", conn)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def read_activities(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql("SELECT * FROM activities ORDER BY date", conn)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def read_checkins(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql("SELECT * FROM daily_checkins ORDER BY date", conn)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def read_body_metrics(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql("SELECT * FROM manual_body_metrics ORDER BY date", conn)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def read_recommendations(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql("SELECT * FROM recommendations ORDER BY date", conn)
    return df


def read_reports(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql("SELECT * FROM reports ORDER BY generated_at DESC", conn)
