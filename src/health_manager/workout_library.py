"""Markdown-based workout library.

Each workout is a Markdown file under `workouts/<sport>/`. The YAML front
matter holds structured metadata (validated by pydantic). The body holds:

* run / bike: Intervals.icu-compatible plain-text workout (kept verbatim).
* strength:  structured Markdown sections (`## Warmup` / `## Main` / `## Cooldown`)
             with bulleted exercise lines parsed loosely with regex.
* recovery:  plain text notes.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter
from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger(__name__)

KNOWN_SPORTS = {"run", "bike", "strength", "recovery", "swim", "walk", "hike"}
KNOWN_CATEGORIES = {
    "easy", "long", "tempo", "threshold", "intervals", "vo2max",
    "recovery", "strength_full", "strength_upper", "strength_lower",
    "mobility", "cross_train",
}


@dataclass
class Exercise:
    name: str
    sets: int | None = None
    reps: str | None = None
    load: str | None = None
    notes: str | None = None
    raw: str = ""


@dataclass
class StrengthBlock:
    warmup: list[Exercise] = field(default_factory=list)
    main: list[Exercise] = field(default_factory=list)
    cooldown: list[Exercise] = field(default_factory=list)


class WorkoutMeta(BaseModel):
    id: str
    name: str
    sport: str
    category: str
    duration_min: int
    estimated_load: float = 0.0
    recovery_cost: int = 2          # 1 (very light) .. 5 (very high)
    intensity: int = 2              # 1 (recovery) .. 5 (VO2max+)
    tags: list[str] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)
    intervals_type: str | None = None
    min_readiness: int = 0
    target_z2_minutes: int = 0

    model_config = ConfigDict(extra="ignore")


@dataclass
class Workout:
    meta: WorkoutMeta
    path: Path
    body: str
    strength_block: StrengthBlock | None = None

    @property
    def id(self) -> str:
        return self.meta.id

    @property
    def sport(self) -> str:
        return self.meta.sport


# ---------- loader ----------


def load_workouts(workouts_dir: Path) -> list[Workout]:
    workouts: list[Workout] = []
    seen_ids: dict[str, Path] = {}
    if not workouts_dir.exists():
        return workouts
    for path in sorted(workouts_dir.rglob("*.md")):
        try:
            wk = _load_one(path)
        except Exception as e:
            log.error("failed to load workout %s: %s", path, e)
            continue
        if wk.meta.sport not in KNOWN_SPORTS:
            log.info("unknown sport '%s' in %s", wk.meta.sport, path)
        if wk.meta.category not in KNOWN_CATEGORIES:
            log.info("unknown category '%s' in %s", wk.meta.category, path)
        if wk.meta.id in seen_ids:
            raise ValueError(
                f"duplicate workout id '{wk.meta.id}' in {path} and {seen_ids[wk.meta.id]}"
            )
        seen_ids[wk.meta.id] = path
        workouts.append(wk)
    return workouts


def _load_one(path: Path) -> Workout:
    with path.open("r", encoding="utf-8") as f:
        post = frontmatter.load(f)
    meta = WorkoutMeta.model_validate(dict(post.metadata or {}))
    body = post.content.strip()
    strength_block: StrengthBlock | None = None
    if meta.sport == "strength":
        strength_block = parse_strength_body(body)
    return Workout(meta=meta, path=path, body=body, strength_block=strength_block)


# ---------- strength body parser ----------


_SECTION_RE = re.compile(r"^##\s+(warmup|main|cooldown)\b", re.IGNORECASE | re.MULTILINE)
_BULLET_RE = re.compile(r"^[-*]\s+(.+)$")


def parse_strength_body(body: str) -> StrengthBlock:
    block = StrengthBlock()
    sections: dict[str, list[str]] = {"warmup": [], "main": [], "cooldown": []}
    current: str | None = None
    for line in body.splitlines():
        header = _SECTION_RE.match(line)
        if header:
            current = header.group(1).lower()
            continue
        if current and line.strip():
            sections[current].append(line)
    block.warmup = _parse_exercise_lines(sections["warmup"])
    block.main = _parse_exercise_lines(sections["main"])
    block.cooldown = _parse_exercise_lines(sections["cooldown"])
    return block


def _parse_exercise_lines(lines: Iterable[str]) -> list[Exercise]:
    out: list[Exercise] = []
    for raw_line in lines:
        m = _BULLET_RE.match(raw_line.strip())
        if not m:
            continue
        out.append(_parse_exercise(m.group(1)))
    return out


_SETS_REPS_RE = re.compile(
    r"\b(?P<sets>\d+)\s*[x×]\s*(?P<reps>[\dA-Za-z\-\s]+?)(?=\b|$|\s@|,)"
)
_LOAD_RE = re.compile(r"@\s*([^,;]+?)(?=,|$)")


def _parse_exercise(line: str) -> Exercise:
    raw = line.strip()
    sets: int | None = None
    reps: str | None = None
    load: str | None = None
    notes: str | None = None

    name_part = raw
    sr_match = _SETS_REPS_RE.search(raw)
    if sr_match:
        try:
            sets = int(sr_match.group("sets"))
        except ValueError:
            sets = None
        reps = sr_match.group("reps").strip().rstrip(",")
        name_part = raw[: sr_match.start()].rstrip(" ,:-")
    load_match = _LOAD_RE.search(raw)
    if load_match:
        load = load_match.group(1).strip()
    # Notes after a semicolon
    if ";" in raw:
        notes = raw.split(";", 1)[1].strip()

    name = name_part.strip(" -:,")
    return Exercise(name=name or raw, sets=sets, reps=reps, load=load, notes=notes, raw=raw)
