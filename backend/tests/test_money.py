from __future__ import annotations

import pytest

from app.core.money import format_paise, group_indian, percent


@pytest.mark.parametrize(
    "digits,expected",
    [
        ("0", "0"),
        ("42", "42"),
        ("850", "850"),
        ("4850", "4,850"),
        ("42850", "42,850"),
        ("142850", "1,42,850"),
        ("1234567", "12,34,567"),
    ],
)
def test_indian_grouping(digits, expected):
    assert group_indian(digits) == expected


@pytest.mark.parametrize(
    "paise,expected",
    [
        (0, "₹0"),
        (4285000, "₹42,850"),
        (3820000, "₹38,200"),
        (60000, "₹600"),
        (4285050, "₹42,850.50"),
        (2050, "₹20.50"),
        (-15000, "-₹150"),
    ],
)
def test_format_paise(paise, expected):
    assert format_paise(paise) == expected


def test_format_paise_rejects_floats():
    """A float here means rupees leaked in somewhere; fail loudly rather than round."""
    with pytest.raises(TypeError):
        format_paise(428.50)


def test_percent_is_none_for_zero_denominator():
    """An empty day has an undefined collection rate, not a 0% one."""
    assert percent(0, 0) is None
    assert percent(3820000, 4285000) == 89
