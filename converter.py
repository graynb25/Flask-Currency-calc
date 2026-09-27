"""Core exchange-rate engine: live rates, caching, conversion, history.

Data sources (all free, no API key):
  * Primary rates ....... https://open.er-api.com (ExchangeRate-API open endpoint,
                          160+ currencies, refreshed daily, base USD)
  * Fallback rates ...... @fawazahmed0/currency-api via jsDelivr CDN (200+ currencies)
  * Currency names ...... @fawazahmed0/currency-api via jsDelivr CDN
  * Historical rates .... https://frankfurter.dev (European Central Bank reference
                          rates, ~30 major currencies, business days only)

Rates are mid-market reference rates: good for calculators, not quotes
you would actually get at a bank or exchange kiosk.
"""

from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import requests

CACHE_FILE = Path(__file__).parent / "rates_cache.json"
CACHE_TTL_SECONDS = 6 * 60 * 60  # the API updates daily; re-fetch every 6 h to stay fresh
HISTORY_CACHE_TTL_SECONDS = 3 * 60 * 60  # historical days never change once published
HTTP_TIMEOUT = 10

PRIMARY_API = "https://open.er-api.com/v6/latest/USD"
FALLBACK_RATES_API = (
    "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.json"
)
NAMES_API = "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies.json"
HISTORY_API = "https://api.frankfurter.dev/v1/{start}..?base={base}&symbols={symbol}"
# One snapshot per publication day; every date has its own package version.
HISTORY_DAILY_API = (
    "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{day}/v1/currencies/{base}.min.json"
)

# ISO-4217 currencies with no minor unit; display them without decimals.
ZERO_DECIMAL = {
    "BIF", "CLP", "DJF", "GNF", "JPY", "KMF", "KRW", "MGA", "PYG",
    "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF",
}

# Emoji flags for the most common currency codes; anything else falls back to 🏳️.
FLAGS = {
    "USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧", "JPY": "🇯🇵", "CAD": "🇨🇦",
    "AUD": "🇦🇺", "CHF": "🇨🇭", "CNY": "🇨🇳", "INR": "🇮🇳", "COP": "🇨🇴",
    "MXN": "🇲🇽", "BRL": "🇧🇷", "ARS": "🇦🇷", "CLP": "🇨🇱", "PEN": "🇵🇪",
    "KRW": "🇰🇷", "SGD": "🇸🇬", "HKD": "🇭🇰", "NZD": "🇳🇿", "SEK": "🇸🇪",
    "NOK": "🇳🇴", "DKK": "🇩🇰", "PLN": "🇵🇱", "CZK": "🇨🇿", "HUF": "🇭🇺",
    "RON": "🇷🇴", "TRY": "🇹🇷", "RUB": "🇷🇺", "UAH": "🇺🇦", "ZAR": "🇿🇦",
    "NGN": "🇳🇬", "EGP": "🇪🇬", "MAD": "🇲🇦", "KES": "🇰🇪", "GHS": "🇬🇭",
    "AED": "🇦🇪", "SAR": "🇸🇦", "QAR": "🇶🇦", "KWD": "🇰🇼", "ILS": "🇮🇱",
    "THB": "🇹🇭", "VND": "🇻🇳", "IDR": "🇮🇩", "MYR": "🇲🇾", "PHP": "🇵🇭",
    "PKR": "🇵🇰", "BDT": "🇧🇩", "LKR": "🇱🇰", "TWD": "🇹🇼", "BOB": "🇧🇴",
    "UYU": "🇺🇾", "PYG": "🇵🇾", "VES": "🇻🇪", "CRC": "🇨🇷", "DOP": "🇩🇴",
    "GTQ": "🇬🇹", "HNL": "🇭🇳", "NIO": "🇳🇮", "PAB": "🇵🇦", "JMD": "🇯🇲",
    "ISK": "🇮🇸", "BGN": "🇧🇬", "HRK": "🇭🇷", "RSD": "🇷🇸",
    "GEL": "🇬🇪", "AMD": "🇦🇲", "AZN": "🇦🇿", "KZT": "🇰🇿", "UZS": "🇺🇿",
    "ETB": "🇪🇹", "TZS": "🇹🇿", "UGX": "🇺🇬", "ZMW": "🇿🇲", "BWP": "🇧🇼",
    "MUR": "🇲🇺", "XCD": "🏳️", "XOF": "🏳️", "XAF": "🏳️", "XPF": "🏳️",
}


class CurrencyError(Exception):
    """Raised when rates cannot be fetched or an input is invalid."""


# --------------------------------------------------------------------------- #
# Rate fetching / caching
# --------------------------------------------------------------------------- #

def fetch_rates(force: bool = False) -> dict:
    """Return the rate cache dict, fetching live data when needed.

    Shape: {"base", "rates", "updated", "fetched_at", "source", "names", "stale"?}
    Falls back to a stale cache when every API is unreachable.
    """
    cache = _load_cache()
    if not force and cache and not _expired(cache):
        return cache

    fresh = _fetch_primary() or _fetch_fallback()
    if fresh is None:
        if cache:
            cache["stale"] = True
            return cache
        raise CurrencyError(
            "Could not reach any exchange-rate API and there is no cached copy. "
            "Check your internet connection and try again."
        )

    fresh["names"] = (cache or {}).get("names") or _fetch_names()
    _save_top(fresh)
    return fresh


def refresh_names(rates: dict) -> dict:
    """Ensure the cache carries currency names; fetch them once if missing."""
    if rates.get("names"):
        return rates
    rates["names"] = _fetch_names()
    _save_top({"names": rates["names"]})
    return rates


def _expired(cache: dict) -> bool:
    return time.time() - cache.get("fetched_at", 0) > CACHE_TTL_SECONDS


def _load_cache() -> dict | None:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_cache(cache: dict) -> None:
    try:
        CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")
    except OSError:
        pass  # caching is best-effort; live conversion still works


_CACHE_LOCK = threading.Lock()  # Flask's threaded server can write concurrently


def _save_top(update: dict) -> None:
    """Merge top-level keys into the cache without clobbering other sections:
    a rates refresh must not wipe the cached history, and vice versa."""
    with _CACHE_LOCK:
        cache = _load_cache() or {}
        cache.update(update)
        cache.pop("stale", None)  # a successful live fetch is not stale
        _save_cache(cache)


def _save_history_entry(key: str, points: list) -> None:
    with _CACHE_LOCK:
        cache = _load_cache() or {}
        cache.setdefault("history", {})[key] = {"fetched_at": time.time(), "points": points}
        _save_cache(cache)


def _fetch_primary() -> dict | None:
    try:
        payload = requests.get(PRIMARY_API, timeout=HTTP_TIMEOUT).json()
        if payload.get("result") != "success":
            return None
        return {
            "base": payload["base_code"],
            "rates": payload["rates"],
            "updated": payload["time_last_update_utc"],
            "fetched_at": time.time(),
            "source": "exchangerate-api.com (open.er-api.com)",
        }
    except (requests.RequestException, KeyError, ValueError):
        return None


def _fetch_fallback() -> dict | None:
    try:
        payload = requests.get(FALLBACK_RATES_API, timeout=HTTP_TIMEOUT).json()
        raw = payload["usd"]
        rates = {
            code.upper(): value
            for code, value in raw.items()
            if re.fullmatch(r"[a-z]{3}", code)  # keep fiat-style codes, drop crypto
        }
        rates["USD"] = 1.0
        return {
            "base": "USD",
            "rates": rates,
            "updated": f"{payload.get('date', 'unknown')} (CDN mirror)",
            "fetched_at": time.time(),
            "source": "@fawazahmed0/currency-api via jsDelivr",
        }
    except (requests.RequestException, KeyError, ValueError):
        return None


def _fetch_names() -> dict:
    """code -> full name; tolerated to fail (UIs then show codes only)."""
    try:
        payload = requests.get(NAMES_API, timeout=HTTP_TIMEOUT).json()
        return {code.upper(): name for code, name in payload.items()}
    except (requests.RequestException, ValueError):
        return {}


# --------------------------------------------------------------------------- #
# Conversion
# --------------------------------------------------------------------------- #

def get_rate(from_cur: str, to_cur: str, rates: dict) -> Decimal:
    """Units of `to_cur` per 1 unit of `from_cur`, via the USD base."""
    table = rates["rates"]
    try:
        return Decimal(str(table[to_cur])) / Decimal(str(table[from_cur]))
    except KeyError as exc:
        raise CurrencyError(f"Unsupported currency code: {exc.args[0]}") from None
    except (ZeroDivisionError, ArithmeticError) as exc:
        raise CurrencyError("Bad rate data; cannot divide.") from exc


def convert(amount: Decimal | float | str, from_cur: str, to_cur: str, rates: dict) -> Decimal:
    from_cur, to_cur = from_cur.upper(), to_cur.upper()
    try:
        qty = Decimal(str(amount))
    except ArithmeticError:
        raise CurrencyError(f"{amount!r} is not a valid number.") from None
    if not qty.is_finite():
        # Decimal happily parses "Infinity"/"NaN", which would later produce
        # non-standard JSON. Reject them here at the boundary.
        raise CurrencyError("Amount must be a finite number.")
    if qty < 0:
        raise CurrencyError("Amount must not be negative.")
    return qty * get_rate(from_cur, to_cur, rates)


def format_money(code: str, value: Decimal) -> str:
    """Human formatting used by the CLI; web UI uses Intl.NumberFormat instead."""
    digits = 0 if code in ZERO_DECIMAL else 2
    return f"{value:,.{digits}f}"


def format_rate(rate: Decimal) -> str:
    """Pick sensible precision for very large and very small rates."""
    magnitude = abs(rate)
    if magnitude >= 1:
        digits = 4
    elif magnitude >= 0.0001:
        digits = 6
    else:
        digits = 8
    return f"{rate:,.{digits}f}"


# --------------------------------------------------------------------------- #
# History (for the chart)
# --------------------------------------------------------------------------- #

def fetch_history(from_cur: str, to_cur: str, days: int = 30) -> list[dict]:
    """Daily rates for a pair over the last `days` days, oldest first.

    Tries, in order: ECB reference rates via Frankfurter (direct, then
    inverted when only one side is supported), then daily CDN snapshots,
    which cover every currency. Results are cached for a few hours.
    Returns [] only when no source has data for the pair.
    """
    from_cur, to_cur = from_cur.upper(), to_cur.upper()
    key = f"{from_cur}/{to_cur}/{days}"
    cache = _load_cache() or {}
    cached = (cache.get("history") or {}).get(key)
    if cached and time.time() - cached.get("fetched_at", 0) < HISTORY_CACHE_TTL_SECONDS:
        return cached["points"]

    start = (date.today() - timedelta(days=days)).isoformat()
    points = _fetch_history_range(from_cur, to_cur, start)
    if points is None:
        inverse = _fetch_history_range(to_cur, from_cur, start)
        if inverse is not None:
            points = [
                {"date": p["date"], "rate": (1 / p["rate"]) if p["rate"] else None}
                for p in inverse
            ]
    if not points:
        points = _fetch_history_daily(from_cur, to_cur, days)
    if points is None:
        points = []

    _save_history_entry(key, points)
    return points


def _fetch_history_daily(base: str, symbol: str, days: int) -> list[dict] | None:
    """Pull one snapshot per day (parallel) and extract the pair's rate.

    Dates without a published snapshot (or a zero rate) are skipped.
    Returns None only when no day yielded anything.
    """
    base, symbol = base.lower(), symbol.lower()
    days_list = [(date.today() - timedelta(days=i)).isoformat() for i in range(days, -1, -1)]

    def one_day(day: str) -> dict | None:
        try:
            url = HISTORY_DAILY_API.format(day=day, base=base)
            payload = requests.get(url, timeout=HTTP_TIMEOUT).json()
            rate = payload[base][symbol]
            if rate:
                return {"date": day, "rate": rate}
        except (requests.RequestException, KeyError, ValueError):
            pass
        return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        found = list(pool.map(one_day, days_list))
    points = [p for p in found if p]
    return points or None


def _fetch_history_range(base: str, symbol: str, start: str) -> list[dict] | None:
    url = HISTORY_API.format(start=start, base=base, symbol=symbol)
    try:
        resp = requests.get(url, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        daily = resp.json()["rates"]
    except (requests.RequestException, KeyError, ValueError):
        return None
    return [
        {"date": day, "rate": values[symbol]}
        for day, values in sorted(daily.items())
        if symbol in values
    ]
