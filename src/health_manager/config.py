"""Configuration models and loaders.

`Settings` discovers the project root and resolves paths to config/, data/,
workouts/, and reports/ subdirectories. Goals live in `config/goals.md`
(YAML front matter + Markdown body); scoring weights in `config/scoring.yml`;
the Intervals.icu field-name mapping in `config/metric_mapping.yml`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import frontmatter
import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger(__name__)


# ---------- goals.md models ----------


class Profile(BaseModel):
    name: str = "athlete"
    timezone: str = "UTC"
    sex: str | None = None
    height_cm: float | None = None
    birth_year: int | None = None


class GoalWeights(BaseModel):
    sleep_recovery: float = 0.30
    aerobic: float = 0.30
    strength: float = 0.25
    body_shape: float = 0.15


class SleepRecoveryGoals(BaseModel):
    sleep_duration_range_h: list[float] = Field(default_factory=lambda: [7.0, 8.5])
    garmin_sleep_score_min: int = 75
    hrv_baseline_window_days: int = 28
    rhr_baseline_window_days: int = 28
    sleep_latency_target_min: int = 20


class AerobicGoals(BaseModel):
    weekly_minutes_min: int = 180
    weekly_z2_minutes_min: int = 120
    weekly_hard_sessions_max: int = 2
    long_run_every_n_days: int = 7
    include_cycling: bool = True


class StrengthGoals(BaseModel):
    weekly_sessions_min: int = 2
    movement_patterns: list[str] = Field(
        default_factory=lambda: ["squat", "hinge", "push", "pull", "carry", "core"]
    )
    progressive_overload: bool = True


class BodyShapeGoals(BaseModel):
    weight_trend: str = "maintain"   # maintain | lose | gain
    waist_cm_target: float | None = None
    monthly_photo_check: bool = False
    subjective_score_min: int = 6


class Guardrails(BaseModel):
    never_hard_training_if_readiness_below: int = 60
    avoid_hard_training_after_bad_sleep: bool = True
    max_consecutive_training_days: int = 6


class Goals(BaseModel):
    profile: Profile = Field(default_factory=Profile)
    goal_weights: GoalWeights = Field(default_factory=GoalWeights)
    sleep_recovery: SleepRecoveryGoals = Field(default_factory=SleepRecoveryGoals)
    aerobic: AerobicGoals = Field(default_factory=AerobicGoals)
    strength: StrengthGoals = Field(default_factory=StrengthGoals)
    body_shape: BodyShapeGoals = Field(default_factory=BodyShapeGoals)
    guardrails: Guardrails = Field(default_factory=Guardrails)
    narrative_notes: str = ""

    model_config = ConfigDict(extra="ignore")


# ---------- scoring.yml models ----------


class SleepRecoveryWeights(BaseModel):
    sleep_duration: float = 0.30
    garmin_sleep_score: float = 0.25
    hrv_vs_baseline: float = 0.20
    rhr_vs_baseline: float = 0.15
    subjective: float = 0.10


class SleepDurationHours(BaseModel):
    min: float = 4.0
    ideal_low: float = 7.0
    ideal_high: float = 8.5
    max: float = 10.0


class SleepRecoveryScoring(BaseModel):
    weights: SleepRecoveryWeights = Field(default_factory=SleepRecoveryWeights)
    sleep_duration_hours: SleepDurationHours = Field(default_factory=SleepDurationHours)
    hrv_z_clamp: list[float] = Field(default_factory=lambda: [-1.5, 1.5])
    rhr_z_clamp_inverted: list[float] = Field(default_factory=lambda: [-1.5, 1.5])
    garmin_sleep_score_clamp: list[float] = Field(default_factory=lambda: [40.0, 100.0])


class LoadBalanceWeights(BaseModel):
    acwr_fit: float = 0.35
    days_since_hard: float = 0.25
    consecutive_days: float = 0.20
    soreness: float = 0.20


class LoadBalanceScoring(BaseModel):
    weights: LoadBalanceWeights = Field(default_factory=LoadBalanceWeights)
    acwr_ideal_range: list[float] = Field(default_factory=lambda: [0.8, 1.3])
    hard_session_target_gap_days: int = 2


class ReadinessCombine(BaseModel):
    sleep_recovery_weight: float = 0.65
    load_balance_weight: float = 0.35


class LevelThresholds(BaseModel):
    green: int = 80
    green_yellow: int = 65
    yellow: int = 50
    orange: int = 35


class RiskPenalties(BaseModel):
    short_sleep_hours_below: float = 5.0
    short_sleep_penalty: int = 15
    illness_flag_penalty: int = 25
    high_soreness_threshold: int = 4
    high_soreness_penalty: int = 10


class ConfidenceLevels(BaseModel):
    high: float = 0.85
    medium: float = 0.55


class ConfidenceScoring(BaseModel):
    required_inputs: list[str] = Field(
        default_factory=lambda: [
            "hrv",
            "sleep_duration_min",
            "garmin_sleep_score",
            "rhr",
            "recent_activities",
        ]
    )
    baseline_window_target_days: int = 28
    baseline_window_floor_days: int = 7
    levels: ConfidenceLevels = Field(default_factory=ConfidenceLevels)


class ScoringConfig(BaseModel):
    sleep_recovery: SleepRecoveryScoring = Field(default_factory=SleepRecoveryScoring)
    load_balance: LoadBalanceScoring = Field(default_factory=LoadBalanceScoring)
    readiness: ReadinessCombine = Field(default_factory=ReadinessCombine)
    levels: LevelThresholds = Field(default_factory=LevelThresholds)
    risk_penalties: RiskPenalties = Field(default_factory=RiskPenalties)
    confidence: ConfidenceScoring = Field(default_factory=ConfidenceScoring)

    model_config = ConfigDict(extra="ignore")


# ---------- metric mapping ----------


class MetricMapping(BaseModel):
    wellness: dict[str, str] = Field(default_factory=dict)
    activity: dict[str, str] = Field(default_factory=dict)
    interval: dict[str, str] = Field(default_factory=dict)


# ---------- Settings (paths + env) ----------


class Settings(BaseModel):
    project_root: Path
    config_dir: Path
    data_dir: Path
    raw_dir: Path
    processed_dir: Path
    manual_dir: Path
    reports_dir: Path
    workouts_dir: Path

    intervals_api_key: str = ""
    intervals_athlete_id: str = "0"
    intervals_auth_method: str = "basic"
    local_timezone: str = "UTC"

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @property
    def sqlite_path(self) -> Path:
        return self.processed_dir / "health.sqlite"

    @property
    def goals_path(self) -> Path:
        return self.config_dir / "goals.md"

    @property
    def scoring_path(self) -> Path:
        return self.config_dir / "scoring.yml"

    @property
    def metric_mapping_path(self) -> Path:
        return self.config_dir / "metric_mapping.yml"

    @property
    def sport_aliases_path(self) -> Path:
        return self.config_dir / "sport_aliases.yml"


def load_settings(project_root: Path | None = None) -> Settings:
    """Build a Settings object. Loads .env if present."""
    root = Path(project_root or os.environ.get("HEALTH_MANAGER_ROOT") or Path.cwd()).resolve()
    load_dotenv(root / ".env", override=False)

    s = Settings(
        project_root=root,
        config_dir=root / "config",
        data_dir=root / "data",
        raw_dir=root / "data" / "raw",
        processed_dir=root / "data" / "processed",
        manual_dir=root / "data" / "manual",
        reports_dir=root / "data" / "reports",
        workouts_dir=root / "workouts",
        intervals_api_key=os.environ.get("INTERVALS_API_KEY", ""),
        intervals_athlete_id=os.environ.get("INTERVALS_ATHLETE_ID", "0"),
        intervals_auth_method=os.environ.get("INTERVALS_AUTH_METHOD", "basic"),
        local_timezone=os.environ.get("LOCAL_TIMEZONE", "UTC"),
    )
    return s


def load_goals(path: Path) -> Goals:
    """Parse goals.md (YAML front matter + Markdown body) into a Goals model."""
    with path.open("r", encoding="utf-8") as f:
        post = frontmatter.load(f)
    meta: dict[str, Any] = dict(post.metadata) if post.metadata else {}
    meta.setdefault("narrative_notes", post.content.strip())
    return Goals.model_validate(meta)


def load_scoring(path: Path) -> ScoringConfig:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return ScoringConfig.model_validate(data)


def load_metric_mapping(path: Path) -> MetricMapping:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return MetricMapping.model_validate(data)


# ---------- sport aliases ----------


CANONICAL_SPORTS = {
    "run", "bike", "walk", "hike", "swim", "strength", "recovery",
    "rockclimbing", "other",
}


def load_sport_aliases(path: Path | None) -> dict[str, str]:
    """Load sport_aliases.yml into a lowercase {alias: canonical} mapping.

    Missing file → returns a small built-in default keyed off Intervals.icu
    labels so the tool still works without the config.
    """
    if path is None or not path.exists():
        raw: dict[str, list[str]] = _DEFAULT_SPORT_ALIASES
    else:
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    out: dict[str, str] = {}
    for canonical, aliases in raw.items():
        canonical_l = canonical.strip().lower()
        if canonical_l not in CANONICAL_SPORTS:
            log.info("sport_aliases: unrecognized canonical '%s'", canonical)
        out[canonical_l] = canonical_l
        for alias in aliases or []:
            out[str(alias).strip().lower()] = canonical_l
    return out


def canonical_sport(raw_value: str | None, aliases: dict[str, str]) -> str:
    """Map a raw activity sport/type string to a canonical label.

    Unknown values are returned as 'other' (so they show up but don't crash
    sport-keyed lookups). Pass an empty/None value → 'other' too.
    """
    if not raw_value:
        return "other"
    key = str(raw_value).strip().lower()
    return aliases.get(key, "other")


_DEFAULT_SPORT_ALIASES: dict[str, list[str]] = {
    "run": ["Run", "Running", "VirtualRun", "TrailRun", "TreadmillRun"],
    "bike": [
        "Ride", "Cycling", "VirtualRide", "EBikeRide", "GravelRide",
        "MountainBikeRide", "IndoorRide", "Handcycle",
    ],
    "walk": ["Walk", "Walking", "Treadmill_Walk"],
    "hike": ["Hike", "Hiking"],
    "swim": ["Swim", "Swimming", "OpenWaterSwim", "PoolSwim"],
    "strength": [
        "WeightTraining", "Strength", "StrengthTraining", "Workout",
        "HIIT", "Crossfit",
    ],
    "recovery": [
        "Yoga", "Stretching", "Pilates", "BreathWork", "Meditation", "Mobility",
    ],
    "rockclimbing": [
        "RockClimbing", "Climbing", "Bouldering", "IndoorClimbing",
    ],
}


def normalize_record(record: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    """Apply a {raw_field: canonical_field} mapping to a single record.

    Unknown keys are kept under their original name; callers log them separately.
    """
    out: dict[str, Any] = {}
    for k, v in record.items():
        canonical = mapping.get(k, k)
        # Last writer wins when multiple raw keys map to the same canonical name.
        if v is not None or canonical not in out:
            out[canonical] = v
    return out
