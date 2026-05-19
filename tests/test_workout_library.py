import pytest

from health_manager.workout_library import (
    load_workouts,
    parse_strength_body,
)


def test_load_workouts_loads_all_samples(sample_workouts_dir):
    workouts = load_workouts(sample_workouts_dir)
    ids = {w.id for w in workouts}
    # Spec: at least one workout per sport, plus we shipped 2-3 each.
    sports = {w.sport for w in workouts}
    assert {"run", "bike", "strength", "recovery"}.issubset(sports)
    assert "run.easy_z2_45" in ids
    assert "strength.full_body_a" in ids


def test_strength_body_parser_splits_sections():
    body = """## Warmup
- Bike 5 min @ easy

## Main
- Back squat 4x5 @ RPE 7-8
- Romanian deadlift 3x8 @ RPE 7; keep neutral spine
- Bench press 4x5 @ RPE 7-8

## Cooldown
- Couch stretch 2x60s each side
"""
    block = parse_strength_body(body)
    assert len(block.warmup) == 1
    assert len(block.main) == 3
    assert len(block.cooldown) == 1

    squat = block.main[0]
    assert squat.name.lower().startswith("back squat")
    assert squat.sets == 4
    assert squat.reps.strip() == "5"
    assert "RPE 7-8" in (squat.load or "")

    rdl = block.main[1]
    assert rdl.notes is not None and "neutral spine" in rdl.notes


def test_duplicate_ids_error(tmp_path):
    base = tmp_path / "workouts" / "run"
    base.mkdir(parents=True)
    body = """---
id: run.dup
name: A
sport: run
category: easy
duration_min: 30
estimated_load: 30
recovery_cost: 1
intensity: 1
---

- 30m Z2"""
    (base / "a.md").write_text(body, encoding="utf-8")
    (base / "b.md").write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate workout id"):
        load_workouts(tmp_path / "workouts")
