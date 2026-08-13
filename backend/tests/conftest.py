from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())


@pytest.fixture
def happy_day():
    return load_fixture("day_happy")


@pytest.fixture
def edge_day():
    return load_fixture("day_edge")


@pytest.fixture
def invalid_day():
    return load_fixture("day_invalid")


@pytest.fixture
def empty_day():
    return load_fixture("day_empty")
