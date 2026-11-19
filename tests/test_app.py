"""Tests for the Flask API: limits, parsing, errors, and headers.

Why: the web layer is the untrusted-input boundary, so it must parse both
amount conventions, reject what float64 cannot hold, throttle runaway
clients, and always answer with valid JSON and a strict CSP.
"""

import json
import time

import pytest

import app as flask_app
import converter
from app import app


@pytest.fixture()
def client():
    """Why: each API test needs a clean client without rate state leaking."""
    flask_app._api_hits.clear()
    app.config.update({"TESTING": True})
    with app.test_client() as test_client:
        yield test_client
    flask_app._api_hits.clear()


def _table(stale=False):
    payload = {
        "base": "USD",
        "rates": {"USD": 1.0, "EUR": 0.9, "COP": 4000.0},
        "updated": "test",
        "fetched_at": time.time(),
        "source": "test",
    }
    if stale:
        payload["stale"] = True
    return payload


def test_rate_limiter_strict_caps_and_retry_after(client, monkeypatch):
    """Why: expensive endpoints need tighter caps so one client cannot flood."""
    monkeypatch.setattr(converter, "fetch_rates", lambda force=False: _table())
    for _ in range(10):
        assert client.post("/api/refresh").status_code == 200
    limited = client.post("/api/refresh")
    assert limited.status_code == 429
    assert limited.headers.get("Retry-After") == "60"


def test_rate_limiter_history_cap(client, monkeypatch):
    """Why: history can fan out to many upstream reads when cold."""
    monkeypatch.setattr(converter, "fetch_rates", lambda force=False: _table())
    monkeypatch.setattr(converter, "fetch_history", lambda *a, **k: [])
    for _ in range(30):
        assert client.get("/api/history?from=USD&to=EUR&days=7").status_code == 200
    limited = client.get("/api/history?from=USD&to=EUR&days=7")
    assert limited.status_code == 429
    assert limited.headers.get("Retry-After") == "60"


def test_normal_traffic_passes(client, monkeypatch):
    """Why: the limiter must throttle floods without blocking normal use."""
    monkeypatch.setattr(converter, "fetch_rates", lambda force=False: _table())
    for _ in range(5):
        assert client.get("/api/convert?amount=1&from=USD&to=EUR").status_code == 200


def test_convert_accepts_both_separators(client, monkeypatch):
    """Why: the API shares the CLI parser, so both conventions must agree."""
    monkeypatch.setattr(converter, "fetch_rates", lambda force=False: _table())
    first = client.get("/api/convert?amount=1,234.56&from=USD&to=EUR")
    second = client.get("/api/convert?amount=1.234,56&from=USD&to=EUR")
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.get_json()["results"] == second.get_json()["results"]


def test_convert_overflow_is_400_without_infinity(client, monkeypatch):
    """Why: 1e400 cannot survive float64, and Infinity is not valid JSON."""
    monkeypatch.setattr(converter, "fetch_rates", lambda force=False: _table())
    resp = client.get("/api/convert?amount=1e400&from=USD&to=EUR")
    assert resp.status_code == 400
    assert b"Infinity" not in resp.data
    json.loads(resp.data.decode("utf-8"))


def test_convert_unknown_currency_is_400(client, monkeypatch):
    """Why: unknown codes must fail cleanly with JSON, not a stack trace."""
    monkeypatch.setattr(converter, "fetch_rates", lambda force=False: _table())
    assert client.get("/api/convert?amount=1&from=XXX&to=EUR").status_code == 400
    assert client.get("/api/convert?amount=1&from=USD&to=XXX").status_code == 400


def test_convert_carries_stale(client, monkeypatch):
    """Why: the UI shows an amber pill when the numbers are stale."""
    monkeypatch.setattr(converter, "fetch_rates", lambda force=False: _table(stale=True))
    resp = client.get("/api/convert?amount=1&from=USD&to=EUR")
    assert resp.status_code == 200
    assert resp.get_json()["stale"] is True


def test_csp_header_on_index_and_api(client, monkeypatch):
    """Why: a strict policy blocks injected scripts from running."""
    monkeypatch.setattr(converter, "fetch_rates", lambda force=False: _table())
    for path in ["/", "/api/convert?amount=1&from=USD&to=EUR"]:
        resp = client.get(path)
        policy = resp.headers.get("Content-Security-Policy", "")
        assert "default-src 'self'" in policy


def test_history_clamps_days(client, monkeypatch):
    """Why: the chart only supports 7 to 90 days, with 30 as the fallback."""
    seen = {}
    monkeypatch.setattr(converter, "fetch_rates", lambda force=False: _table())

    def fake_history(from_cur, to_cur, days=30):
        seen["days"] = days
        return []

    monkeypatch.setattr(converter, "fetch_history", fake_history)
    client.get("/api/history?from=USD&to=EUR&days=1")
    assert seen["days"] == 7
    client.get("/api/history?from=USD&to=EUR&days=999")
    assert seen["days"] == 90
    client.get("/api/history?from=USD&to=EUR&days=nonsense")
    assert seen["days"] == 30


def test_history_pairs_stay_isolated_through_api(client, monkeypatch):
    """Why: two pairs asked through the API must each get their own points."""
    monkeypatch.setattr(converter, "fetch_rates", lambda force=False: _table())

    def fake_collect(from_cur, to_cur, days, start):
        if (from_cur, to_cur) == ("USD", "COP"):
            return [{"date": "2024-01-01", "rate": 4000.0}]
        return [{"date": "2024-01-01", "rate": 0.9}]

    monkeypatch.setattr(converter, "_collect_history", fake_collect)
    first = client.get("/api/history?from=USD&to=COP&days=7")
    second = client.get("/api/history?from=USD&to=EUR&days=7")
    assert first.get_json()["points"] == [{"date": "2024-01-01", "rate": 4000.0}]
    assert second.get_json()["points"] == [{"date": "2024-01-01", "rate": 0.9}]
