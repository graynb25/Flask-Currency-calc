"""Tests for history series inversion.

Why: when Frankfurter only serves the reverse pair, the chart still needs
a correctly dated series, and a zero rate must not divide by zero.
"""

from converter import _invert


def test_zero_rate_becomes_none():
    """Why: a zero upstream rate is missing data, not a crash."""
    points = [{"date": "2024-01-02", "rate": 0}]
    assert _invert(points) == [{"date": "2024-01-02", "rate": None}]


def test_inversion_math_holds():
    """Why: flipping twice must return the original rates for the chart."""
    points = [
        {"date": "2024-01-01", "rate": 2.0},
        {"date": "2024-01-02", "rate": 0.5},
    ]
    flipped = _invert(points)
    assert flipped[0] == {"date": "2024-01-01", "rate": 0.5}
    assert flipped[1] == {"date": "2024-01-02", "rate": 2.0}
    assert [item["date"] for item in flipped] == ["2024-01-01", "2024-01-02"]
