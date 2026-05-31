"""Readiness scoring. Pure rule-based, fully transparent.

All weights and thresholds come from `ScoringConfig`. The output includes a
breakdown per component and a list of human-readable reasons so the daily
report and dashboard can explain *why* the score came out the way it did.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from .config import Goals, ScoringConfig
from .metrics import (
    BaselineStat,
    acwr,
    baseline_stability,
    consecutive_training_days,
    days_since_last_hard,
    hrv_baseline,
    latest_checkin,
    required_input_presence,
    rhr_baseline,
)


@dataclass
class SubScores:
    sleep_duration: float
    garmin_sleep_score: float
    hrv_vs_baseline: float
    rhr_vs_baseline: float
    subjective: float

    def weighted(self, w) -> float:
        return (
            w.sleep_duration * self.sleep_duration
            + w.garmin_sleep_score * self.garmin_sleep_score
            + w.hrv_vs_baseline * self.hrv_vs_baseline
            + w.rhr_vs_baseline * self.rhr_vs_baseline
            + w.subjective * self.subjective
        )


@dataclass
class LoadBalance:
    acwr_fit: float
    days_since_hard: float
    consecutive_days: float
    soreness: float
    raw_acwr: float | None
    raw_days_since_hard: int | None
    raw_consecutive_days: int

    def weighted(self, w) -> float:
        return (
            w.acwr_fit * self.acwr_fit
            + w.days_since_hard * self.days_since_hard
            + w.consecutive_days * self.consecutive_days
            + w.soreness * self.soreness
        )


@dataclass
class Readiness:
    score: float
    level: str
    sleep_recovery_score: float
    load_balance_score: float
    risk_penalty: float
    sub_scores: SubScores
    load_balance: LoadBalance
    reasons: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    hrv_stat: BaselineStat | None = None
    rhr_stat: BaselineStat | None = None


@dataclass
class Confidence:
    level: str            # high | medium | low
    score: float          # 0..1
    present: dict[str, bool]
    baseline_stability: dict[str, float]


# ---------- helpers ----------


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _lerp(x: float, x0: float, x1: float, y0: float, y1: float) -> float:
    if x1 == x0:
        return y0
    return y0 + (y1 - y0) * ((x - x0) / (x1 - x0))


def sleep_duration_score(hours: float | None, cfg) -> float:
    """Triangular: 0 at min/max, 100 across [ideal_low, ideal_high]."""
    if hours is None or math.isnan(hours):
        return 50.0
    if hours <= cfg.min or hours >= cfg.max:
        return 0.0
    if hours < cfg.ideal_low:
        return _clamp(_lerp(hours, cfg.min, cfg.ideal_low, 0, 100), 0, 100)
    if hours <= cfg.ideal_high:
        return 100.0
    return _clamp(_lerp(hours, cfg.ideal_high, cfg.max, 100, 0), 0, 100)


def _z_to_score(z: float | None, clamp: list[float]) -> float:
    if z is None:
        return 50.0
    lo, hi = clamp
    return _clamp(_lerp(_clamp(z, lo, hi), lo, hi, 0, 100), 0, 100)


def _z_to_score_inverted(z: float | None, clamp: list[float]) -> float:
    """Lower z is better (RHR)."""
    if z is None:
        return 50.0
    lo, hi = clamp
    # Map z=hi -> 0, z=lo -> 100
    return _clamp(_lerp(_clamp(z, lo, hi), lo, hi, 100, 0), 0, 100)


def _garmin_sleep_normalized(value: float | None, clamp: list[float]) -> float:
    if value is None:
        return 50.0
    lo, hi = clamp
    return _clamp(_lerp(_clamp(value, lo, hi), lo, hi, 0, 100), 0, 100)


def _subjective_score(checkin: dict[str, float | None]) -> float:
    """Combine motivation (high good), sleep_quality (high good), soreness (low good), stress (low good).
    Each component is on a 1..5 scale; we map to 0..100 and average those present."""
    parts: list[float] = []
    if checkin.get("motivation") is not None:
        parts.append(_clamp(_lerp(checkin["motivation"], 1, 5, 0, 100), 0, 100))
    if checkin.get("sleep_quality") is not None:
        parts.append(_clamp(_lerp(checkin["sleep_quality"], 1, 5, 0, 100), 0, 100))
    if checkin.get("soreness") is not None:
        parts.append(_clamp(_lerp(checkin["soreness"], 1, 5, 100, 0), 0, 100))
    if checkin.get("stress") is not None:
        parts.append(_clamp(_lerp(checkin["stress"], 1, 5, 100, 0), 0, 100))
    return sum(parts) / len(parts) if parts else 50.0


def _acwr_fit_score(ratio: float | None, ideal: list[float]) -> float:
    if ratio is None:
        return 60.0   # neutral when we have no chronic load yet
    lo, hi = ideal
    if lo <= ratio <= hi:
        return 100.0
    # Penalize linearly outside the range, clamp at 0 when far away.
    if ratio < lo:
        return _clamp(_lerp(ratio, 0.0, lo, 0, 100), 0, 100)
    return _clamp(_lerp(ratio, hi, hi + 0.7, 100, 0), 0, 100)


def _days_since_hard_score(days: int | None, target_gap: int) -> float:
    if days is None:
        return 80.0   # never had a hard session -> probably fresh
    if days >= target_gap:
        return 100.0
    # 0 days -> 30, 1 day -> 60, target -> 100
    return _clamp(_lerp(days, 0, target_gap, 30, 100), 0, 100)


def _consecutive_days_score(days: int, max_days: int) -> float:
    if days <= 1:
        return 100.0
    if days >= max_days:
        return 0.0
    return _clamp(_lerp(days, 1, max_days, 100, 0), 0, 100)


def _soreness_score(soreness: float | None) -> float:
    if soreness is None:
        return 75.0
    return _clamp(_lerp(soreness, 1, 5, 100, 0), 0, 100)


def _level_for(score: float, levels) -> str:
    if score >= levels.green:
        return "green"
    if score >= levels.green_yellow:
        return "green_yellow"
    if score >= levels.yellow:
        return "yellow"
    if score >= levels.orange:
        return "orange"
    return "red"


# ---------- main entry points ----------


def compute_readiness(
    as_of: date,
    wellness: pd.DataFrame,
    activities: pd.DataFrame,
    checkins: pd.DataFrame,
    goals: Goals,
    scoring: ScoringConfig,
) -> Readiness:
    sr = scoring.sleep_recovery
    lb = scoring.load_balance

    hrv_stat = hrv_baseline(wellness, as_of, goals.sleep_recovery.hrv_baseline_window_days)
    rhr_stat = rhr_baseline(wellness, as_of, goals.sleep_recovery.rhr_baseline_window_days)

    # Wellness "today" (the row dated as_of, if any).
    today_row = None
    if not wellness.empty:
        rows = wellness[wellness["date"] == as_of]
        if not rows.empty:
            today_row = rows.iloc[0]

    sleep_min = float(today_row["sleep_duration_min"]) if today_row is not None and pd.notna(today_row.get("sleep_duration_min")) else None
    sleep_hours = sleep_min / 60.0 if sleep_min is not None else None
    garmin_sleep = float(today_row["garmin_sleep_score"]) if today_row is not None and pd.notna(today_row.get("garmin_sleep_score")) else None

    checkin = latest_checkin(checkins, as_of)
    # Wellness has its own soreness/motivation; merge into checkin as fallback.
    if today_row is not None:
        for k in ("soreness", "motivation", "stress"):
            if checkin.get(k) is None and pd.notna(today_row.get(k)):
                checkin[k] = float(today_row[k])

    sub = SubScores(
        sleep_duration=sleep_duration_score(sleep_hours, sr.sleep_duration_hours),
        garmin_sleep_score=_garmin_sleep_normalized(garmin_sleep, sr.garmin_sleep_score_clamp),
        hrv_vs_baseline=_z_to_score(hrv_stat.z, sr.hrv_z_clamp),
        rhr_vs_baseline=_z_to_score_inverted(rhr_stat.z, sr.rhr_z_clamp_inverted),
        subjective=_subjective_score(checkin),
    )
    sleep_recovery_score = sub.weighted(sr.weights)

    ratio = acwr(activities, as_of)
    dsh = days_since_last_hard(activities, as_of)
    consec = consecutive_training_days(activities, as_of)
    load = LoadBalance(
        acwr_fit=_acwr_fit_score(ratio, lb.acwr_ideal_range),
        days_since_hard=_days_since_hard_score(dsh, lb.hard_session_target_gap_days),
        consecutive_days=_consecutive_days_score(consec, goals.guardrails.max_consecutive_training_days),
        soreness=_soreness_score(checkin.get("soreness")),
        raw_acwr=ratio,
        raw_days_since_hard=dsh,
        raw_consecutive_days=consec,
    )
    load_balance_score = load.weighted(lb.weights)

    # Risk penalties
    rp = scoring.risk_penalties
    risk_penalty = 0.0
    risk_flags: list[str] = []
    if sleep_hours is not None and sleep_hours < rp.short_sleep_hours_below:
        risk_penalty += rp.short_sleep_penalty
        risk_flags.append(f"short sleep <{rp.short_sleep_hours_below}h ({sleep_hours:.1f}h)")
    if today_row is not None and bool(today_row.get("illness")):
        risk_penalty += rp.illness_flag_penalty
        risk_flags.append("illness flag set")
    if checkin.get("soreness") is not None and checkin["soreness"] >= rp.high_soreness_threshold:
        risk_penalty += rp.high_soreness_penalty
        risk_flags.append(f"high soreness ({checkin['soreness']:.0f}/5)")

    raw_score = (
        scoring.readiness.sleep_recovery_weight * sleep_recovery_score
        + scoring.readiness.load_balance_weight * load_balance_score
        - risk_penalty
    )
    score = _clamp(raw_score, 0.0, 100.0)
    level = _level_for(score, scoring.levels)

    reasons: list[str] = []
    if sleep_hours is not None:
        reasons.append(f"sleep duration {sleep_hours:.1f}h -> {sub.sleep_duration:.0f}/100")
    if garmin_sleep is not None:
        reasons.append(f"source sleep score {garmin_sleep:.0f} -> {sub.garmin_sleep_score:.0f}/100")
    if hrv_stat.z is not None:
        reasons.append(
            f"HRV {hrv_stat.latest:.1f} vs baseline {hrv_stat.mean:.1f} (z={hrv_stat.z:+.2f}) "
            f"-> {sub.hrv_vs_baseline:.0f}/100"
        )
    elif hrv_stat.latest is not None:
        reasons.append(f"HRV {hrv_stat.latest:.1f} (no baseline yet)")
    if rhr_stat.z is not None:
        reasons.append(
            f"RHR {rhr_stat.latest:.1f} vs baseline {rhr_stat.mean:.1f} (z={rhr_stat.z:+.2f}) "
            f"-> {sub.rhr_vs_baseline:.0f}/100"
        )
    if ratio is not None:
        reasons.append(f"ACWR {ratio:.2f} -> {load.acwr_fit:.0f}/100")
    if dsh is not None:
        reasons.append(f"{dsh}d since last hard session -> {load.days_since_hard:.0f}/100")
    reasons.append(f"{consec} consecutive training day(s) -> {load.consecutive_days:.0f}/100")

    return Readiness(
        score=score,
        level=level,
        sleep_recovery_score=sleep_recovery_score,
        load_balance_score=load_balance_score,
        risk_penalty=risk_penalty,
        sub_scores=sub,
        load_balance=load,
        reasons=reasons,
        risk_flags=risk_flags,
        hrv_stat=hrv_stat,
        rhr_stat=rhr_stat,
    )


def compute_confidence(
    as_of: date,
    wellness: pd.DataFrame,
    activities: pd.DataFrame,
    goals: Goals,
    scoring: ScoringConfig,
    hrv_stat: BaselineStat,
    rhr_stat: BaselineStat,
) -> Confidence:
    today_row = None
    if not wellness.empty:
        rows = wellness[wellness["date"] == as_of]
        if not rows.empty:
            today_row = rows.iloc[0]

    has_recent_activities = False
    if not activities.empty:
        # Within the last 14 days
        from datetime import timedelta as _td

        recent = activities[activities["date"] >= as_of - _td(days=14)]
        has_recent_activities = not recent.empty

    present = required_input_presence(today_row, has_recent_activities, scoring.confidence.required_inputs)
    n_present = sum(1 for v in present.values() if v)
    coverage = n_present / max(1, len(present))

    bs = {
        "hrv": baseline_stability(
            hrv_stat,
            scoring.confidence.baseline_window_target_days,
            scoring.confidence.baseline_window_floor_days,
        ),
        "rhr": baseline_stability(
            rhr_stat,
            scoring.confidence.baseline_window_target_days,
            scoring.confidence.baseline_window_floor_days,
        ),
    }
    baseline_score = (bs["hrv"] + bs["rhr"]) / 2.0

    # Weighted combination: coverage matters more than baselines.
    combined = 0.6 * coverage + 0.4 * baseline_score
    lvl = "low"
    if combined >= scoring.confidence.levels.high:
        lvl = "high"
    elif combined >= scoring.confidence.levels.medium:
        lvl = "medium"

    return Confidence(level=lvl, score=combined, present=present, baseline_stability=bs)
