"""Pick today's workout from the local library, given readiness and goals.

The recommender is a pure function over its inputs. It returns the main pick,
optional conservative and progressive alternatives, the list of workouts it
considered (with reasons they were rejected), and the confidence reported by
scoring.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from .config import Goals
from .metrics import (
    days_since_last_hard,
    weekly_hard_sessions,
    weekly_minutes_by_sport,
    weekly_strength_sessions,
    weekly_z2_minutes,
)
from .scoring import Confidence, Readiness
from .workout_library import Workout

# ---------- output dataclasses ----------


@dataclass
class WorkoutPick:
    workout_id: str
    name: str
    sport: str
    category: str
    duration_min: int
    rank_score: float
    components: dict[str, float]


@dataclass
class RejectedWorkout:
    workout_id: str
    name: str
    sport: str
    reasons: list[str]


@dataclass
class Recommendation:
    date: date
    main: WorkoutPick | None
    conservative: WorkoutPick | None
    progressive: WorkoutPick | None
    not_recommended: list[RejectedWorkout]
    readiness: Readiness
    confidence: Confidence
    weekly_state: dict[str, float] = field(default_factory=dict)


# ---------- public entry point ----------


def recommend(
    today: date,
    workouts: Sequence[Workout],
    readiness: Readiness,
    confidence: Confidence,
    goals: Goals,
    activities: pd.DataFrame,
    wellness: pd.DataFrame,
) -> Recommendation:
    # Defensive: tolerate None/empty inputs from callers.
    if activities is None:
        activities = pd.DataFrame()
    if wellness is None:
        wellness = pd.DataFrame()

    # Aggregate weekly state once. Each helper is itself empty-safe.
    minutes_by_sport = weekly_minutes_by_sport(activities, today)
    z2_min = weekly_z2_minutes(activities, today)
    hard_count = weekly_hard_sessions(activities, today)
    strength_count = weekly_strength_sessions(activities, today)
    dsh = days_since_last_hard(activities, today)
    consecutive = readiness.load_balance.raw_consecutive_days

    weekly_state = {
        "minutes_by_sport": minutes_by_sport,
        "weekly_z2_minutes": z2_min,
        "weekly_hard_sessions": hard_count,
        "weekly_strength_sessions": strength_count,
        "days_since_last_hard": dsh,
        "consecutive_training_days": consecutive,
    }

    # Last-night sleep hours (used by guardrail).
    sleep_hours = _last_night_sleep_hours(wellness, today)

    accepted: list[tuple[Workout, dict[str, float], float]] = []
    rejected: list[RejectedWorkout] = []
    guardrails = goals.guardrails

    for wk in workouts:
        reasons = _hard_filter_reasons(
            wk,
            readiness=readiness,
            goals=goals,
            sleep_hours=sleep_hours,
            consecutive=consecutive,
            dsh=dsh,
            weekly_hard=hard_count,
        )
        if reasons:
            rejected.append(
                RejectedWorkout(
                    workout_id=wk.id,
                    name=wk.meta.name,
                    sport=wk.sport,
                    reasons=reasons,
                )
            )
            continue
        components = _rank_components(
            wk,
            readiness=readiness,
            weekly_state=weekly_state,
            goals=goals,
        )
        rank = (
            0.40 * components["goal_gap_match"]
            + 0.25 * components["readiness_fit"]
            + 0.15 * components["weekly_balance"]
            + 0.10 * components["variety"]
            + 0.10 * components["time_fit"]
            - components["risk_penalty"]
        )
        accepted.append((wk, components, rank))

    accepted.sort(key=lambda t: t[2], reverse=True)

    main_pick = _to_pick(accepted[0]) if accepted else None
    conservative = _select_conservative(accepted, main_pick)
    progressive = _select_progressive(accepted, main_pick, readiness)

    # If readiness is so low that only recovery should run, force the first recovery option as main.
    if (
        main_pick is None
        or readiness.score < guardrails.never_hard_training_if_readiness_below
    ):
        recovery_pick = _first_of_sport(accepted, "recovery")
        if recovery_pick is not None and (main_pick is None or main_pick.sport != "recovery"):
            main_pick = recovery_pick

    return Recommendation(
        date=today,
        main=main_pick,
        conservative=conservative,
        progressive=progressive,
        not_recommended=rejected,
        readiness=readiness,
        confidence=confidence,
        weekly_state=weekly_state,
    )


# ---------- filters ----------


def _hard_filter_reasons(
    wk: Workout,
    readiness: Readiness,
    goals: Goals,
    sleep_hours: float | None,
    consecutive: int,
    dsh: int | None,
    weekly_hard: int,
) -> list[str]:
    reasons: list[str] = []
    gd = goals.guardrails

    if wk.sport == "bike" and not goals.aerobic.include_cycling:
        reasons.append("cycling disabled in goals")

    if wk.meta.min_readiness > readiness.score:
        reasons.append(f"min_readiness {wk.meta.min_readiness} > readiness {readiness.score:.0f}")

    # Hard sessions blocked when readiness too low or sleep too short.
    is_hard_workout = wk.meta.intensity >= 4
    if is_hard_workout:
        if readiness.score < gd.never_hard_training_if_readiness_below:
            reasons.append(
                f"hard workout blocked: readiness {readiness.score:.0f} < {gd.never_hard_training_if_readiness_below}"
            )
        if sleep_hours is not None and sleep_hours < 5.0:
            reasons.append(f"hard workout blocked: last sleep {sleep_hours:.1f}h < 5.0h")
        if dsh is not None and dsh < 2 and wk.sport in ("run", "bike"):
            reasons.append(f"hard run/bike spacing: only {dsh}d since last hard session")
        if weekly_hard >= goals.aerobic.weekly_hard_sessions_max:
            reasons.append(
                f"weekly hard cap reached: {weekly_hard} >= {goals.aerobic.weekly_hard_sessions_max}"
            )

    if consecutive >= gd.max_consecutive_training_days and wk.sport != "recovery":
        reasons.append(
            f"consecutive training days {consecutive} >= cap {gd.max_consecutive_training_days}"
        )

    return reasons


# ---------- rank components ----------


def _rank_components(
    wk: Workout,
    readiness: Readiness,
    weekly_state: dict,
    goals: Goals,
) -> dict[str, float]:
    components = {
        "goal_gap_match": _goal_gap_match(wk, weekly_state, goals),
        "readiness_fit": _readiness_fit(wk, readiness),
        "weekly_balance": _weekly_balance(wk, weekly_state, goals),
        "variety": _variety(wk, weekly_state),
        "time_fit": _time_fit(wk),
        "risk_penalty": _risk_penalty(wk, readiness),
    }
    return components


def _goal_gap_match(wk: Workout, ws: dict, goals: Goals) -> float:
    """Higher when this workout helps close a weekly goal gap."""
    score = 0.0

    if wk.sport == "strength":
        gap = max(0, goals.strength.weekly_sessions_min - ws["weekly_strength_sessions"])
        score = min(1.0, gap / max(1, goals.strength.weekly_sessions_min))
        return score

    if wk.sport in ("run", "bike", "walk", "hike"):
        z2_gap = max(0.0, goals.aerobic.weekly_z2_minutes_min - ws["weekly_z2_minutes"])
        if wk.meta.target_z2_minutes > 0 and z2_gap > 0:
            score = min(1.0, wk.meta.target_z2_minutes / max(15.0, z2_gap))
        total_mins = sum(ws["minutes_by_sport"].values()) if ws["minutes_by_sport"] else 0.0
        if total_mins < goals.aerobic.weekly_minutes_min:
            score = max(score, 0.5)
        # Hard sessions help only if we are under the hard cap.
        if wk.meta.intensity >= 4 and ws["weekly_hard_sessions"] < goals.aerobic.weekly_hard_sessions_max:
            score = max(score, 0.6)
        return score

    if wk.sport == "recovery":
        # Recovery valuable when load_balance is low or many consecutive days.
        consec = ws["consecutive_training_days"]
        return min(1.0, consec / max(1, goals.guardrails.max_consecutive_training_days))

    return 0.3


def _readiness_fit(wk: Workout, readiness: Readiness) -> float:
    """Best when workout intensity matches readiness level."""
    target = {
        "green": 4,
        "green_yellow": 3,
        "yellow": 2,
        "orange": 1,
        "red": 1,
    }[readiness.level]
    diff = abs(wk.meta.intensity - target)
    return max(0.0, 1.0 - 0.25 * diff)


def _weekly_balance(wk: Workout, ws: dict, goals: Goals) -> float:
    """Prefer workouts that fill an under-used sport this week."""
    minutes = ws["minutes_by_sport"]
    if not minutes:
        return 0.6
    total = sum(minutes.values())
    if total <= 0:
        return 0.6
    # weekly_minutes_by_sport now keys by canonical sport (run/bike/strength/recovery/...).
    sport_key = "recovery" if wk.sport == "recovery" else wk.sport
    used = minutes.get(sport_key, 0.0)
    ratio = used / total
    return max(0.0, 1.0 - ratio)


def _variety(wk: Workout, ws: dict) -> float:
    """Mild bonus for sports under-represented this week."""
    minutes = ws["minutes_by_sport"]
    if not minutes:
        return 0.5
    # weekly_minutes_by_sport now keys by canonical sport (run/bike/strength/recovery/...).
    sport_key = "recovery" if wk.sport == "recovery" else wk.sport
    if minutes.get(sport_key, 0.0) == 0:
        return 1.0
    return 0.4


def _time_fit(wk: Workout) -> float:
    """Prefer 30-75 min workouts. Tunable later."""
    d = wk.meta.duration_min
    if 30 <= d <= 75:
        return 1.0
    if d < 30:
        return max(0.4, d / 30.0)
    return max(0.4, 1.0 - (d - 75) / 120.0)


def _risk_penalty(wk: Workout, readiness: Readiness) -> float:
    penalty = 0.0
    if wk.meta.intensity >= 4 and readiness.level in ("yellow", "orange", "red"):
        penalty += 0.5
    if wk.meta.recovery_cost >= 4 and readiness.level == "yellow":
        penalty += 0.2
    return penalty


# ---------- helpers ----------


def _to_pick(entry: tuple[Workout, dict[str, float], float]) -> WorkoutPick:
    wk, components, rank = entry
    return WorkoutPick(
        workout_id=wk.id,
        name=wk.meta.name,
        sport=wk.sport,
        category=wk.meta.category,
        duration_min=wk.meta.duration_min,
        rank_score=rank,
        components=components,
    )


def _select_conservative(
    accepted: list[tuple[Workout, dict[str, float], float]],
    main: WorkoutPick | None,
) -> WorkoutPick | None:
    if main is None:
        return None
    # Find the main's intensity/recovery_cost to compare against.
    main_entry = next((e for e in accepted if e[0].id == main.workout_id), None)
    if main_entry is None:
        return None
    main_wk = main_entry[0]
    # Prefer something explicitly lighter and lower-intensity.
    for wk, components, rank in accepted:
        if wk.id == main.workout_id:
            continue
        if wk.meta.recovery_cost < main_wk.meta.recovery_cost and wk.meta.intensity <= 2:
            return _to_pick((wk, components, rank))
    for wk, components, rank in accepted:
        if wk.id == main.workout_id:
            continue
        if wk.meta.intensity < main_wk.meta.intensity:
            return _to_pick((wk, components, rank))
    return None


def _select_progressive(
    accepted: list[tuple[Workout, dict[str, float], float]],
    main: WorkoutPick | None,
    readiness: Readiness,
) -> WorkoutPick | None:
    if main is None or readiness.level != "green":
        return None
    for wk, components, rank in accepted:
        if wk.id == main.workout_id:
            continue
        if wk.meta.intensity > 3 or wk.meta.estimated_load > 80:
            return _to_pick((wk, components, rank))
    return None


def _first_of_sport(
    accepted: list[tuple[Workout, dict[str, float], float]], sport: str
) -> WorkoutPick | None:
    for entry in accepted:
        if entry[0].sport == sport:
            return _to_pick(entry)
    return None


def _last_night_sleep_hours(wellness: pd.DataFrame, today: date) -> float | None:
    if wellness.empty:
        return None
    row = wellness[wellness["date"] == today]
    if row.empty:
        return None
    val = row.iloc[0].get("sleep_duration_min")
    if val is None or pd.isna(val):
        return None
    return float(val) / 60.0
