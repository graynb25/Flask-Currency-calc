"""Tests for cross-rate math, validation, and float narrowing.

Why: money math must stay exact through Decimal, reject bad input at the
boundary, and never let an out-of-range value become an Infinity token
that breaks JSON parsing in the browser.
"""

import json
import math
from decimal import Decimal

import pytest

import converter
from converter import CurrencyError, convert, finite_float, get_rate


def _rates():
    """Why: a fixed USD table makes cross-rate expectations exact."""
    return {
        "base": "USD",
        "rates": {"USD": 1.0, "EUR": 0.9, "COP": 4000.0, "JPY": 150.0},
        "updated": "test",
        "fetched_at": 0,
        "source": "test",
    }


def test_cross_rate_via_usd():
    """Why: every pair derives from the single USD table, not live data."""
    rates = _rates()
    assert get_rate("USD", "EUR", rates) == Decimal("0.9")
    assert get_rate("EUR", "COP", rates) == Decimal(str(4000.0)) / Decimal(str(0.9))


def test_reciprocal_product_within_3e28():
    """Why: ARCHITECTURE.md promises this bound, so pin it."""
    rates = _rates()
    for one, other in [("EUR", "COP"), ("USD", "JPY"), ("EUR", "USD")]:
        product = get_rate(one, other, rates) * get_rate(other, one, rates)
        assert abs(product - 1) < Decimal("3e-28")


def test_round_trip_within_3e26():
    """Why: ARCHITECTURE.md promises this bound for a 100 unit trip."""
    rates = _rates()
    start = Decimal("100")
    there = convert(start, "EUR", "COP", rates)
    back = convert(there, "COP", "EUR", rates)
    assert abs(back - start) < Decimal("3e-26")


def test_unsupported_code_raises():
    """Why: unknown codes must fail with a clean error, not a KeyError."""
    rates = _rates()
    with pytest.raises(CurrencyError):
        get_rate("USD", "XXX", rates)
    with pytest.raises(CurrencyError):
        convert(Decimal("1"), "XXX", "USD", rates)


def test_negative_amount_rejected():
    """Why: negative money is invalid input, not a conversion."""
    rates = _rates()
    with pytest.raises(CurrencyError):
        convert(Decimal("-1"), "USD", "EUR", rates)
    with pytest.raises(CurrencyError):
        convert("-5", "USD", "EUR", rates)


def test_zero_rate_is_clean_error():
    """Why: bad upstream data must surface as CurrencyError, not crash."""
    rates = _rates()
    rates["rates"]["EUR"] = 0.0
    with pytest.raises(CurrencyError):
        get_rate("EUR", "USD", rates)
    with pytest.raises(CurrencyError):
        convert(Decimal("1"), "EUR", "USD", rates)
    try:
        get_rate("EUR", "USD", rates)
    except ZeroDivisionError:
        pytest.fail("zero rate escaped as ZeroDivisionError")
    except CurrencyError:
        pass


def test_finite_float_rejects_overflow():
    """Why: Decimal holds 1e400 but float64 cannot, and Infinity is bad JSON."""
    with pytest.raises(CurrencyError):
        finite_float(Decimal("1e400"))
    with pytest.raises(CurrencyError):
        finite_float(float("inf"))
    with pytest.raises(CurrencyError):
        finite_float(float("nan"))
    assert finite_float(Decimal("1.5")) == 1.5
    assert math.isfinite(finite_float(Decimal("1.5")))
    assert "Infinity" not in json.dumps({"v": finite_float(Decimal("1.5"))})


def test_convert_rejects_nonfinite():
    """Why: the boundary must stop Infinity and NaN before they spread."""
    rates = _rates()
    with pytest.raises(CurrencyError):
        convert(float("inf"), "USD", "EUR", rates)
    with pytest.raises(CurrencyError):
        convert("NaN", "USD", "EUR", rates)
