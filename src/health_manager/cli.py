"""Typer CLI entry point. Exposes:

  health init
  health sync --days N
  health today
  health report weekly|monthly
  health dashboard
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import (
    load_goals,
    load_metric_mapping,
    load_scoring,
    load_settings,
)
from .ingest import sync as run_sync
from .recommender import recommend
from .reports import write_daily, write_monthly, write_weekly
from .scoring import compute_confidence, compute_readiness
from .storage import (
    connect,
    get_meta,
    init_db,
    read_activities,
    read_checkins,
    read_wellness,
    upsert_recommendation,
)
from .workout_library import load_workouts

app = typer.Typer(add_completion=False, help="Local-first personal health & training tool.")
console = Console()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def _here() -> Path:
    """Package directory (used to locate sample files for `health init`)."""
    return Path(__file__).resolve().parent


def _samples_dir() -> Path:
    return _here().parent.parent   # project root when running from source


@app.callback()
def main(version: bool = typer.Option(False, "--version", help="Show version and exit.")) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit()


# ---------- init ----------


@app.command()
def init() -> None:
    """Create sample configs, manual CSV templates, and sample workouts."""
    settings = load_settings()
    root = settings.project_root
    samples = _samples_dir()

    for d in (
        settings.config_dir,
        settings.workouts_dir,
        settings.manual_dir,
        settings.raw_dir / "intervals" / "wellness",
        settings.raw_dir / "intervals" / "activities",
        settings.raw_dir / "intervals" / "activity_details",
        settings.processed_dir,
        settings.reports_dir / "daily",
        settings.reports_dir / "weekly",
        settings.reports_dir / "monthly",
    ):
        d.mkdir(parents=True, exist_ok=True)

    # Copy config samples (don't overwrite user edits).
    for name in ("goals.md", "scoring.yml", "metric_mapping.yml"):
        src = samples / "config" / name
        dst = settings.config_dir / name
        if src.exists() and not dst.exists():
            shutil.copyfile(src, dst)
            console.print(f"[green]wrote[/green] {dst.relative_to(root)}")

    # Copy sample workouts (don't overwrite).
    for sport_dir in ("run", "bike", "strength", "recovery"):
        src = samples / "workouts" / sport_dir
        dst = settings.workouts_dir / sport_dir
        dst.mkdir(parents=True, exist_ok=True)
        if src.exists():
            for f in src.glob("*.md"):
                target = dst / f.name
                if not target.exists():
                    shutil.copyfile(f, target)
                    console.print(f"[green]wrote[/green] {target.relative_to(root)}")

    # Manual CSV templates.
    body_csv = settings.manual_dir / "body_metrics.csv"
    if not body_csv.exists():
        body_csv.write_text("date,weight_kg,waist_cm,body_fat_pct,notes\n", encoding="utf-8")
        console.print(f"[green]wrote[/green] {body_csv.relative_to(root)}")
    checkin_csv = settings.manual_dir / "daily_checkins.csv"
    if not checkin_csv.exists():
        checkin_csv.write_text(
            "date,soreness,motivation,stress,sleep_quality,notes\n", encoding="utf-8"
        )
        console.print(f"[green]wrote[/green] {checkin_csv.relative_to(root)}")

    # .env.example -> .env (only if neither exists yet).
    env_example = samples / ".env.example"
    env_target = root / ".env.example"
    if env_example.exists() and not env_target.exists():
        shutil.copyfile(env_example, env_target)
        console.print(f"[green]wrote[/green] {env_target.relative_to(root)}")

    # Initialize SQLite up front so 'today' works without a sync.
    with connect(settings.sqlite_path) as conn:
        init_db(conn)
    console.print(f"[green]initialized[/green] {settings.sqlite_path.relative_to(root)}")
    console.print(
        "\n[bold]Next:[/bold] set INTERVALS_API_KEY in .env, then run "
        "[cyan]health sync --days 180[/cyan] and [cyan]health today[/cyan]."
    )


# ---------- sync ----------


@app.command()
def sync(
    days: int = typer.Option(180, "--days", help="How many days back to fetch."),
    force_details: bool = typer.Option(
        False,
        "--force-details",
        help="Re-fetch activity intervals even if the raw JSON is already cached.",
    ),
) -> None:
    """Pull recent wellness + activities from Intervals.icu."""
    settings = load_settings()
    if not settings.intervals_api_key:
        console.print("[red]INTERVALS_API_KEY is not set in .env[/red]")
        raise typer.Exit(code=2)
    mapping = load_metric_mapping(settings.metric_mapping_path)
    report = run_sync(settings, mapping, days=days, force_details=force_details)
    console.print(
        f"[green]synced[/green] wellness={report.wellness_count} "
        f"activities={report.activity_count} "
        f"detail_new={report.detail_count} detail_cached={report.detail_cached}"
    )
    if report.unknown_wellness_fields:
        console.print(f"unknown wellness fields: {sorted(report.unknown_wellness_fields)}")
    if report.unknown_activity_fields:
        console.print(f"unknown activity fields: {sorted(report.unknown_activity_fields)}")


# ---------- today ----------


@app.command()
def today(
    skip_sync: bool = typer.Option(False, "--skip-sync", help="Don't auto-sync if data is stale."),
    on_date: str | None = typer.Option(None, "--date", help="ISO date override (defaults to today)."),
) -> None:
    """Compute readiness, pick today's workout, write the daily report."""
    settings = load_settings()
    target = date.fromisoformat(on_date) if on_date else date.today()

    if not skip_sync and settings.intervals_api_key:
        _maybe_sync(settings)

    goals = load_goals(settings.goals_path)
    scoring = load_scoring(settings.scoring_path)
    workouts = load_workouts(settings.workouts_dir)

    with connect(settings.sqlite_path) as conn:
        init_db(conn)
        wellness = read_wellness(conn)
        activities = read_activities(conn)
        checkins = read_checkins(conn)

    readiness = compute_readiness(target, wellness, activities, checkins, goals, scoring)
    confidence = compute_confidence(
        target, wellness, activities, goals, scoring,
        hrv_stat=readiness.hrv_stat,
        rhr_stat=readiness.rhr_stat,
    )
    rec = recommend(target, workouts, readiness, confidence, goals, activities, wellness)

    md_path, json_path = write_daily(settings, rec)

    with connect(settings.sqlite_path) as conn:
        upsert_recommendation(
            conn,
            date=target.isoformat(),
            workout_id=rec.main.workout_id if rec.main else None,
            readiness=readiness.score,
            readiness_level=readiness.level,
            confidence=confidence.level,
            payload={"main": asdict(rec.main) if rec.main else None},
        )

    _print_today(rec, md_path, json_path)


def _maybe_sync(settings, stale_hours: float = 6.0) -> None:
    """Auto-sync if last sync is older than `stale_hours`."""
    try:
        with connect(settings.sqlite_path) as conn:
            init_db(conn)
            last = get_meta(conn, "last_sync_utc")
    except sqlite3.Error:
        last = None
    needs_sync = True
    if last:
        try:
            t = datetime.fromisoformat(last)
            if t.tzinfo is None:
                t = t.replace(tzinfo=UTC)
            age = (datetime.now(UTC) - t).total_seconds() / 3600.0
            needs_sync = age >= stale_hours
        except ValueError:
            needs_sync = True
    if needs_sync:
        console.print("[dim]data stale; auto-syncing last 7 days…[/dim]")
        mapping = load_metric_mapping(settings.metric_mapping_path)
        try:
            # Short window for auto-sync; relies on the on-disk detail cache to
            # avoid re-fetching activity intervals we already have.
            run_sync(settings, mapping, days=7)
        except Exception as e:
            console.print(f"[yellow]auto-sync failed:[/yellow] {e}")


def _print_today(rec, md_path: Path, json_path: Path) -> None:
    r = rec.readiness
    console.print(f"\n[bold]Readiness:[/bold] {r.score:.0f}/100 ([cyan]{r.level}[/cyan])")
    if rec.main:
        console.print(
            f"[bold]Main:[/bold] {rec.main.name} ({rec.main.sport}, {rec.main.duration_min} min)"
        )
    if rec.conservative:
        console.print(f"  conservative: {rec.conservative.name}")
    if rec.progressive:
        console.print(f"  progressive:  {rec.progressive.name}")
    console.print(f"[bold]Confidence:[/bold] {rec.confidence.level} ({rec.confidence.score:.2f})")

    if r.risk_flags:
        console.print("[yellow]Risk flags:[/yellow] " + "; ".join(r.risk_flags))

    table = Table(title="Reasons", show_header=False)
    for reason in r.reasons[:8]:
        table.add_row(reason)
    if r.reasons:
        console.print(table)

    console.print(f"\n[dim]wrote {md_path}[/dim]")
    console.print(f"[dim]wrote {json_path}[/dim]")


# ---------- report ----------


report_app = typer.Typer(help="Generate weekly / monthly reports.")
app.add_typer(report_app, name="report")


@report_app.command("weekly")
def report_weekly(
    on_date: str | None = typer.Option(None, "--date", help="ISO date inside the target week."),
) -> None:
    settings = load_settings()
    goals = load_goals(settings.goals_path)
    target = date.fromisoformat(on_date) if on_date else date.today()
    md, js = write_weekly(settings, goals, target)
    console.print(f"[green]wrote[/green] {md} and {js}")


@report_app.command("monthly")
def report_monthly(
    on_date: str | None = typer.Option(None, "--date", help="ISO date inside the target month."),
) -> None:
    settings = load_settings()
    goals = load_goals(settings.goals_path)
    target = date.fromisoformat(on_date) if on_date else date.today()
    md, js = write_monthly(settings, goals, target)
    console.print(f"[green]wrote[/green] {md} and {js}")


# ---------- dashboard ----------


@app.command()
def dashboard(port: int = typer.Option(8501, "--port")) -> None:
    """Launch the Streamlit dashboard."""
    target = _here() / "dashboard.py"
    cmd = [sys.executable, "-m", "streamlit", "run", str(target), "--server.port", str(port)]
    console.print(f"[dim]running:[/dim] {' '.join(cmd)}")
    subprocess.run(cmd, check=False)


if __name__ == "__main__":   # pragma: no cover
    app()
