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
import math
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
# After every upstream source fails, refuse to re-dial for this long. Without
# it each request pays two 10 s timeouts before it can fall back to the cache,
# so a network outage turns the app into a request flood that is also slow.
UPSTREAM_RETRY_SECONDS = 60

PRIMARY_API = "https://open.er-api.com/v6/latest/USD"
FALLBACK_RATES_API = (
    "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.json"
)
NAMES_API = "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies.json"
HISTORY_API = "https://api.frankfurter.dev/v1/{start}..{end}?base={base}&symbols={symbol}"
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
# Upstream fetch gate
# --------------------------------------------------------------------------- #

class _Upstream:
    """A one-shot gate in front of an upstream fetch.

    It does two jobs that both come from the same weakness: the rate cache is
    shared but nothing coordinates the threads reading it.

    * Coalescing. The dev server is threaded, so when the 6 h TTL lapses every
      in-flight request sees an expired cache and fires its own HTTP call. The
      first one through does the work; the rest wait and share its answer
      instead of doubling the load on the provider.
    * Back-off. A total failure is remembered in memory for
      `retry_seconds`, so an outage costs one round of timeouts per window
      rather than one per client. Deliberately process-local: the file cache
      only ever holds data a source actually vouched for.
    """

    def __init__(self, retry_seconds: float = UPSTREAM_RETRY_SECONDS) -> None:
        self._cv = threading.Condition()
        self._retry_seconds = retry_seconds
        self._busy = False
        self._failed_at = 0.0
        self._result: object | None = None
        self._succeeded = False

    def run(self, fetch, bypass_backoff: bool = False):
        """Call `fetch` at most once across concurrent callers.

        Returns `(result, ok)`. `ok` is True when `result` is live data —
        either our own, or the leader's that we waited for. `ok` is False when
        nobody tried, either because a recent failure says not to bother or
        because the leader failed; the caller must then fall back to its cache
        rather than treat the empty result as an answer.
        """
        with self._cv:
            if self._busy:
                leader = False
            elif bypass_backoff:
                # An explicit user-initiated refresh should always re-dial;
                # the per-IP rate limit is what bounds how often that can
                # happen, not the failure of somebody else's earlier attempt.
                self._failed_at = 0.0
                self._busy = True
                leader = True
            elif self._failed_at and time.monotonic() - self._failed_at < self._retry_seconds:
                return None, False  # recent failure: fail fast on stale data
            else:
                self._busy = True
                leader = True

        if leader:
            try:
                result = fetch()
            except BaseException:
                with self._cv:
                    self._result, self._succeeded = None, False
                    self._failed_at = time.monotonic()
                    self._busy = False
                    self._cv.notify_all()
                raise
            with self._cv:
                self._result, self._succeeded = result, result is not None
                if result is None:
                    self._failed_at = time.monotonic()
                self._busy = False
                self._cv.notify_all()
            return result, True

        with self._cv:  # a peer is already asking; wait for its verdict
            while self._busy:
                self._cv.wait()
            # Copy so followers cannot mutate the leader's dict underneath it.
            return (dict(self._result) if isinstance(self._result, dict) else self._result), self._succeeded


_RATES_GATE = _Upstream()
_HISTORY_GATE = _Upstream()
_NAMES_GATE = _Upstream()


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

    fresh, ok = _RATES_GATE.run(_fetch_fresh_rates, bypass_backoff=force)
    if not ok:
        if cache:
            cache["stale"] = True
            return cache
        raise CurrencyError(
            "Could not reach any exchange-rate API and there is no cached copy. "
            "Check your internet connection and try again."
        )
    return fresh


def _fetch_fresh_rates() -> dict | None:
    """One complete refresh: rates, names, and the merged cache write.

    Lives inside the gate so that concurrent callers share a single upstream
    round trip instead of each fetching names and rewriting the file.
    """
    fresh = _fetch_primary() or _fetch_fallback()
    if fresh is None:
        return None
    fresh["names"] = (_load_cache() or {}).get("names") or _fetch_names()
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
    names, ok = _NAMES_GATE.run(_fetch_names_uncached)
    return names if ok and names else {}


def _fetch_names_uncached() -> dict | None:
    try:
        payload = requests.get(NAMES_API, timeout=HTTP_TIMEOUT).json()
        return {code.upper(): name for code, name in payload.items()}
    except (requests.RequestException, ValueError):
        return None


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


def parse_amount(text: str) -> Decimal:
    """Parse a human-typed amount, tolerating `1,234.56` and `1.234,56`.

    Delimiters are a convention, not data, and both frontends get input from
    people on both sides of the Atlantic. The obvious implementation - delete
    every comma - is silently wrong: "1.234,56" becomes 1.23456 and "1,5"
    becomes 15, so the converter reports amounts 1000x and 10x off with a
    confident-looking answer.

    So decide from position, not from character:

    * both separators present: the right-most one is the decimal point and the
      other is grouping, whatever it looks like;
    * one separator, used twice or more: grouping (1.234.567);
    * one separator with exactly three digits after it: grouping (1,234);
    * anything else: a decimal point (1,5 / 1.5 / 0.0076722).

    The three-digit rule is a genuine coin flip for input like "1.234" and is
    resolved the way spreadsheet and POS software resolve it: in a money tool,
    "1,234" is far more often meant as one thousand two hundred thirty-four.
    """
    if not isinstance(text, str):
        text = str(text)
    # Spaces are groupers in several locales, and a non-breaking space is what
    # a pasted value usually carries.
    cleaned = re.sub(r"[\s\u00a0\u202f\u2009]+", "", text)
    if not cleaned:
        raise CurrencyError("Enter an amount.")

    exponent = 0
    mantissa = re.fullmatch(r"([+-]?)([\d.,]+)[eE]([+-]?\d+)", cleaned)
    if mantissa:
        cleaned = mantissa.group(1) + mantissa.group(2)
        exponent = int(mantissa.group(3))

    sign = ""
    if cleaned[:1] in "+-":
        sign, cleaned = cleaned[0], cleaned[1:]
    if not cleaned or not re.fullmatch(r"[\d.,]+", cleaned):
        raise CurrencyError(f"{text!r} is not a valid number.")

    normalized = _normalize_digits(cleaned)
    if not normalized:
        raise CurrencyError(f"{text!r} is not a valid number.")
    try:
        value = Decimal(f"{sign}{normalized}")
    except ArithmeticError:
        raise CurrencyError(f"{text!r} is not a valid number.") from None
    if exponent:
        value = value.scaleb(exponent)
    if not value.is_finite():
        raise CurrencyError("Amount must be a finite number.")
    return value


def _normalize_digits(body: str) -> str:
    """Rewrite a mixed-separator number as plain `[-]ddd[.ddd]`, or "" if bad."""
    dots, commas = body.count("."), body.count(",")

    if dots and commas:
        point = "." if body.rfind(".") > body.rfind(",") else ","
        grouping = "," if point == "." else "."
        head, _, tail = body.rpartition(point)
        head = head.replace(grouping, "")
        if not head.isdigit() or not tail.isdigit():
            return ""
        return f"{head}.{tail}"

    sep = "." if dots else ("," if commas else "")
    if not sep:
        return body if body.isdigit() else ""
    parts = body.split(sep)
    if len(parts) > 2:  # 1.234.567 -> grouping throughout
        if not parts[0].isdigit() or not all(len(p) == 3 and p.isdigit() for p in parts[1:]):
            return ""
        return "".join(parts)
    if len(parts) != 2:
        return ""
    head, tail = parts
    if not tail:
        # "100." is a normal thing to have typed halfway through an edit;
        # "100," is not a number anyone means.
        return head if (sep == "." and head.isdigit()) else ""
    if not tail.isdigit():
        return ""
    if not head:
        # ".5" is an ordinary decimal; ",5" is not a number anyone means.
        return f"0.{tail}" if sep == "." else ""
    if len(tail) == 3:  # 1,234 -> grouping
        return head + tail
    return f"{head}.{tail}"  # 1,5 / 1.5 / 0.0076722


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


def finite_float(value, label: str = "Amount") -> float:
    """Narrow a Decimal to float for JSON, refusing to lose the check.

    `convert` validates the *Decimal*, but Decimal has a far wider range than
    float64: Decimal("1e400") is finite and passes, then float() turns it into
    inf and json.dumps emits a bare `Infinity` token. That is not valid JSON,
    so the browser's JSON.parse throws and the client sees a response it cannot
    read at all. Re-checking after the narrowing is the only place the overflow
    is visible.
    """
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CurrencyError(f"{label} is not a valid number.") from exc
    if not math.isfinite(number):
        raise CurrencyError(f"{label} is too large to convert exactly.")
    return number


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
    Returns [] when no source has data for the pair, and also when no source
    could be reached - the two are kept apart internally, because only the
    first is an answer worth remembering.
    """
    from_cur, to_cur = from_cur.upper(), to_cur.upper()
    key = f"{from_cur}/{to_cur}/{days}"
    cache = _load_cache() or {}
    cached = (cache.get("history") or {}).get(key)
    if cached and time.time() - cached.get("fetched_at", 0) < HISTORY_CACHE_TTL_SECONDS:
        return cached["points"]

    start = (date.today() - timedelta(days=days)).isoformat()
    points, ok = _HISTORY_GATE.run(
        lambda: _collect_history(from_cur, to_cur, days, start)
    )
    if not ok or points is None:
        # Every source was unreachable. Persisting this would tell the client
        # "no history exists for this pair" for the full 3 h TTL, so a laptop
        # that briefly lost wifi would keep showing an empty chart long after
        # it was back online. Serve the empty list, remember nothing.
        return []
    _save_history_entry(key, points)
    return points


def _collect_history(from_cur: str, to_cur: str, days: int, start: str) -> list[dict] | None:
    """Best points available; None when no source could be reached at all.

    [] and None mean different things and callers depend on the difference:
    [] is a source vouching that it has nothing, None is nobody answering.
    """
    points = _fetch_history_range(from_cur, to_cur, start)
    answered = points is not None
    if points is None:
        inverse = _fetch_history_range(to_cur, from_cur, start)
        if inverse is not None:
            answered = True
            points = _invert(inverse)
    if not points:
        daily = _fetch_history_daily(from_cur, to_cur, days)
        if daily is not None:
            answered = True
            points = daily
    return points if answered else None


def _invert(points: list[dict]) -> list[dict]:
    """Flip a to/from series into a from/to one, keeping dates aligned."""
    return [
        {"date": p["date"], "rate": (1 / p["rate"]) if p["rate"] else None}
        for p in points
    ]


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
    """Daily rates for one pair, or None when Frankfurter cannot serve it.

    The end date must be explicit. Frankfurter's open-ended `{start}..` form
    answers with a *single* date's snapshot - `{"date": ..., "rates":
    {"EUR": 0.9}}` - instead of the per-date map a range returns. Treating
    that as a range used to iterate the currency codes as if they were dates
    and raise `TypeError: argument of type 'float' is not iterable`, which
    nothing caught, so a shape mismatch upstream turned into a 500 rather
    than a fallback.
    """
    end = date.today().isoformat()
    url = HISTORY_API.format(start=start, end=end, base=base, symbol=symbol)
    try:
        resp = requests.get(url, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        daily = resp.json()["rates"]
        return [
            {"date": day, "rate": values[symbol]}
            for day, values in sorted(daily.items())
            if isinstance(values, dict) and symbol in values
        ]
    except (requests.RequestException, KeyError, TypeError, ValueError):
        # Any unexpected shape reads as "this source cannot answer", so the
        # caller moves on to the next tier instead of 500-ing.
        return None
