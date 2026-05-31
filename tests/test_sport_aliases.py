from health_manager.config import (
    canonical_sport,
    load_sport_aliases,
)


def test_canonical_sport_with_loaded_yaml(sample_config_dir):
    aliases = load_sport_aliases(sample_config_dir / "sport_aliases.yml")
    # Intervals.icu raw labels
    assert canonical_sport("Run", aliases) == "run"
    assert canonical_sport("Ride", aliases) == "bike"
    assert canonical_sport("EBikeRide", aliases) == "bike"
    assert canonical_sport("WeightTraining", aliases) == "strength"
    assert canonical_sport("RockClimbing", aliases) == "rockclimbing"
    # Apple Watch style labels
    assert canonical_sport("Running", aliases) == "run"
    assert canonical_sport("Outdoor Run", aliases) == "run"
    assert canonical_sport("Cycling", aliases) == "bike"
    assert canonical_sport("Indoor Cycle", aliases) == "bike"
    assert canonical_sport("Traditional Strength Training", aliases) == "strength"
    assert canonical_sport("Open Water Swimming", aliases) == "swim"
    # Case-insensitive
    assert canonical_sport("run", aliases) == "run"
    assert canonical_sport("RIDE", aliases) == "bike"


def test_canonical_sport_unknown_falls_back():
    aliases = load_sport_aliases(None)   # built-in defaults
    assert canonical_sport("SkyDiving", aliases) == "other"
    assert canonical_sport(None, aliases) == "other"
    assert canonical_sport("", aliases) == "other"


def test_load_sport_aliases_returns_built_in_defaults_when_missing(tmp_path):
    aliases = load_sport_aliases(tmp_path / "does_not_exist.yml")
    # Should still recognize the Intervals.icu names from the built-in default.
    assert canonical_sport("Run", aliases) == "run"
    assert canonical_sport("Outdoor Run", aliases) == "run"
    assert canonical_sport("WeightTraining", aliases) == "strength"
    assert canonical_sport("Traditional Strength Training", aliases) == "strength"
