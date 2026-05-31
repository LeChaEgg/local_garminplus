"""Smoke test for the static-HTML dashboard generator.

The dashboard is read-only over SQLite + report JSON, so the easiest way to
test it is: build the same Settings shape `cli.dashboard` uses, write a small
SQLite + a daily report payload to disk, then assert the generated HTML
contains the expected structural pieces.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from health_manager.config import Settings
from health_manager.dashboard import build_and_write_dashboard, build_dashboard_html
from health_manager.storage import (
    connect,
    init_db,
    set_meta,
    upsert_activity,
    upsert_wellness,
)


def _settings(tmp_path: Path, project_root: Path) -> Settings:
    return Settings(
        project_root=tmp_path,
        config_dir=project_root / "config",
        data_dir=tmp_path / "data",
        raw_dir=tmp_path / "data" / "raw",
        processed_dir=tmp_path / "data" / "processed",
        manual_dir=tmp_path / "data" / "manual",
        reports_dir=tmp_path / "data" / "reports",
        workouts_dir=project_root / "workouts",
        intervals_api_key="dummy",
        intervals_athlete_id="0",
    )


def _seed_db(settings: Settings) -> None:
    with connect(settings.sqlite_path) as conn:
        init_db(conn)
        set_meta(conn, "last_sync_utc", "2026-05-19T00:00:00+00:00")
        for i in range(14, 0, -1):
            upsert_wellness(
                conn,
                {
                    "date": (date(2026, 5, 19).replace(day=max(1, 19 - i))).isoformat(),
                    "hrv": 60.0 + (i % 5),
                    "rhr": 50.0,
                    "garmin_sleep_score": 85.0,
                    "sleep_duration_min": 480.0,
                    "raw_json": "{}",
                },
            )
        upsert_activity(
            conn,
            {
                "id": "a1",
                "date": "2026-05-18",
                "sport": "Run",
                "sport_canonical": "run",
                "type": "Run",
                "name": "Easy run",
                "duration_min": 45.0,
                "load": 45.0,
                "intensity": 65.0,
                "avg_hr": 138.0,
                "is_hard": 0,
                "raw_json": "{}",
            },
        )
        upsert_activity(
            conn,
            {
                "id": "a2",
                "date": "2026-05-19",
                "sport": "Ride",
                "sport_canonical": "bike",
                "type": "Ride",
                "name": "Morning ride",
                "duration_min": 62.0,
                "distance_m": 25100.0,
                "load": 70.0,
                "intensity": 72.0,
                "avg_hr": 132.0,
                "source_updated_at": "2026-05-19T01:30:00+00:00",
                "is_hard": 0,
                "raw_json": "{}",
            },
        )


def _seed_daily_report(settings: Settings) -> None:
    daily = settings.reports_dir / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": "2026-05-19",
        "readiness": {
            "score": 78.0,
            "level": "green_yellow",
            "sleep_recovery_score": 82.0,
            "load_balance_score": 70.0,
            "risk_penalty": 0,
            "reasons": ["sleep duration 8.0h -> 100/100", "HRV up"],
            "risk_flags": [],
        },
        "confidence": {"level": "high", "score": 0.95, "present": {}, "baseline_stability": {}},
        "recommendation": {
            "main": {
                "workout_id": "run.easy_z2_45",
                "name": "Easy Z2 run, 45 min",
                "sport": "run",
                "category": "easy",
                "duration_min": 45,
            },
            "conservative": None,
            "progressive": None,
            "not_recommended": [],
        },
        "weekly_state": {},
    }
    (daily / "2026-05-19.json").write_text(json.dumps(payload), encoding="utf-8")


def test_dashboard_builds_with_seeded_data(tmp_path, project_root):
    settings = _settings(tmp_path, project_root)
    _seed_db(settings)
    _seed_daily_report(settings)

    out = build_and_write_dashboard(settings)
    assert out.exists()
    text = out.read_text(encoding="utf-8")

    # Structural pieces.
    assert "<!doctype html>" in text
    assert "<title>Health Manager" in text
    assert 'class="donut"' in text
    # Readiness score and level rendered into the donut.
    assert "--score:78" in text
    assert "var(--green_yellow)" in text
    # Workout recommendation surfaces.
    assert "Easy Z2 run, 45 min" in text
    assert "🏃" in text or "&#x1f3c3;" in text   # run glyph
    # Goals section uses card layout, not a JSON dump.
    assert '<div class="goal-card">' in text
    assert "<pre>" not in text
    # Workout library is grouped.
    assert 'class="wk-group"' in text
    # Freshness and recent activity are visible.
    assert "Data freshness" in text
    assert "Latest Intervals.icu sync" in text
    assert "Latest synced activity" in text
    assert "Recent sports activities" in text
    assert "Morning ride" in text
    assert "Training load" in text
    assert "Average heart rate" in text
    # Common dashboard labels use plain-language names before abbreviations.
    assert "Heart rate variability (HRV)" in text
    assert "Resting heart rate" in text
    # Data quality coverage bars.
    assert 'class="coverage-bar"' in text
    # Dark mode CSS present.
    assert "prefers-color-scheme: dark" in text


def test_dashboard_renders_without_any_data(tmp_path, project_root):
    """Bare settings (no DB, no reports) should still produce a valid page with empty states."""
    settings = _settings(tmp_path, project_root)
    settings.processed_dir.mkdir(parents=True, exist_ok=True)
    out = build_and_write_dashboard(settings)
    text = out.read_text(encoding="utf-8")
    assert "<!doctype html>" in text
    assert "No daily report yet" in text
    assert "No data yet" in text


def test_dashboard_file_does_not_include_refresh_button(tmp_path, project_root):
    """The on-disk file:// version stays JS-free and has no refresh button."""
    settings = _settings(tmp_path, project_root)
    _seed_db(settings)
    out = build_and_write_dashboard(settings)
    text = out.read_text(encoding="utf-8")
    assert "id=\"refresh-btn\"" not in text
    assert "/api/refresh" not in text
    assert "<script>" not in text


def test_dashboard_served_html_includes_refresh_button(tmp_path, project_root):
    """The served (with_refresh=True) HTML has the button + the POST script."""
    settings = _settings(tmp_path, project_root)
    _seed_db(settings)
    served = build_dashboard_html(settings, with_refresh=True)
    assert 'id="refresh-btn"' in served
    assert "/api/refresh" in served
    assert "location.reload()" in served
