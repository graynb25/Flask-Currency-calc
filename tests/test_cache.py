"""Tests for cache loading, stale fallback, and atomic writes.

Why: the cache is the offline lifeline, so a weird file must refetch
instead of 500-ing, an outage must serve stale data, and a write must
never corrupt the file or drop the other section.
"""

import json
import os
import time

import pytest

import converter
from converter import CurrencyError


def _write_cache(payload):
    converter.CACHE_FILE.write_text(json.dumps(payload), encoding="utf-8")


def _fresh_rates():
    return {
        "base": "USD",
        "rates": {"USD": 1.0, "EUR": 0.9},
        "updated": "fresh",
        "fetched_at": time.time(),
        "source": "test",
    }


def test_load_cache_rejects_non_dicts():
    """Why: valid JSON of the wrong shape is not a cache anyone can use."""
    for bad in [[], [1, 2], "hello", 123]:
        converter.CACHE_FILE.write_text(json.dumps(bad), encoding="utf-8")
        assert converter._load_cache() is None


def test_load_cache_dict_without_rates_stays_permissive():
    """Why: history entries must survive even when the rates table is absent."""
    _write_cache({"history": {"USD/EUR/30": {"fetched_at": time.time(), "points": []}}})
    loaded = converter._load_cache()
    assert isinstance(loaded, dict)
    assert "history" in loaded


def test_fetch_rates_refetches_without_usable_table(monkeypatch):
    """Why: missing or misshapen rates must trigger a live fetch, not a 500."""
    cases = [
        {},
        {"history": {}},
        {"rates": ["USD"]},
        {"rates": "USD"},
        {"rates": None},
    ]
    for bad in cases:
        converter._RATES_GATE = converter._Upstream()
        _write_cache(bad)
        monkeypatch.setattr(converter, "_fetch_fresh_rates", _fresh_rates)
        result = converter.fetch_rates()
        assert result["rates"] == {"USD": 1.0, "EUR": 0.9}


def test_fetch_rates_refetches_top_level_list(monkeypatch):
    """Why: a top-level list used to flow into the app and surface as a 500."""
    converter.CACHE_FILE.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(converter, "_fetch_fresh_rates", _fresh_rates)
    result = converter.fetch_rates()
    assert isinstance(result["rates"], dict)


def test_stale_served_when_upstream_fails(monkeypatch):
    """Why: an outage with a cache must degrade, not error."""
    _write_cache(
        {
            "base": "USD",
            "rates": {"USD": 1.0},
            "updated": "old",
            "fetched_at": time.time() - 7 * 3600,
            "source": "test",
        }
    )
    monkeypatch.setattr(converter._RATES_GATE, "run", lambda *a, **k: (None, False))
    result = converter.fetch_rates()
    assert result["stale"] is True
    assert result["rates"] == {"USD": 1.0}


def test_missing_cache_and_failed_upstream_raises(monkeypatch):
    """Why: with no cache and no upstream there is nothing honest to serve."""
    if converter.CACHE_FILE.exists():
        converter.CACHE_FILE.unlink()
    monkeypatch.setattr(converter._RATES_GATE, "run", lambda *a, **k: (None, False))
    with pytest.raises(CurrencyError):
        converter.fetch_rates()


def test_failed_save_is_swallowed_and_leaves_no_temp(monkeypatch):
    """Why: caching is best effort, live conversion must still work."""
    def _boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", _boom)
    converter._save_cache({"rates": {"USD": 1.0}})
    leftovers = list(converter.CACHE_FILE.parent.glob("rates_cache.json.*.tmp"))
    assert leftovers == []


def test_rates_refresh_merges_without_dropping_history():
    """Why: a rates refresh once wiped the chart history it shared a file with."""
    _write_cache(
        {
            "base": "USD",
            "rates": {"USD": 1.0},
            "updated": "old",
            "fetched_at": time.time(),
            "source": "test",
            "history": {
                "USD/EUR/30": {"fetched_at": time.time(), "points": [{"date": "2024-01-01", "rate": 0.9}]}
            },
        }
    )
    converter._save_top({"base": "USD", "rates": {"USD": 1.0, "EUR": 0.9}})
    merged = json.loads(converter.CACHE_FILE.read_text(encoding="utf-8"))
    assert merged["rates"]["EUR"] == 0.9
    assert merged["history"]["USD/EUR/30"]["points"] == [{"date": "2024-01-01", "rate": 0.9}]
