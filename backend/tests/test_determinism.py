"""Guards on the property the brief calls out: the deterministic layer is ground
truth and never calls an LLM.

A comment saying so would rot the first time someone added an import. These tests
read the source instead.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.core.analytics import compute_analytics
from app.core.parsing import parse_billing_log
from app.core.reconciliation import reconcile

CORE_DIR = Path(__file__).parent.parent / "app" / "core"

#: Anything that could reach a model, or the network it lives behind.
FORBIDDEN_PREFIXES = (
    "httpx",
    "requests",
    "urllib",
    "http",
    "socket",
    "openai",
    "anthropic",
    "aiohttp",
    "app.narrative",
)

CORE_MODULES = sorted(p for p in CORE_DIR.glob("*.py") if p.name != "__init__.py")


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


@pytest.mark.parametrize("module_path", CORE_MODULES, ids=lambda p: p.name)
def test_core_never_imports_an_llm_or_the_network(module_path):
    for imported in imported_modules(module_path):
        for banned in FORBIDDEN_PREFIXES:
            assert not (
                imported == banned or imported.startswith(banned + ".")
            ), f"{module_path.name} imports {imported}"


def test_core_modules_were_actually_scanned():
    """Guards the guard: a rename must not silently empty the scan."""
    names = {p.name for p in CORE_MODULES}
    assert {"parsing.py", "reconciliation.py", "analytics.py"} <= names


@pytest.mark.parametrize("fixture", ["happy_day", "edge_day", "empty_day"])
def test_reports_are_reproducible(fixture, request):
    """Same input, same numbers — every time, in any order."""
    payload = request.getfixturevalue(fixture)

    first_visits = parse_billing_log(payload).visits
    second_visits = parse_billing_log(payload).visits

    assert reconcile(first_visits).to_dict() == reconcile(second_visits).to_dict()
    assert (
        compute_analytics(first_visits).to_dict() == compute_analytics(second_visits).to_dict()
    )


def test_report_is_independent_of_row_order(happy_day):
    """Totals must not depend on the order the front desk happened to log visits."""
    visits = parse_billing_log(happy_day).visits
    reversed_visits = list(reversed(visits))

    assert reconcile(visits).to_dict() == reconcile(reversed_visits).to_dict()
    assert (
        compute_analytics(visits).to_dict() == compute_analytics(reversed_visits).to_dict()
    )
