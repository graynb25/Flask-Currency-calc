"""Regression tests for the first total-upstream failure in a retry window.

Why: the gate leader once reported ok=True with a None result, so the very
first outage in a window returned None (or crashed on it) instead of stale
data or a clean error, while every later call in the window behaved because
followers read the stored verdict.
"""

import json
import threading
import time

import pytest

import app as flask_app
import converter
from app import app
from converter import CurrencyError


def _expired_cache(names=None):
    """Why: an expired but usable cache is what an outage must fall back to."""
    payload = {
        "base": "USD",
        "rates": {"USD": 1.0, "EUR": 0.9},
        "updated": "old",
        "fetched_at": time.time() - 7 * 3600,
        "source": "test",
    }
    if names is not None:
        payload["names"] = names
    converter.CACHE_FILE.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def test_gate_leader_none_is_not_ok():
    """Why: the docstring promises ok means live data, and None is not data."""
    gate = converter._Upstream()
    assert gate.run(lambda: None) == (None, False)


def test_gate_follower_of_none_leader_is_not_ok():
    """Why: followers already read the stored verdict, the leader must match."""
    gate = converter._Upstream()
    entered = threading.Event()
    release = threading.Event()
    errors = []
    outcomes = []

    def fetch():
        entered.set()
        assert release.wait(timeout=5)
        return None

    def leader():
        try:
            outcomes.append(gate.run(fetch))
        except Exception as exc:  # keep thread errors visible to the test
            errors.append(exc)

    def follower():
        try:
            outcomes.append(gate.run(lambda: (_ for _ in ()).throw(AssertionError("must coalesce"))))
        except Exception as exc:
            errors.append(exc)

    first = threading.Thread(target=leader)
    first.start()
    assert entered.wait(timeout=5)
    second = threading.Thread(target=follower)
    second.start()
    time.sleep(0.2)
    release.set()
    first.join(timeout=5)
    second.join(timeout=5)
    assert not errors
    assert len(outcomes) == 2
    assert all(outcome == (None, False) for outcome in outcomes)


def test_expired_cache_first_failure_serves_stale(monkeypatch):
    """Why: the first outage must degrade to stale, never hand back None."""
    _expired_cache()
    monkeypatch.setattr(converter, "_fetch_primary", lambda: None)
    monkeypatch.setattr(converter, "_fetch_fallback", lambda: None)
    result = converter.fetch_rates()
    assert result is not None
    assert result["stale"] is True
    assert result["rates"] == {"USD": 1.0, "EUR": 0.9}


def test_no_cache_first_failure_raises(monkeypatch):
    """Why: with no cache and no upstream, the honest answer is an error."""
    if converter.CACHE_FILE.exists():
        converter.CACHE_FILE.unlink()
    monkeypatch.setattr(converter, "_fetch_primary", lambda: None)
    monkeypatch.setattr(converter, "_fetch_fallback", lambda: None)
    with pytest.raises(CurrencyError):
        converter.fetch_rates()


def test_api_currencies_first_failure_serves_stale(monkeypatch):
    """Why: the currencies endpoint must stay a 200 with stale data on outage."""
    _expired_cache(names={"USD": "US Dollar", "EUR": "Euro"})
    monkeypatch.setattr(converter, "_fetch_primary", lambda: None)
    monkeypatch.setattr(converter, "_fetch_fallback", lambda: None)
    flask_app._api_hits.clear()
    app.config.update({"TESTING": True})
    with app.test_client() as client:
        resp = client.get("/api/currencies")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["stale"] is True
    assert body["rates"] == {"USD": 1.0, "EUR": 0.9}
