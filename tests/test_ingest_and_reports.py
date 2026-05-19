"""Ingest (mocked httpx) + daily/weekly/monthly report generation."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import respx

from health_manager.config import (
    Settings,
    load_goals,
    load_metric_mapping,
    load_scoring,
)
from health_manager.ingest import sync as run_sync
from health_manager.intervals_client import IntervalsClient
from health_manager.recommender import recommend
from health_manager.reports import write_daily, write_monthly, write_weekly
from health_manager.scoring import compute_confidence, compute_readiness
from health_manager.storage import (
    connect,
    read_activities,
    read_checkins,
    read_wellness,
)
from health_manager.workout_library import load_workouts


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


def test_sync_writes_raw_and_sqlite(tmp_path, project_root, fixtures_dir):
    settings = _settings(tmp_path, project_root)
    mapping = load_metric_mapping(settings.metric_mapping_path)

    wellness = json.loads((fixtures_dir / "wellness_sample.json").read_text(encoding="utf-8"))
    activities = json.loads((fixtures_dir / "activities_sample.json").read_text(encoding="utf-8"))
    detail = json.loads((fixtures_dir / "activity_detail_a3.json").read_text(encoding="utf-8"))

    with respx.mock(base_url="https://intervals.icu", assert_all_called=False) as mock:
        mock.get("/api/v1/athlete/0/wellness").mock(return_value=httpx.Response(200, json=wellness))
        mock.get("/api/v1/athlete/0/activities").mock(return_value=httpx.Response(200, json=activities))
        # Any activity detail returns the same payload.
        mock.get(url__regex=r"/api/v1/activity/.*").mock(
            return_value=httpx.Response(200, json=detail)
        )
        client = IntervalsClient(api_key="dummy", athlete_id="0")
        report = run_sync(settings, mapping, days=30, today=date(2026, 5, 19), client=client)

    assert report.wellness_count == len(wellness)
    assert report.activity_count == len(activities)

    raw_dir = settings.raw_dir / "intervals" / "wellness"
    assert any(raw_dir.iterdir()), "raw wellness JSON not written"

    with connect(settings.sqlite_path) as conn:
        df = read_wellness(conn)
        assert len(df) == len(wellness)
        adf = read_activities(conn)
        assert len(adf) == len(activities)
        # The intensity-92 activity should be flagged hard.
        assert int(adf[adf["id"] == "a3"].iloc[0]["is_hard"]) == 1


def test_daily_report_round_trip(tmp_path, project_root, fixtures_dir):
    settings = _settings(tmp_path, project_root)
    mapping = load_metric_mapping(settings.metric_mapping_path)

    wellness = json.loads((fixtures_dir / "wellness_sample.json").read_text(encoding="utf-8"))
    activities = json.loads((fixtures_dir / "activities_sample.json").read_text(encoding="utf-8"))
    detail = json.loads((fixtures_dir / "activity_detail_a3.json").read_text(encoding="utf-8"))

    with respx.mock(base_url="https://intervals.icu", assert_all_called=False) as mock:
        mock.get("/api/v1/athlete/0/wellness").mock(return_value=httpx.Response(200, json=wellness))
        mock.get("/api/v1/athlete/0/activities").mock(return_value=httpx.Response(200, json=activities))
        mock.get(url__regex=r"/api/v1/activity/.*").mock(
            return_value=httpx.Response(200, json=detail)
        )
        client = IntervalsClient(api_key="dummy")
        run_sync(settings, mapping, days=30, today=date(2026, 5, 19), client=client)

    goals = load_goals(settings.goals_path)
    scoring = load_scoring(settings.scoring_path)
    workouts = load_workouts(settings.workouts_dir)

    with connect(settings.sqlite_path) as conn:
        wdf = read_wellness(conn)
        adf = read_activities(conn)
        cdf = read_checkins(conn)

    target = date(2026, 5, 19)
    r = compute_readiness(target, wdf, adf, cdf, goals, scoring)
    conf = compute_confidence(target, wdf, adf, goals, scoring, r.hrv_stat, r.rhr_stat)
    rec = recommend(target, workouts, r, conf, goals, adf, wdf)

    md_path, json_path = write_daily(settings, rec)
    assert md_path.exists() and json_path.exists()
    md = md_path.read_text(encoding="utf-8")
    assert "Daily report" in md
    assert "Recommendation" in md
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["date"] == "2026-05-19"
    assert "readiness" in payload
    assert "recommendation" in payload


def test_weekly_and_monthly_reports(tmp_path, project_root, fixtures_dir):
    settings = _settings(tmp_path, project_root)
    mapping = load_metric_mapping(settings.metric_mapping_path)

    wellness = json.loads((fixtures_dir / "wellness_sample.json").read_text(encoding="utf-8"))
    activities = json.loads((fixtures_dir / "activities_sample.json").read_text(encoding="utf-8"))
    detail = json.loads((fixtures_dir / "activity_detail_a3.json").read_text(encoding="utf-8"))

    with respx.mock(base_url="https://intervals.icu", assert_all_called=False) as mock:
        mock.get("/api/v1/athlete/0/wellness").mock(return_value=httpx.Response(200, json=wellness))
        mock.get("/api/v1/athlete/0/activities").mock(return_value=httpx.Response(200, json=activities))
        mock.get(url__regex=r"/api/v1/activity/.*").mock(
            return_value=httpx.Response(200, json=detail)
        )
        client = IntervalsClient(api_key="dummy")
        run_sync(settings, mapping, days=30, today=date(2026, 5, 19), client=client)

    goals = load_goals(settings.goals_path)

    wmd, wjs = write_weekly(settings, goals, date(2026, 5, 19))
    assert wmd.exists() and wjs.exists()
    weekly_payload = json.loads(wjs.read_text(encoding="utf-8"))
    assert weekly_payload["period"] == "weekly"

    mmd, mjs = write_monthly(settings, goals, date(2026, 5, 19))
    assert mmd.exists() and mjs.exists()
    monthly_payload = json.loads(mjs.read_text(encoding="utf-8"))
    assert monthly_payload["period"] == "monthly"
