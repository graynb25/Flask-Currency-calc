"""Tests for CLI suggestions, input regex, and conversion output.

Why: the CLI shares the engine but owns its own input language, so typos
must suggest usable codes and the conversion line must stay stable.
"""

import main
from main import CONVERSION_RE, do_conversion, suggest


def _rates():
    """Why: a tiny table keeps suggestion expectations deterministic."""
    return {"rates": {"USD": 1.0, "EUR": 0.9, "COP": 4000.0}}


def test_suggest_returns_code():
    """Why: a typo must map back to a code the user can actually convert."""
    assert "USD" in suggest("usdd", _rates(), {})


def test_suggest_names_fallback_returns_codes():
    """Why: matching a full name must still suggest a code, not the name."""
    rates = _rates()
    names = {"USD": "United States Dollar", "EUR": "Euro"}
    found = suggest("united states dolllar", rates, names)
    assert "USD" in found
    assert "US DOLLAR" not in found
    assert "United States Dollar" not in found


def test_suggest_empty_names_still_matches_codes():
    """Why: names are allowed to fail, codes must work without them."""
    assert "USD" in suggest("usdd", _rates(), {})


def test_suggest_no_match_returns_hint():
    """Why: with no close match the CLI must say what to do next."""
    assert suggest("zzzqqq", _rates(), {}) == "try `list` to browse codes"


def test_conversion_regex_optional_amount_and_multi_targets():
    """Why: the REPL accepts bare pairs and comma-separated targets."""
    plain = CONVERSION_RE.match("100 usd to cop")
    assert plain is not None
    assert plain.group("amount") == "100"
    assert plain.group("to") == "cop"
    bare = CONVERSION_RE.match("usd to eur")
    assert bare is not None
    assert bare.group("amount") is None
    multi = CONVERSION_RE.match("250 eur in jpy,cad")
    assert multi is not None
    assert multi.group("amount") == "250"
    assert "jpy" in multi.group("to").lower()
    assert "cad" in multi.group("to").lower()


def test_do_conversion_prints_expected_line(capsys):
    """Why: the conversion line is the CLI contract users read."""
    rates = {"rates": {"USD": 1.0, "EUR": 0.9}}
    do_conversion("100 usd to eur", rates, {})
    out = capsys.readouterr().out
    assert "100.00 USD" in out
    assert "90.00 EUR" in out
    assert "1 USD = 0.900000 EUR" in out
