"""Tests for history gate coalescing, backoff sharing, and registry bounds.

Why: the gate exists because the threaded server fires parallel upstream
reads, so without coalescing one slow cache expiry multiplies provider
load, and without the registry bound distinct pairs leak memory.
"""

import threading
import time

import pytest

import converter


def test_same_key_coalesces_to_one_call(monkeypatch):
    """Why: overlapping readers for one pair must share a single dial."""
    calls = []
    entered = threading.Event()
    release = threading.Event()

    def fake_collect(from_cur, to_cur, days, start):
        calls.append(1)
        entered.set()
        assert release.wait(timeout=5)
        return [{"date": "2024-01-01", "rate": 1.5}]

    monkeypatch.setattr(converter, "_collect_history", fake_collect)
    results = []
    errors = []

    def worker():
        try:
            results.append(converter.fetch_history("USD", "EUR", 30))
        except Exception as exc:  # keep thread errors visible to the test
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for thread in threads:
        thread.start()
    assert entered.wait(timeout=5)
    time.sleep(0.2)
    release.set()
    for thread in threads:
        thread.join(timeout=5)
    assert not errors
    assert len(results) == 5
    assert len(calls) == 1
    assert all(item == results[0] for item in results)


def test_different_keys_are_isolated(monkeypatch):
    """Why: a shared gate once handed one pair the other pair's points."""
    def fake_collect(from_cur, to_cur, days, start):
        time.sleep(0.2)
        if (from_cur, to_cur) == ("USD", "COP"):
            return [{"date": "2024-01-01", "rate": 4000.0}]
        if (from_cur, to_cur) == ("USD", "EUR"):
            return [{"date": "2024-01-01", "rate": 0.9}]
        return []

    monkeypatch.setattr(converter, "_collect_history", fake_collect)
    results = {}

    def worker(from_cur, to_cur):
        results[(from_cur, to_cur)] = converter.fetch_history(from_cur, to_cur, 30)

    first = threading.Thread(target=worker, args=("USD", "COP"))
    second = threading.Thread(target=worker, args=("USD", "EUR"))
    first.start()
    second.start()
    first.join(timeout=5)
    second.join(timeout=5)
    assert results[("USD", "COP")] == [{"date": "2024-01-01", "rate": 4000.0}]
    assert results[("USD", "EUR")] == [{"date": "2024-01-01", "rate": 0.9}]


def test_failure_suppresses_distinct_key(monkeypatch):
    """Why: one outage must cost one round of timeouts, not one per pair."""
    calls = []

    def fake_none(from_cur, to_cur, days, start):
        calls.append((from_cur, to_cur))
        return None

    monkeypatch.setattr(converter, "_collect_history", fake_none)
    assert converter.fetch_history("USD", "EUR", 30) == []
    assert len(calls) == 1
    assert converter.fetch_history("USD", "COP", 30) == []
    assert len(calls) == 1


def test_bypass_backoff_redials():
    """Why: an explicit refresh must re-dial even inside the retry window."""
    gate = converter._Upstream(backoff=converter._Backoff(retry_seconds=60))

    def boom():
        raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        gate.run(boom)

    called = []

    def fetch():
        called.append(1)
        return {"ok": True}

    result, ok = gate.run(fetch)
    assert (result, ok) == (None, False)
    assert called == []
    result, ok = gate.run(fetch, bypass_backoff=True)
    assert ok is True
    assert result == {"ok": True}
    assert len(called) == 1


def test_empty_list_cached_none_not(monkeypatch):
    """Why: a source saying no data is an answer, silence is not."""
    import json as jsonlib

    calls = []

    def fake_empty(from_cur, to_cur, days, start):
        calls.append(1)
        return []

    monkeypatch.setattr(converter, "_collect_history", fake_empty)
    assert converter.fetch_history("USD", "EUR", 30) == []
    assert converter.fetch_history("USD", "EUR", 30) == []
    assert len(calls) == 1
    cached = jsonlib.loads(converter.CACHE_FILE.read_text(encoding="utf-8"))
    assert cached["history"]["USD/EUR/30"]["points"] == []

    calls.clear()

    def fake_none(from_cur, to_cur, days, start):
        calls.append(1)
        return None

    monkeypatch.setattr(converter, "_collect_history", fake_none)
    converter._HISTORY_BACKOFF.reset()
    assert converter.fetch_history("USD", "COP", 30) == []
    converter._HISTORY_BACKOFF.reset()
    assert converter.fetch_history("USD", "COP", 30) == []
    assert len(calls) == 2


def test_registry_stays_bounded(monkeypatch):
    """Why: unbounded per-key gates would grow with every pair ever asked."""
    monkeypatch.setattr(converter, "_HISTORY_GATE_IDLE_SECONDS", 0)
    monkeypatch.setattr(
        converter,
        "_collect_history",
        lambda f, t, d, s: [{"date": "2024-01-01", "rate": 1.0}],
    )
    total = converter._HISTORY_GATES_MAX + 10
    for i in range(total):
        converter.fetch_history("AA%03d" % i, "BB%03d" % i, 30)
    assert len(converter._HISTORY_GATES) <= converter._HISTORY_GATES_MAX
    first = converter.fetch_history("AA000", "BB000", 30)
    last = converter.fetch_history(
        "AA%03d" % (total - 1), "BB%03d" % (total - 1), 30
    )
    assert first == [{"date": "2024-01-01", "rate": 1.0}]
    assert last == [{"date": "2024-01-01", "rate": 1.0}]


def test_busy_gate_survives_prune_and_follower_gets_answer(monkeypatch):
    """Why: pruning must never break a wait already in progress."""
    monkeypatch.setattr(converter, "_HISTORY_GATE_IDLE_SECONDS", 0)
    entered = threading.Event()
    release = threading.Event()

    def fake_block(from_cur, to_cur, days, start):
        if (from_cur, to_cur) == ("USD", "EUR"):
            entered.set()
            assert release.wait(timeout=5)
            return [{"date": "2024-01-01", "rate": 0.9}]
        return [{"date": "2024-01-01", "rate": 1.0}]

    monkeypatch.setattr(converter, "_collect_history", fake_block)
    results = []

    def leader():
        results.append(converter.fetch_history("USD", "EUR", 30))

    def follower():
        results.append(converter.fetch_history("USD", "EUR", 30))

    first = threading.Thread(target=leader)
    first.start()
    assert entered.wait(timeout=5)
    time.sleep(0.1)
    second = threading.Thread(target=follower)
    second.start()
    time.sleep(0.2)
    for i in range(converter._HISTORY_GATES_MAX + 10):
        converter._history_gate("X%03d/Y%03d/30" % (i, i))
    assert "USD/EUR/30" in converter._HISTORY_GATES
    release.set()
    first.join(timeout=5)
    second.join(timeout=5)
    assert len(results) == 2
    assert results[0] == results[1] == [{"date": "2024-01-01", "rate": 0.9}]
