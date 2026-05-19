from health_manager.config import (
    load_goals,
    load_metric_mapping,
    load_scoring,
    normalize_record,
)


def test_load_goals_from_sample(sample_config_dir):
    goals = load_goals(sample_config_dir / "goals.md")
    assert goals.profile.timezone == "Asia/Tokyo"
    assert goals.aerobic.weekly_z2_minutes_min >= 100
    assert goals.guardrails.max_consecutive_training_days == 6
    assert "Stay healthy" in goals.narrative_notes


def test_load_scoring_from_sample(sample_config_dir):
    scoring = load_scoring(sample_config_dir / "scoring.yml")
    w = scoring.sleep_recovery.weights
    assert abs(
        w.sleep_duration + w.garmin_sleep_score + w.hrv_vs_baseline
        + w.rhr_vs_baseline + w.subjective - 1.0
    ) < 1e-9
    assert scoring.levels.green == 80


def test_metric_mapping_normalizes_known_fields(sample_config_dir):
    mapping = load_metric_mapping(sample_config_dir / "metric_mapping.yml")
    raw = {"hrv": 60, "restingHR": 50, "sleepScore": 82, "weird_field": 1}
    out = normalize_record(raw, mapping.wellness)
    assert out["hrv"] == 60
    assert out["rhr"] == 50
    assert out["garmin_sleep_score"] == 82
    # Unknown fields are preserved under their original name.
    assert out["weird_field"] == 1
