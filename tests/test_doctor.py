"""Smoke test for the `health doctor` checks.

We invoke the internal helper directly so we don't have to drive the full
Typer CLI in the test. The helper builds the standard list of (status, label,
detail) tuples — we assert key categories show up with the expected statuses.
"""

from __future__ import annotations

from pathlib import Path

from health_manager.cli import _run_doctor_checks
from health_manager.config import Settings
from health_manager.storage import connect, init_db


def _settings(tmp_path: Path, project_root: Path, with_api_key: bool = True) -> Settings:
    return Settings(
        project_root=tmp_path,
        config_dir=project_root / "config",
        data_dir=tmp_path / "data",
        raw_dir=tmp_path / "data" / "raw",
        processed_dir=tmp_path / "data" / "processed",
        manual_dir=tmp_path / "data" / "manual",
        reports_dir=tmp_path / "data" / "reports",
        workouts_dir=project_root / "workouts",
        intervals_api_key="dummy-key-123" if with_api_key else "",
        intervals_athlete_id="0",
        local_timezone="Asia/Tokyo",
    )


def _by_label(results: list[tuple[str, str, str]]) -> dict[str, tuple[str, str]]:
    return {label: (status, detail) for status, label, detail in results}


def test_doctor_all_ok_with_real_configs(tmp_path, project_root):
    settings = _settings(tmp_path, project_root)
    # Seed an empty SQLite so the db check has something to look at.
    settings.processed_dir.mkdir(parents=True, exist_ok=True)
    with connect(settings.sqlite_path) as conn:
        init_db(conn)

    results = _run_doctor_checks(settings)
    by = _by_label(results)

    # No FAILs.
    assert all(status != "FAIL" for status, _, _ in results)

    assert by["INTERVALS_API_KEY"][0] == "OK"
    assert by["config: goals.md"][0] == "OK"
    assert by["config: scoring.yml"][0] == "OK"
    assert by["config: metric_mapping.yml"][0] == "OK"
    assert by["config: sport_aliases.yml"][0] == "OK"
    assert by["workouts library"][0] == "OK"
    assert by["SQLite db"][0] == "OK"
    # last sync should be a WARN because we never synced in the test.
    assert by["last sync"][0] == "WARN"


def test_doctor_warns_when_api_key_missing(tmp_path, project_root):
    settings = _settings(tmp_path, project_root, with_api_key=False)
    results = _run_doctor_checks(settings)
    by = _by_label(results)
    assert by["INTERVALS_API_KEY"][0] == "WARN"


def test_doctor_fails_on_corrupt_config(tmp_path, project_root):
    # Build a settings pointing at a config dir with broken scoring.yml.
    bad_config = tmp_path / "config"
    bad_config.mkdir()
    # Copy valid files from the real config dir.
    for name in ("goals.md", "metric_mapping.yml", "sport_aliases.yml"):
        (bad_config / name).write_text(
            (project_root / "config" / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    # Write deliberately invalid YAML for scoring.yml.
    (bad_config / "scoring.yml").write_text(
        ": this is not: valid: yaml: [\n", encoding="utf-8"
    )

    settings = Settings(
        project_root=tmp_path,
        config_dir=bad_config,
        data_dir=tmp_path / "data",
        raw_dir=tmp_path / "data" / "raw",
        processed_dir=tmp_path / "data" / "processed",
        manual_dir=tmp_path / "data" / "manual",
        reports_dir=tmp_path / "data" / "reports",
        workouts_dir=project_root / "workouts",
        intervals_api_key="dummy",
        intervals_athlete_id="0",
    )
    results = _run_doctor_checks(settings)
    by = _by_label(results)
    assert by["config: scoring.yml"][0] == "FAIL"
