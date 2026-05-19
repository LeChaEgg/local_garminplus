"""Shared fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture()
def project_root() -> Path:
    return ROOT


@pytest.fixture()
def sample_config_dir() -> Path:
    return ROOT / "config"


@pytest.fixture()
def sample_workouts_dir() -> Path:
    return ROOT / "workouts"


@pytest.fixture()
def fixtures_dir() -> Path:
    return ROOT / "tests" / "fixtures"
