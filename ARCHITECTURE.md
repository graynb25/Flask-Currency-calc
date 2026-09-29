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
| `requirements.txt`  | Pinned runtime dependencies (flask, requests)                |
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
| History tier 1 | Frankfurter (ECB reference rates)        | Bounded `{start}..{end}` range, then inverted pair if the base is unsupported |
| History tier 2 | Daily CDN snapshots (`@{date}` versions) | One request per day, fetched in parallel, covers every currency |

Frankfurter's range endpoints take an explicit end date, and the open-ended
`{start}..` form is not used: it answers with a single date's snapshot rather
than a per-date map, so treating it as a range iterated currency codes as if
they were dates. `_fetch_history_range` also catches `TypeError` and ignores
non-dict entries, so a future shape change degrades into the CDN tier instead
of escaping as a 500.

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

**A failure is never cached as an answer.** `fetch_history` distinguishes two
empty results that look identical from the outside: `[]` means a source was
reached and has no data for that pair (worth remembering for the TTL), and
`None` means nobody could be reached (not worth remembering). Persisting the
second one would make a momentary network blip tell the UI "no history exists
for this pair" for the full three hours. `_collect_history` returns which of
the two it was, and only the first is written to disk.

### Upstream gate

Every network read passes through an `_Upstream` gate, which exists because the
cache is shared but nothing was coordinating the threads reading it. It does
two jobs:

- **Coalescing.** The dev server is threaded, so when the 6 h TTL lapses every
  in-flight request sees an expired cache and fires its own HTTP call. The
  first through does the work; the rest wait on a condition variable and share
  its answer instead of multiplying the load on the provider. Eight
  simultaneous cold readers make one upstream request.
- **Back-off.** A total failure is remembered in memory for
  `UPSTREAM_RETRY_SECONDS` (60 s), so an outage costs one round of timeouts per
  window rather than one per client. Without it every request paid two 10 s
  timeouts before it could fall back to the stale cache.

The state is process-local and deliberately never persisted: the file holds
only data a source actually vouched for. `bypass_backoff` is passed for an
explicit user-initiated refresh, since the per-IP rate limit, not an earlier
stranger's failure, is what should bound how often that re-dials. Rates,
history, and names each get their own gate, because a single provider being
down should not block the others.

## Conversion math

- The primary API returns one table keyed in USD. A cross rate is
  `rates[TO] / rates[FROM]`, computed with `Decimal` (28 significant digits),
  so binary float error never touches money.
- Measured guarantees: `rate(A→B) × rate(B→A)` equals 1 within 3e-28, and
  `100 A → B → A` round trips within 3e-26.

**Amounts are parsed, not just coerced.** `parse_amount()` in `converter.py`
decides which separator is the decimal point by *position*, because both
frontends take input typed by people on both sides of the Atlantic. The
obvious implementation, deleting every comma, is silently wrong: `1.234,56`
becomes 1.23456 and `1,5` becomes 15, so the converter reports amounts 1000x
and 10x off with a confident-looking answer. The rules are: both separators
present means the right-most one is the decimal point; one separator used
twice or more is grouping; one separator with exactly three digits after it is
grouping; anything else is a decimal point. The three-digit case is a genuine
coin flip for input like `1.234` and resolves the way spreadsheet and POS
software does, because in a money tool a misread grouping is the worse
failure. `app.js` mirrors this in `parseAmountText()` and the two must be
changed together.

**`Decimal` alone does not make a response safe to serialize.** `convert()`
rejects non-finite amounts, but `Decimal` has a far wider range than float64:
`Decimal("1e400")` is finite and passes, then `float()` turns it into `inf`
and `json.dumps` emits a bare `Infinity` token. That is not valid JSON, so the
browser's `JSON.parse` throws, the client sees `null`, and the result box
freezes with no error shown. `finite_float()` re-validates *after* the
narrowing, which is the only point at which the overflow is visible, and
`/api/convert` routes every value through it.
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
lock; idle buckets are pruned past 4096 IPs, and the prune runs *before* the
limit check so that a throttled client still triggers it, since its rejected
requests are what fill the dict). It is deliberately keyed on `remote_addr`
only: trusting `X-Forwarded-For` would let clients rotate fake IPs and bypass
the limit unless a trusted proxy sits in front. The limiter protects the
upstream providers from a runaway client; it is not a defence against a real
network-level DDoS.

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
- `localStorage` remembers the last pair, amount, and chart range across
  reloads. The range pills are static markup, so the active one is derived
  from state and synced on load rather than trusted from the HTML; otherwise a
  restored 90-day preference drew 90 days with 30D still highlighted.
- The result count-up cancels the previous animation frame before starting a
  new one. The input debounce (250 ms) is shorter than the animation (480 ms),
  so without that two loops run at once and the older one overwrites the newer
  one mid-flight.
- `api()` throws when a 2xx body will not parse. A response that cannot be
  read is a broken response, and returning `null` let the caller dereference it
  and freeze the UI with no explanation.

## Design decisions

- **Keyless APIs only**: no signup, no secrets in the repo, nothing to rotate.
- **One shared engine**: the CLI and web UI cannot drift apart; there is one
  place where rates, caching, and math live.
- **Merge writes with a lock** instead of rewriting the cache file: prevents
  the subtle bug where refreshing rates deleted chart history.
- **Mid-market rates**: the app converts with interbank midpoints and says so;
  it is a calculator, not a quote from a bank or kiosk.
- **One shared amount parser**: the CLI and the web input disagreeing about
  `1.234,56` would be worse than either being wrong alone, so `parse_amount()`
  is the single definition and `app.js` mirrors it.
- **Flask dev server, debug on**: this is a local learning tool. Hosting it
  publicly means turning debug off and running behind a real WSGI server plus
  a reverse proxy (see Security notes in the README).
