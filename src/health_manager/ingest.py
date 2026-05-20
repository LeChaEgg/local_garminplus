"""Sync raw data from Intervals.icu into raw JSON files + SQLite.

`sync(...)` is the public entry point used by `health sync` and (lazily) by
`health today`. The function is deterministic given a `today` date and the
contents of the API; we expose `today` as a parameter for testability.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, timedelta
from pathlib import Path
from typing import Any

from .config import (
    MetricMapping,
    Settings,
    canonical_sport,
    load_sport_aliases,
    normalize_record,
)
from .intervals_client import IntervalsClient
from .storage import (
    connect,
    import_manual_csv,
    init_db,
    replace_activity_intervals,
    set_meta,
    upsert_activity,
    upsert_wellness,
)

log = logging.getLogger(__name__)

HARD_INTENSITY_THRESHOLD = 80.0   # Intervals.icu icu_intensity ~ % FTP/threshold proxy
DETAIL_LOOKBACK_DAYS = 14


@dataclass
class SyncReport:
    wellness_count: int = 0
    activity_count: int = 0
    detail_count: int = 0
    detail_cached: int = 0
    unknown_wellness_fields: set[str] = field(default_factory=set)
    unknown_activity_fields: set[str] = field(default_factory=set)


def sync(
    settings: Settings,
    mapping: MetricMapping,
    days: int = 180,
    today: date | None = None,
    client: IntervalsClient | None = None,
    force_details: bool = False,
) -> SyncReport:
    """Fetch the last `days` of data into raw/ and SQLite.

    By default, activity detail (intervals) is only fetched for activities
    that have *no* cached `data/raw/intervals/activity_details/<id>.json`.
    Pass `force_details=True` to re-fetch every recent activity's intervals
    (useful if Intervals.icu's analysis was re-run on an activity).
    """
    today = today or date.today()
    oldest = today - timedelta(days=days)

    sport_aliases = load_sport_aliases(settings.sport_aliases_path)

    owned_client = False
    if client is None:
        client = IntervalsClient(
            api_key=settings.intervals_api_key,
            athlete_id=settings.intervals_athlete_id,
        )
        owned_client = True

    report = SyncReport()
    try:
        wellness = client.get_wellness(oldest, today)
        activities = client.get_activities(oldest, today)

        _write_raw(settings.raw_dir / "intervals" / "wellness", wellness, key_field="id_or_date")
        _write_raw(settings.raw_dir / "intervals" / "activities", activities, key_field="id")

        with connect(settings.sqlite_path) as conn:
            init_db(conn)
            _backfill_sport_canonical(conn, sport_aliases)

            for raw in wellness:
                normalized = normalize_record(raw, mapping.wellness)
                unknown = set(normalized) - _KNOWN_WELLNESS
                if unknown:
                    report.unknown_wellness_fields |= unknown
                normalized["raw_json"] = json.dumps(raw, default=str)
                if "date" not in normalized:
                    # Some wellness records use id == date
                    normalized["date"] = raw.get("id")
                if normalized.get("date"):
                    upsert_wellness(conn, normalized)
                    report.wellness_count += 1
                else:
                    log.warning("wellness record missing date; skipped: %s", raw)

            recent_threshold = today - timedelta(days=DETAIL_LOOKBACK_DAYS)
            for raw in activities:
                normalized = normalize_record(raw, mapping.activity)
                unknown = set(normalized) - _KNOWN_ACTIVITY
                if unknown:
                    report.unknown_activity_fields |= unknown
                normalized["raw_json"] = json.dumps(raw, default=str)
                date_str = _coerce_date(normalized.get("date"))
                normalized["date"] = date_str
                if not normalized.get("id") or not date_str:
                    log.warning("activity missing id/date; skipped: %s", raw)
                    continue
                # Derive duration_min from seconds if needed.
                if normalized.get("duration_min") is None and normalized.get("duration_sec"):
                    normalized["duration_min"] = float(normalized["duration_sec"]) / 60.0
                if normalized.get("elapsed_min") is None and normalized.get("elapsed_sec"):
                    normalized["elapsed_min"] = float(normalized["elapsed_sec"]) / 60.0
                normalized["sport_canonical"] = canonical_sport(
                    normalized.get("sport") or normalized.get("type"),
                    sport_aliases,
                )
                normalized["is_hard"] = int(_is_hard(normalized))
                upsert_activity(conn, normalized)
                report.activity_count += 1

                # Pull intervals for recent activities, but skip if we already have
                # the detail cached on disk (unless force_details is set).
                try:
                    dt = date.fromisoformat(date_str)
                except ValueError:
                    dt = None
                if dt and dt >= recent_threshold:
                    detail_dir = settings.raw_dir / "intervals" / "activity_details"
                    cached_path = detail_dir / f"{normalized['id']}.json"
                    if cached_path.exists() and not force_details:
                        report.detail_cached += 1
                    else:
                        try:
                            detail = client.get_activity(
                                str(normalized["id"]), include_intervals=True
                            )
                        except Exception as e:  # pragma: no cover - network failures
                            log.warning(
                                "activity detail fetch failed for %s: %s",
                                normalized["id"],
                                e,
                            )
                            continue
                        _write_raw(detail_dir, [detail], key_field="id")
                        intervals = (
                            detail.get("icu_intervals")
                            or detail.get("intervals")
                            or []
                        )
                        normalized_intervals = [
                            _normalize_interval(iv, mapping.interval) for iv in intervals
                        ]
                        replace_activity_intervals(
                            conn, str(normalized["id"]), normalized_intervals
                        )
                        report.detail_count += 1

            # Pull in manual CSVs (no-op if files absent).
            import_manual_csv(conn, settings.manual_dir)

            set_meta(conn, "last_sync_utc", _utc_now_iso())
            set_meta(conn, "last_sync_days", str(days))
    finally:
        if owned_client:
            client.close()

    if report.unknown_wellness_fields:
        log.info("Unknown wellness fields (kept in raw_json): %s",
                 sorted(report.unknown_wellness_fields))
    if report.unknown_activity_fields:
        log.info("Unknown activity fields (kept in raw_json): %s",
                 sorted(report.unknown_activity_fields))
    return report


# ---------- helpers ----------


_KNOWN_WELLNESS = {
    "date", "hrv", "hrv_sdnn", "rhr", "avg_sleeping_hr",
    "garmin_sleep_score", "sleep_quality_score",
    "sleep_duration_min", "sleep_duration_sec", "sleep_latency_min",
    "respiration", "spo2",
    "soreness", "fatigue", "motivation", "mood", "stress",
    "weight_kg", "body_fat_pct", "waist_cm",
    "bp_systolic", "bp_diastolic",
    "vo2max", "steps", "garmin_readiness",
    "illness", "injury",
    "ctl", "atl", "ctl_load", "atl_load", "ramp_rate",
    "comments", "source_updated_at",
    "raw_json",
}

_KNOWN_ACTIVITY = {
    "id", "date", "date_utc", "sport", "sport_canonical", "type", "name", "description",
    "duration_min", "duration_sec", "elapsed_min", "elapsed_sec",
    "distance_m", "load", "intensity",
    "efficiency_factor", "variability_index", "decoupling", "polarization_index",
    "avg_hr", "max_hr", "avg_power", "avg_cadence", "avg_speed", "max_speed",
    "elevation_gain_m", "calories", "kg_lifted",
    "feel", "perceived_exertion", "session_rpe",
    "ftp", "lthr", "trimp", "hr_load", "pace_load", "power_load",
    "source_updated_at", "is_hard", "raw_json",
}


def _normalize_interval(iv: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    out = normalize_record(iv, mapping)
    if out.get("duration_s") is None and out.get("duration"):
        with contextlib.suppress(TypeError, ValueError):
            out["duration_s"] = float(out["duration"])
    out["raw_json"] = json.dumps(iv, default=str)
    return out


def _is_hard(activity: dict[str, Any]) -> bool:
    intensity = activity.get("intensity")
    try:
        if intensity is not None and float(intensity) >= HARD_INTENSITY_THRESHOLD:
            return True
    except (TypeError, ValueError):
        pass
    load = activity.get("load")
    try:
        if load is not None and float(load) >= 80.0:
            return True
    except (TypeError, ValueError):
        pass
    return False


def _coerce_date(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value)
    return s[:10] if len(s) >= 10 else None


def _write_raw(dir_path: Path, items: Iterable[dict[str, Any]], key_field: str) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    for item in items:
        if key_field == "id_or_date":
            key = item.get("id") or item.get("date")
        else:
            key = item.get(key_field)
        if key is None:
            continue
        safe = str(key).replace("/", "_").replace(":", "_")
        with (dir_path / f"{safe}.json").open("w", encoding="utf-8") as f:
            json.dump(item, f, ensure_ascii=False, indent=2, default=str)


def _utc_now_iso() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat()


def _backfill_sport_canonical(conn, aliases: dict[str, str]) -> int:
    """Fill `sport_canonical` for rows where it's NULL or empty.

    Idempotent — only touches rows that need it. Returns count updated.
    """
    cur = conn.execute(
        "SELECT id, sport, type FROM activities "
        "WHERE sport_canonical IS NULL OR sport_canonical = ''"
    )
    rows = cur.fetchall()
    if not rows:
        return 0
    updates = [
        (canonical_sport(row["sport"] or row["type"], aliases), row["id"])
        for row in rows
    ]
    conn.executemany(
        "UPDATE activities SET sport_canonical = ? WHERE id = ?", updates
    )
    log.info("Backfilled sport_canonical for %d activity rows.", len(updates))
    return len(updates)
