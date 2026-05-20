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


# ---------- doctor ----------


@app.command()
def doctor() -> None:
    """Run a quick environment + config + data sanity check.

    Reports OK / WARN / FAIL for each check and exits non-zero only when
    something is clearly broken (parser errors, missing required env vars).
    """
    settings = load_settings()
    results = _run_doctor_checks(settings)

    n_fail = sum(1 for r in results if r[0] == "FAIL")
    n_warn = sum(1 for r in results if r[0] == "WARN")

    style = {"OK": "green", "WARN": "yellow", "FAIL": "red"}
    for status, label, detail in results:
        console.print(
            f"[{style[status]}]{status:<4}[/{style[status]}]  "
            f"[bold]{label}[/bold]  {detail}"
        )

    console.print()
    if n_fail:
        console.print(f"[red]✗ {n_fail} failing, {n_warn} warning(s).[/red]")
        raise typer.Exit(code=1)
    if n_warn:
        console.print(f"[yellow]△ {n_warn} warning(s) — fix when convenient.[/yellow]")
        return
    console.print("[green]✓ All checks passed.[/green]")


def _run_doctor_checks(settings) -> list[tuple[str, str, str]]:
    """Returns a list of (status, label, detail) tuples."""
    from datetime import datetime, timedelta

    out: list[tuple[str, str, str]] = []

    # Project layout
    root = settings.project_root
    if not (root / "pyproject.toml").exists():
        out.append(("WARN", "project root", f"{root} has no pyproject.toml"))
    else:
        out.append(("OK", "project root", str(root)))

    # Env vars
    if settings.intervals_api_key:
        masked = settings.intervals_api_key[:3] + "…" + settings.intervals_api_key[-3:]
        out.append(("OK", "INTERVALS_API_KEY", f"set ({masked})"))
    else:
        out.append(("WARN", "INTERVALS_API_KEY", "not set — sync will fail"))
    out.append(("OK", "INTERVALS_ATHLETE_ID", settings.intervals_athlete_id))
    out.append(("OK", "LOCAL_TIMEZONE", settings.local_timezone))

    # Config files
    for label, p in (
        ("goals.md", settings.goals_path),
        ("scoring.yml", settings.scoring_path),
        ("metric_mapping.yml", settings.metric_mapping_path),
        ("sport_aliases.yml", settings.sport_aliases_path),
    ):
        if not p.exists():
            out.append(("WARN", f"config: {label}", f"missing at {p}"))
            continue
        try:
            if label == "goals.md":
                load_goals(p)
            elif label == "scoring.yml":
                load_scoring(p)
            elif label == "metric_mapping.yml":
                load_metric_mapping(p)
            elif label == "sport_aliases.yml":
                from .config import load_sport_aliases as _lsa
                _lsa(p)
        except Exception as e:
            out.append(("FAIL", f"config: {label}", f"parse error: {e}"))
            continue
        out.append(("OK", f"config: {label}", "parses cleanly"))

    # Workouts library
    try:
        wks = load_workouts(settings.workouts_dir)
    except Exception as e:
        out.append(("FAIL", "workouts library", f"load error: {e}"))
    else:
        by_sport: dict[str, int] = {}
        for w in wks:
            by_sport[w.sport] = by_sport.get(w.sport, 0) + 1
        summary = ", ".join(f"{k}:{v}" for k, v in sorted(by_sport.items()))
        if not wks:
            out.append(("WARN", "workouts library", "empty"))
        elif len(wks) < 6:
            out.append(("WARN", "workouts library", f"only {len(wks)} workouts — {summary}"))
        else:
            out.append(("OK", "workouts library", f"{len(wks)} loaded ({summary})"))

    # SQLite
    if not settings.sqlite_path.exists():
        out.append(("WARN", "SQLite db", f"not created yet at {settings.sqlite_path}"))
    else:
        try:
            with connect(settings.sqlite_path) as conn:
                init_db(conn)
                wellness_n = conn.execute(
                    "SELECT COUNT(*) FROM wellness_daily"
                ).fetchone()[0]
                act_n = conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0]
                last_sync = get_meta(conn, "last_sync_utc")
                schema_v = get_meta(conn, "schema_version")
        except Exception as e:
            out.append(("FAIL", "SQLite db", f"open error: {e}"))
            return out
        out.append(
            ("OK", "SQLite db",
             f"wellness={wellness_n}, activities={act_n}, schema_v={schema_v}")
        )
        if last_sync:
            try:
                t = datetime.fromisoformat(last_sync)
                age_h = (datetime.now(t.tzinfo) - t).total_seconds() / 3600.0
                if age_h > 24:
                    out.append(("WARN", "last sync", f"{age_h:.1f}h ago — consider `health sync`"))
                else:
                    out.append(("OK", "last sync", f"{age_h:.1f}h ago"))
            except ValueError:
                out.append(("WARN", "last sync", f"unparseable: {last_sync}"))
        else:
            out.append(("WARN", "last sync", "never — run `health sync`"))

        # Baseline freshness for HRV
        if wellness_n > 0:
            try:
                with connect(settings.sqlite_path) as conn:
                    hrv_n = conn.execute(
                        "SELECT COUNT(*) FROM wellness_daily "
                        "WHERE hrv IS NOT NULL AND date >= ?",
                        ((datetime.now() - timedelta(days=28)).date().isoformat(),),
                    ).fetchone()[0]
            except Exception:
                hrv_n = 0
            if hrv_n < 7:
                out.append(("WARN", "HRV baseline", f"only {hrv_n}/28 days populated"))
            elif hrv_n < 21:
                out.append(("WARN", "HRV baseline", f"{hrv_n}/28 days — still building"))
            else:
                out.append(("OK", "HRV baseline", f"{hrv_n}/28 days"))

    return out


# ---------- dashboard ----------


@app.command()
def dashboard(
    no_open: bool = typer.Option(
        False, "--no-open", help="Write the HTML but don't auto-open the browser."
    ),
) -> None:
    """Regenerate the static HTML dashboard and open it in the default browser."""
    from .dashboard import build_and_write_dashboard

    settings = load_settings()
    out = build_and_write_dashboard(settings)
    console.print(f"[green]wrote[/green] {out}")
    if no_open:
        return
    _open_in_browser(out)


def _open_in_browser(path: Path) -> None:
    """Open a local file in the default browser, cross-platform."""
    import webbrowser

    url = path.resolve().as_uri()
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    elif sys.platform.startswith("linux"):
        subprocess.run(["xdg-open", str(path)], check=False)
    elif sys.platform == "win32":
        subprocess.run(["cmd", "/c", "start", "", str(path)], check=False, shell=False)
    else:
        webbrowser.open(url)


if __name__ == "__main__":   # pragma: no cover
    app()
