"""Pure analytical functions over wellness + activity DataFrames.

All functions accept a `as_of: date` so they are deterministic and reproducible
when re-running historical reports.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd


@dataclass
class BaselineStat:
    mean: float | None
    std: float | None
    latest: float | None
    z: float | None
    n: int

    @property
    def has_baseline(self) -> bool:
        return self.n >= 7 and self.std is not None and self.std > 0


def _window(df: pd.DataFrame, as_of: date, days: int, column: str) -> pd.Series:
    if df.empty or column not in df.columns:
        return pd.Series(dtype="float64")
    start = as_of - timedelta(days=days)
    mask = (df["date"] >= start) & (df["date"] < as_of)
    return pd.to_numeric(df.loc[mask, column], errors="coerce").dropna()


def baseline(df: pd.DataFrame, as_of: date, column: str, window_days: int) -> BaselineStat:
    """Mean/std over the last `window_days` days *before* as_of, and the value on as_of."""
    series = _window(df, as_of, window_days, column)
    n = int(series.shape[0])
    mean = float(series.mean()) if n else None
    std = float(series.std(ddof=0)) if n else None
    latest = None
    if not df.empty and column in df.columns:
        rows = df[df["date"] == as_of]
        if not rows.empty:
            val = pd.to_numeric(rows[column], errors="coerce").iloc[0]
            if pd.notna(val):
                latest = float(val)
    z = None
    if mean is not None and std is not None and std > 0 and latest is not None:
        z = (latest - mean) / std
    return BaselineStat(mean=mean, std=std, latest=latest, z=z, n=n)


def hrv_baseline(df: pd.DataFrame, as_of: date, window_days: int) -> BaselineStat:
    return baseline(df, as_of, "hrv", window_days)


def rhr_baseline(df: pd.DataFrame, as_of: date, window_days: int) -> BaselineStat:
    return baseline(df, as_of, "rhr", window_days)


def _load_window(activities: pd.DataFrame, as_of: date, days: int) -> float:
    if activities.empty or "load" not in activities.columns:
        return 0.0
    start = as_of - timedelta(days=days)
    mask = (activities["date"] >= start) & (activities["date"] < as_of)
    return float(pd.to_numeric(activities.loc[mask, "load"], errors="coerce").fillna(0).sum())


def acute_load(activities: pd.DataFrame, as_of: date, days: int = 7) -> float:
    return _load_window(activities, as_of, days)


def chronic_load(activities: pd.DataFrame, as_of: date, days: int = 28) -> float:
    return _load_window(activities, as_of, days) / max(1, days // 7)


def acwr(activities: pd.DataFrame, as_of: date) -> float | None:
    a = acute_load(activities, as_of, 7)
    c = chronic_load(activities, as_of, 28)
    if c <= 0:
        return None
    return a / c


def days_since_last_hard(activities: pd.DataFrame, as_of: date) -> int | None:
    if activities.empty or "is_hard" not in activities.columns:
        return None
    hard = activities[(activities["is_hard"] == 1) & (activities["date"] < as_of)]
    if hard.empty:
        return None
    last = hard["date"].max()
    return (as_of - last).days


def consecutive_training_days(activities: pd.DataFrame, as_of: date) -> int:
    """Counts unique training days ending the day before `as_of`."""
    if activities.empty:
        return 0
    dates = set(d for d in activities["date"] if d < as_of)
    count = 0
    cursor = as_of - timedelta(days=1)
    while cursor in dates:
        count += 1
        cursor -= timedelta(days=1)
    return count


def weekly_window_bounds(as_of: date) -> tuple[date, date]:
    """ISO week containing `as_of`. Returns [monday, sunday]."""
    monday = as_of - timedelta(days=as_of.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def weekly_minutes_by_sport(activities: pd.DataFrame, as_of: date) -> dict[str, float]:
    if activities.empty:
        return {}
    monday, sunday = weekly_window_bounds(as_of)
    mask = (activities["date"] >= monday) & (activities["date"] <= sunday)
    df = activities.loc[mask].copy()
    if df.empty:
        return {}
    df["duration_min"] = pd.to_numeric(df["duration_min"], errors="coerce").fillna(0)
    return df.groupby(df["sport"].fillna("unknown"))["duration_min"].sum().to_dict()


def weekly_z2_minutes(activities: pd.DataFrame, as_of: date) -> float:
    """Heuristic: count minutes from activities where intensity is missing or <70 as Z2."""
    if activities.empty:
        return 0.0
    monday, sunday = weekly_window_bounds(as_of)
    df = activities[(activities["date"] >= monday) & (activities["date"] <= sunday)].copy()
    if df.empty:
        return 0.0
    df["duration_min"] = pd.to_numeric(df["duration_min"], errors="coerce").fillna(0)
    df["intensity"] = pd.to_numeric(df["intensity"], errors="coerce")
    sport_ok = df["sport"].isin(["Run", "Ride", "Walk", "Hike", "VirtualRide"])
    z2_mask = (df["intensity"].isna() | (df["intensity"] < 70)) & sport_ok
    return float(df.loc[z2_mask, "duration_min"].sum())


def weekly_hard_sessions(activities: pd.DataFrame, as_of: date) -> int:
    if activities.empty or "is_hard" not in activities.columns:
        return 0
    monday, sunday = weekly_window_bounds(as_of)
    mask = (
        (activities["date"] >= monday)
        & (activities["date"] <= sunday)
        & (activities["is_hard"] == 1)
    )
    return int(mask.sum())


def weekly_strength_sessions(activities: pd.DataFrame, as_of: date) -> int:
    if activities.empty:
        return 0
    monday, sunday = weekly_window_bounds(as_of)
    df = activities[(activities["date"] >= monday) & (activities["date"] <= sunday)]
    if df.empty:
        return 0
    sport = df["sport"].fillna("").str.lower()
    typ = df["type"].fillna("").str.lower() if "type" in df.columns else pd.Series("", index=df.index)
    return int(((sport == "weighttraining") | sport.str.contains("strength")
                | typ.str.contains("strength")).sum())


def baseline_stability(stat: BaselineStat, target_days: int, floor_days: int) -> float:
    """Map number of samples in baseline to [0, 1]."""
    if stat.n >= target_days:
        return 1.0
    if stat.n <= floor_days:
        return max(0.0, stat.n / target_days)
    return stat.n / target_days


def latest_checkin(checkins: pd.DataFrame, as_of: date) -> dict[str, float | None]:
    if checkins.empty:
        return {}
    row = checkins[checkins["date"] == as_of]
    if row.empty:
        return {}
    out = {}
    for col in ("soreness", "motivation", "stress", "sleep_quality"):
        if col in row.columns:
            val = row.iloc[0][col]
            out[col] = float(val) if pd.notna(val) else None
    return out


def required_input_presence(
    wellness_today: pd.Series | None,
    has_recent_activities: bool,
    fields: Iterable[str],
) -> dict[str, bool]:
    present: dict[str, bool] = {}
    for f in fields:
        if f == "recent_activities":
            present[f] = has_recent_activities
        else:
            if wellness_today is None:
                present[f] = False
            else:
                val = wellness_today.get(f) if hasattr(wellness_today, "get") else None
                present[f] = val is not None and not (isinstance(val, float) and pd.isna(val))
    return present
