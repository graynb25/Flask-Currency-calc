"""Tests for parse_amount separator rules.

Why: the parser is the one shared definition of user input, and a wrong
guess about grouping versus decimal is a 1000x error that still looks
plausible, so each rule needs a pinned example.
"""

from decimal import Decimal

import pytest

from converter import CurrencyError, parse_amount


def test_both_separators_agree():
    """Why: people on both sides of the Atlantic type the same value."""
    assert parse_amount("1,234.56") == Decimal("1234.56")
    assert parse_amount("1.234,56") == Decimal("1234.56")
    assert parse_amount("1,234.56") == parse_amount("1.234,56")


def test_single_comma_grouping():
    """Why: a money tool must read 1,234 as thousands, not 1.234."""
    assert parse_amount("1,234") == Decimal("1234")


def test_single_separator_decimal():
    """Why: short fractions must stay fractional in either convention."""
    assert parse_amount("1.5") == Decimal("1.5")
    assert parse_amount("1,5") == Decimal("1.5")


def test_trailing_dot_accepted():
    """Why: mid-edit input should not error while the user is still typing."""
    assert parse_amount("100.") == Decimal("100")


def test_leading_dot_becomes_fraction():
    """Why: .5 is ordinary shorthand for half a unit."""
    assert parse_amount(".5") == Decimal("0.5")


def test_leading_comma_rejected():
    """Why: ,5 is not a number anyone means, so fail loudly."""
    with pytest.raises(CurrencyError):
        parse_amount(",5")


def test_repeated_separator_is_grouping():
    """Why: thousands group the same way no matter which char is used."""
    assert parse_amount("1.234.567") == Decimal("1234567")
    assert parse_amount("1,234,567") == Decimal("1234567")


def test_exponent_forms():
    """Why: pasted scientific input should scale, not error."""
    assert parse_amount("1.5e2") == Decimal("150")
    assert parse_amount("1,5e2") == Decimal("150")
    assert parse_amount("2E3") == Decimal("2000")


def test_spaces_and_nbsp_stripped():
    """Why: pasted figures carry spaces and non-breaking spaces as groupers."""
    assert parse_amount("1 234.56") == Decimal("1234.56")
    assert parse_amount("1\u00a0234.56") == Decimal("1234.56")
    assert parse_amount("1\u202f234.56") == Decimal("1234.56")
    assert parse_amount("1\u2009234.56") == Decimal("1234.56")


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "abc", "12a34", "1.2.3.4", "--1", "100,", ",,,", "1..2", "e10"],
)
def test_garbage_raises(bad):
    """Why: invalid input must fail cleanly, never guess a value."""
    with pytest.raises(CurrencyError):
        parse_amount(bad)


@pytest.mark.parametrize(
    "bad",
    [float("inf"), float("-inf"), float("nan"), "inf", "Infinity", "NaN"],
)
def test_nonfinite_rejected(bad):
    """Why: non-finite input would later poison JSON or math."""
    with pytest.raises(CurrencyError):
        parse_amount(bad)
