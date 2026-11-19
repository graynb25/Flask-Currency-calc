"""Shared fixtures: keep tests offline and isolated from real state.

Why: the engine talks to live providers and shares process-global gates,
so without these guards one test could dial the network, read the real
cache file, or inherit backoff from another test and turn green by luck.
"""

import pytest
import requests

import app as flask_app
import converter


@pytest.fixture(autouse=True)
def _offline_and_isolated(tmp_path, monkeypatch):
    """Block the network and point the cache and gates at fresh state.

    Why: offline tests must fail loudly if they touch the network, and the
    real rates_cache.json must never be read or written by the suite.
    """
    def _blocked(*args, **kwargs):
        raise AssertionError("network disabled in tests, stub explicitly")

    monkeypatch.setattr(requests, "get", _blocked)
    try:
        monkeypatch.setattr(requests.sessions.Session, "get", _blocked)
    except AttributeError:
        pass

    monkeypatch.setattr(converter, "CACHE_FILE", tmp_path / "rates_cache.json")

    converter._RATES_GATE = converter._Upstream()
    converter._NAMES_GATE = converter._Upstream()
    converter._HISTORY_BACKOFF = converter._Backoff()
    converter._HISTORY_GATES.clear()
    converter._HISTORY_GATE_TOUCHED.clear()

    flask_app._api_hits.clear()
    yield
    flask_app._api_hits.clear()
    converter._HISTORY_GATES.clear()
    converter._HISTORY_GATE_TOUCHED.clear()
