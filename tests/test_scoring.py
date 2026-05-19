from datetime import date, timedelta

import pandas as pd

from health_manager.config import Goals, ScoringConfig
from health_manager.scoring import (
    compute_confidence,
    compute_readiness,
    sleep_duration_score,
)


def _wellness_df(today: date, base_hrv=60, base_rhr=50, sleep_min=480, sleep_score=85, n=30):
    rows = []
    for i in range(n, 0, -1):
        d = today - timedelta(days=i - 1)
        rows.append(
            {
                "date": d,
                "hrv": float(base_hrv + (i % 5) - 2),
                "rhr": float(base_rhr + ((i + 1) % 4) - 1),
                "garmin_sleep_score": float(sleep_score),
                "sleep_duration_min": float(sleep_min),
                "sleep_latency_min": 10.0,
                "soreness": 2.0,
                "fatigue": 2.0,
                "motivation": 4.0,
                "stress": 2.0,
                "weight_kg": 72.0,
                "illness": 0,
                "injury": 0,
                "ctl": 50.0,
                "atl": 45.0,
                "ramp_rate": 0.0,
                "raw_json": "{}",
            }
        )
    return pd.DataFrame(rows)


def _activities_df(today: date):
    rows = []
    for offset, kind, load, intensity in [
        (10, "Run", 50, 70),
        (8, "Run", 60, 75),
        (6, "WeightTraining", 45, 70),
        (4, "Run", 90, 90),     # hard
        (2, "Run", 40, 65),
    ]:
        rows.append(
            {
                "id": f"a{offset}",
                "date": today - timedelta(days=offset),
                "sport": kind,
                "type": kind,
                "name": kind,
                "duration_min": 45.0,
                "elapsed_min": 50.0,
                "distance_m": 8000.0,
                "load": float(load),
                "intensity": float(intensity),
                "avg_hr": 150.0,
                "max_hr": 175.0,
                "avg_power": None,
                "calories": 400.0,
                "is_hard": 1 if intensity >= 80 else 0,
                "raw_json": "{}",
            }
        )
    return pd.DataFrame(rows)


def test_sleep_duration_triangular():
    assert sleep_duration_score(8.0, _slp_cfg()) == 100.0
    assert sleep_duration_score(7.0, _slp_cfg()) == 100.0
    assert sleep_duration_score(4.0, _slp_cfg()) == 0.0
    assert 0 < sleep_duration_score(5.5, _slp_cfg()) < 100


def _slp_cfg():
    return ScoringConfig().sleep_recovery.sleep_duration_hours


def test_compute_readiness_green_for_healthy_user():
    today = date(2026, 5, 19)
    wellness = _wellness_df(today)
    # Override today's row to a clearly good day: HRV up vs baseline, RHR down.
    mask = wellness["date"] == today
    wellness.loc[mask, "hrv"] = 65.0
    wellness.loc[mask, "rhr"] = 48.0
    wellness.loc[mask, "sleep_duration_min"] = 8.0 * 60
    wellness.loc[mask, "garmin_sleep_score"] = 92.0
    activities = _activities_df(today)
    checkins = pd.DataFrame(columns=["date", "soreness", "motivation", "stress", "sleep_quality"])
    goals = Goals()
    scoring = ScoringConfig()

    r = compute_readiness(today, wellness, activities, checkins, goals, scoring)
    assert 65 <= r.score <= 100
    assert r.level in {"green", "green_yellow"}
    assert r.hrv_stat.n > 0


def test_short_sleep_triggers_risk_penalty_and_lower_score():
    today = date(2026, 5, 19)
    wellness = _wellness_df(today)
    # Override last night to 4h
    wellness.loc[wellness["date"] == today, "sleep_duration_min"] = 4 * 60
    activities = _activities_df(today)
    checkins = pd.DataFrame(columns=["date", "soreness", "motivation", "stress", "sleep_quality"])

    r = compute_readiness(today, wellness, activities, checkins, Goals(), ScoringConfig())
    assert any("short sleep" in flag for flag in r.risk_flags)
    assert r.risk_penalty > 0


def test_confidence_reflects_missing_inputs():
    today = date(2026, 5, 19)
    wellness = _wellness_df(today)
    activities = _activities_df(today)
    goals = Goals()
    scoring = ScoringConfig()
    r = compute_readiness(today, wellness, activities, pd.DataFrame(), goals, scoring)
    full_conf = compute_confidence(today, wellness, activities, goals, scoring, r.hrv_stat, r.rhr_stat)
    assert full_conf.level in {"high", "medium"}

    # Drop all wellness today
    wellness2 = wellness[wellness["date"] != today].copy()
    r2 = compute_readiness(today, wellness2, activities, pd.DataFrame(), goals, scoring)
    low = compute_confidence(today, wellness2, activities, goals, scoring, r2.hrv_stat, r2.rhr_stat)
    assert low.score < full_conf.score
