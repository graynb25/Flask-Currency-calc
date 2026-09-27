# Architecture

Python Currency Calc is a live-rate currency converter with two frontends
sharing a single engine: a Flask web app and an interactive CLI. This document
explains how the pieces fit, where the data comes from, and why the important
decisions were made.

## Big picture

```
┌─────────────────────────────────────┐
│  Browser                            │
│  static/style.css + static/app.js   │
│  vanilla JS, no build step          │
└──────────────┬──────────────────────┘
               │ JSON over HTTP (localhost)
┌──────────────▼──────────────────────┐
│  app.py (Flask)                     │
│  JSON API · rate limiter · headers  │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│  converter.py (engine)              │
│  fetch · cache · convert · history  │
└──────┬──────────────────────┬───────┘
       │                      │
       ▼                      ▼
rates_cache.json        upstream APIs
(local, generated)      (keyless, free)
```

The CLI (`main.py`) calls `converter.py` directly and skips the web layer.

## Components

| File                | Role                                                       |
|---------------------|------------------------------------------------------------|
| `converter.py`      | Engine: rate fetching, caching, conversion math, history    |
| `app.py`            | Flask backend: JSON API, rate limiting, security headers    |
| `main.py`           | Interactive CLI (conversion REPL, list, refresh)            |
| `main_original.py`  | The original learning project, kept as a reference          |
| `templates/index.html` | Single page served to the browser                        |
| `static/style.css`  | Dark "liquid glass" theme                                   |
| `static/app.js`     | Comboboxes, formatting, chart, persistence                  |
| `rates_cache.json`  | Generated local cache (gitignored, safe to delete)          |

## Data sources

All sources are free and keyless, so the repo holds no secrets.

| Purpose        | Source                                   | Notes                                    |
|----------------|------------------------------------------|------------------------------------------|
| Rates          | `open.er-api.com` (ExchangeRate-API)     | ~166 fiat currencies, updated daily, base USD |
| Rates fallback | `@fawazahmed0/currency-api` via jsDelivr | 3-letter fiat codes only (crypto dropped) |
| Currency names | Same CDN package (`currencies.json`)     | Code to full name                        |
| History tier 1 | Frankfurter (ECB reference rates)        | Direct request, then inverted pair if the base is unsupported |
| History tier 2 | Daily CDN snapshots (`@{date}` versions) | One request per day, fetched in parallel, covers every currency |

Fallback chain for a conversion session: fresh cache, then primary API, then
CDN mirror, then stale cache (flagged), and only then an error.

## Caching

Everything lives in one JSON file, `rates_cache.json`: zero dependencies,
human-inspectable, and it survives restarts.

| Section | Key                | TTL  | Notes                                        |
|---------|--------------------|------|----------------------------------------------|
| rates   | (top level)        | 6 h  | The upstream table updates daily             |
| history | `FROM/TO/DAYS`     | 3 h  | Past days never change; today arrives with the next snapshot |
| names   | (top level)        | none | Currency names are effectively static        |

Writes are merge-based under a `threading.Lock` (`_save_top`,
`_save_history_entry`), so a rates refresh never clobbers cached history and
vice versa. This matters because Flask's threaded dev server can issue
concurrent writes. When every upstream fails but a cache exists, stale data is
served with a `stale: true` flag, which the UI shows as an amber status pill.

## Conversion math

- The primary API returns one table keyed in USD. A cross rate is
  `rates[TO] / rates[FROM]`, computed with `Decimal` (28 significant digits),
  so binary float error never touches money.
- Measured guarantees: `rate(A→B) × rate(B→A)` equals 1 within 3e-28, and
  `100 A → B → A` round trips within 3e-26.
- Display rules: currencies without minor units (JPY, KRW, ...) render without
  decimals; values below one hundredth of a unit render with up to 8 decimals
  so tiny conversions stay readable instead of collapsing to "$0.01".
- Swapping carries the converted value into the amount box at 15 significant
  digits (the practical precision of float64), so swap round trips are exact.

## Web API

| Endpoint          | Method | Purpose                              | Rate limit |
|-------------------|--------|--------------------------------------|------------|
| `/`               | GET    | Web UI                               | none       |
| `/api/currencies` | GET    | Rate table, names, flags, status     | 120/min    |
| `/api/convert`    | GET    | Authoritative conversion (Decimal)   | 120/min    |
| `/api/history`    | GET    | Daily points for a pair (7..90 days) | 30/min     |
| `/api/refresh`    | POST   | Force an upstream re-fetch           | 10/min     |

Errors are always JSON: `{"error": "..."}` with status 400 (bad input),
429 (rate limited, includes `Retry-After`), or 503 (upstream down and no cache).

## Rate limiting

A sliding 60-second window per IP, in-process (a dict of deques guarded by a
lock; idle buckets are pruned past 4096 IPs). It is deliberately keyed on
`remote_addr` only: trusting `X-Forwarded-For` would let clients rotate fake
IPs and bypass the limit unless a trusted proxy sits in front. The limiter
protects the upstream providers from a runaway client; it is not a defence
against a real network-level DDoS.

## Frontend

- No build step: vanilla JavaScript plus Chart.js and fonts from CDNs, served
  by Flask. Open `app.py` and it works.
- `createCombobox()` builds a typable, scrollable, keyboard-accessible selector;
  one factory instantiates both. Third-party names are HTML-escaped before
  touching `innerHTML`.
- Formatting goes through `Intl.NumberFormat`: per-currency symbols
  (`narrowSymbol`), zero-decimal handling, and the sub-cent widening above.
- Conversions are authoritative from `/api/convert`; the "Also worth knowing"
  cards are computed client-side from the rate table so they update instantly.
- The history chart guards against stale responses: each fetch carries a
  pair/range key, and a late reply for an old pair is discarded.
- `localStorage` remembers the last pair and amount across reloads.

## Design decisions

- **Keyless APIs only**: no signup, no secrets in the repo, nothing to rotate.
- **One shared engine**: the CLI and web UI cannot drift apart; there is one
  place where rates, caching, and math live.
- **Merge writes with a lock** instead of rewriting the cache file: prevents
  the subtle bug where refreshing rates deleted chart history.
- **Mid-market rates**: the app converts with interbank midpoints and says so;
  it is a calculator, not a quote from a bank or kiosk.
- **Flask dev server, debug on**: this is a local learning tool. Hosting it
  publicly means turning debug off and running behind a real WSGI server plus
  a reverse proxy (see Security notes in the README).
