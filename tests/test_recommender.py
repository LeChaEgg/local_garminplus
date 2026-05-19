from datetime import date, timedelta

import pandas as pd

from health_manager.config import Goals, ScoringConfig
from health_manager.recommender import recommend
from health_manager.scoring import compute_confidence, compute_readiness
from health_manager.workout_library import load_workouts


def _wellness_df(today: date, **overrides):
    rows = []
    for i in range(28, 0, -1):
        d = today - timedelta(days=i - 1)
        rows.append(
            {
                "date": d,
                "hrv": 60.0,
                "rhr": 50.0,
                "garmin_sleep_score": overrides.get("sleep_score", 85.0),
                "sleep_duration_min": overrides.get("sleep_min", 480.0),
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


def _activities_df(today: date, last_hard_days_ago: int = 4):
    rows = [
        {
            "id": "a1",
            "date": today - timedelta(days=last_hard_days_ago),
            "sport": "Run",
            "type": "Run",
            "name": "Threshold",
            "duration_min": 60.0,
            "elapsed_min": 65.0,
            "distance_m": 9000.0,
            "load": 100.0,
            "intensity": 92.0,
            "avg_hr": 168.0,
            "max_hr": 180.0,
            "avg_power": None,
            "calories": 600.0,
            "is_hard": 1,
            "raw_json": "{}",
        },
        {
            "id": "a2",
            "date": today - timedelta(days=2),
            "sport": "Run",
            "type": "Run",
            "name": "Easy",
            "duration_min": 45.0,
            "elapsed_min": 47.0,
            "distance_m": 6000.0,
            "load": 45.0,
            "intensity": 65.0,
            "avg_hr": 138.0,
            "max_hr": 150.0,
            "avg_power": None,
            "calories": 400.0,
            "is_hard": 0,
            "raw_json": "{}",
        },
    ]
    return pd.DataFrame(rows)


def test_low_readiness_forces_recovery(sample_workouts_dir):
    today = date(2026, 5, 19)
    wellness = _wellness_df(today, sleep_min=4 * 60, sleep_score=40)
    activities = _activities_df(today, last_hard_days_ago=1)
    goals = Goals()
    scoring = ScoringConfig()
    workouts = load_workouts(sample_workouts_dir)

    r = compute_readiness(today, wellness, activities, pd.DataFrame(), goals, scoring)
    conf = compute_confidence(today, wellness, activities, goals, scoring, r.hrv_stat, r.rhr_stat)
    rec = recommend(today, workouts, r, conf, goals, activities, wellness)

    # Either the main is recovery, or every hard workout is rejected for the right reason.
    if rec.main and rec.main.sport == "recovery":
        assert True
    else:
        hard_rejections = [
            nr for nr in rec.not_recommended if any("hard" in reason for reason in nr.reasons)
        ]
        assert hard_rejections, "low readiness should reject hard workouts"


def test_high_readiness_picks_a_workout(sample_workouts_dir):
    today = date(2026, 5, 19)
    wellness = _wellness_df(today)
    activities = _activities_df(today, last_hard_days_ago=5)
    goals = Goals()
    scoring = ScoringConfig()
    workouts = load_workouts(sample_workouts_dir)

    r = compute_readiness(today, wellness, activities, pd.DataFrame(), goals, scoring)
    conf = compute_confidence(today, wellness, activities, goals, scoring, r.hrv_stat, r.rhr_stat)
    rec = recommend(today, workouts, r, conf, goals, activities, wellness)

    assert rec.main is not None
    assert rec.confidence.level in {"high", "medium", "low"}


def test_hard_session_spacing_blocks_back_to_back_hard(sample_workouts_dir):
    today = date(2026, 5, 19)
    wellness = _wellness_df(today)
    activities = _activities_df(today, last_hard_days_ago=1)   # too recent
    goals = Goals()
    scoring = ScoringConfig()
    workouts = load_workouts(sample_workouts_dir)

    r = compute_readiness(today, wellness, activities, pd.DataFrame(), goals, scoring)
    conf = compute_confidence(today, wellness, activities, goals, scoring, r.hrv_stat, r.rhr_stat)
    rec = recommend(today, workouts, r, conf, goals, activities, wellness)

    threshold = next(
        (nr for nr in rec.not_recommended if nr.workout_id == "run.threshold_4x8"),
        None,
    )
    assert threshold is not None, "expected threshold workout to be blocked"
    assert any("spacing" in reason or "hard" in reason for reason in threshold.reasons)


def test_ranking_is_deterministic(sample_workouts_dir):
    today = date(2026, 5, 19)
    wellness = _wellness_df(today)
    activities = _activities_df(today, last_hard_days_ago=4)
    goals = Goals()
    scoring = ScoringConfig()
    workouts = load_workouts(sample_workouts_dir)

    r = compute_readiness(today, wellness, activities, pd.DataFrame(), goals, scoring)
    conf = compute_confidence(today, wellness, activities, goals, scoring, r.hrv_stat, r.rhr_stat)
    rec_a = recommend(today, workouts, r, conf, goals, activities, wellness)
    rec_b = recommend(today, workouts, r, conf, goals, activities, wellness)
    assert rec_a.main and rec_b.main
    assert rec_a.main.workout_id == rec_b.main.workout_id
