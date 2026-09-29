# Python Currency Calc

A currency converter that pulls **real, live exchange rates** for **160+ currencies**
(USD, EUR, CAD, COP, JPY, ...), with a modern glassmorphism web UI and a CLI.

## Run the web UI

```bat
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python app.py
```

Needs Python 3.9 or newer; the only dependencies are `flask` and `requests`.

Opens http://127.0.0.1:5000 in your browser automatically. Features:

- Live mid-market rates (auto-cached locally for 6 h, refresh button to force update)
- Convert between any of 160+ currencies, incl. multi-currency side panel
- Typable, searchable combobox currency selectors
- Currency symbols on the amount field and the result ($, €, ¥, ...)
- 7/30/90-day historical chart for every currency pair
- Popular-pair shortcuts, swap animation, animated results, copy button

## Run the CLI

```bat
.venv\Scripts\python main.py
```

```
> 100 usd to cop
  100.00 USD = 325,715 COP   (1 USD = 3,257.1500 COP)
> 1.234,56 usd to cop
  1,234.56 USD = 4,013,882 COP
> 250 eur in jpy,cad
> list cop          browse/search supported currencies
> refresh           force fresh rates
```

Amounts may use either convention: `1,234.56` and `1.234,56` both mean one
thousand two hundred thirty-four, decided from where the separators fall. The
web input and the CLI share one parser, so the two cannot drift apart.

## Data sources (all free, no API key)

| Purpose   | Source                                       | Coverage                        |
|-----------|----------------------------------------------|---------------------------------|
| Rates     | [open.er-api.com](https://www.exchangerate-api.com/docs/free) | 160+ currencies, updated daily |
| Fallback  | [@fawazahmed0/currency-api](https://github.com/fawazahmed0/exchange) via jsDelivr | 200+ currencies |
| Names     | @fawazahmed0/currency-api                    | currency code to full name      |
| History   | [frankfurter.dev](https://frankfurter.dev) (ECB) for major currencies; daily CDN snapshots for all others | any pair |

Rates are **mid-market reference rates**, the interbank midpoint. Banks, cards and
kiosks add their own margin (typically 1-4 %), so treat results as accurate
calculations, not quotes.

## Documentation

- [Architecture](ARCHITECTURE.md): how the engine, cache, API, and UI fit together
- [Contributing](CONTRIBUTING.md): setup, project rules, and PR checklist

## Security notes

- No API keys or secrets: every data source is free and keyless.
- The Flask dev server binds to `127.0.0.1` only, with debug/reloader on. The
  Werkzeug interactive debugger is **unauthenticated** and allows anyone who
  can reach the port to run code in the interpreter, so keep this on loopback.
  `app.py` prints a warning at startup; set `FLASK_DEBUG=0` to turn it off, and
  use a real WSGI server (waitress, gunicorn) plus a reverse proxy if you host
  this anywhere public.
- `/api/*` endpoints are rate limited per IP: 120/min default, 30/min for history,
  10/min for refresh. One client cannot hammer the upstream providers through this app.
- Currency names fetched from the CDN are HTML-escaped before rendering.

## Files

| File               | Purpose                                        |
|--------------------|------------------------------------------------|
| `requirements.txt` | Pinned runtime dependencies (`flask`, `requests`) |
| `converter.py`     | Rate fetching/caching, conversion math, history |
| `app.py`           | Flask backend + JSON API for the web UI         |
| `main.py`          | Interactive CLI                                 |
| `main_original.py` | The original hand-entered-rate version (backup) |
| `templates/`, `static/` | Web UI (no build step, vanilla JS)         |
| `rates_cache.json` | Local rate cache (auto-created, safe to delete) |

## License

Released under the [MIT License](LICENSE). Exchange-rate data belongs to its
providers (ExchangeRate-API, the fawazahmed0 currency-api project, and the
European Central Bank via Frankfurter).
