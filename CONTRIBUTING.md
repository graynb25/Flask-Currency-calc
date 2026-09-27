# Contributing

Thanks for wanting to improve Currency Calc! This is a small, deliberately
simple project: two frontends (web and CLI) on one shared engine. The rules
below keep it that way.

## Getting set up

```bash
git clone https://github.com/graynb25/Flask-Currency-calc.git
cd Flask-Currency-calc

python -m venv .venv
# Windows:
.venv\Scripts\pip install requests flask
# macOS / Linux:
.venv/bin/pip install requests flask

# Web UI (opens http://127.0.0.1:5000):
.venv\Scripts\python app.py
# CLI:
.venv\Scripts\python main.py
```

Only `requests` and `flask` are required. Do not add dependencies for things
the standard library already covers.

## Before you open a pull request

- Keep it small: one bug fix or one feature per PR.
- Run the app and exercise your change in the UI, and in the CLI if it touches
  `converter.py`.
- Sanity checks worth running:
  - Convert in both directions (for example `100 USD to COP`, then swap) and
    confirm the round trip is exact.
  - Enter an invalid amount and an unknown currency; both should fail with a
    clean message, not a stack trace.
  - Check the browser console for JavaScript errors.
- Update `ARCHITECTURE.md` and the README if you change behavior or add an
  endpoint.

## Project rules

1. **Data sources must be free and keyless.** No APIs that require signup,
   keys, or tokens; nothing secret should ever exist in this repo.
2. **Respect upstream providers.** Read from the cache before hitting the
   network, bound parallel fetches, and put a rate limit on any new expensive
   endpoint (see `app.py`).
3. **No build step.** Vanilla JavaScript and CSS served by Flask. No bundlers,
   no frameworks, no transpiling.
4. **No em dashes in user-facing text.** Project style choice; use commas,
   semicolons, or restructure the sentence.
5. **Escape third-party data.** Anything fetched from an external API goes
   through the `esc()` helper before it touches `innerHTML`.
6. **Precision matters.** Money math is `Decimal` on the server; frontend
   formatting goes through the `Intl.NumberFormat` helpers in `app.js`. Do not
   round a carried amount to display decimals.
7. **Fail gracefully.** Every upstream call needs a timeout, a fallback, and a
   clean JSON error path. An outage should degrade the app, not crash it.

## Reporting bugs

Open an issue with:

- The currency pair and amount (for example `24.99 COP to USD`)
- What you expected and what was shown
- Whether the app was offline or rate limited
- Any server console output or browser console errors

## Style

- Python: stdlib plus `requests`/`flask`, type hints on public functions,
  docstrings that say why, not what.
- JavaScript: vanilla ES, small pure helpers, no globals beyond what `app.js`
  already defines.

## License

By contributing, you agree that your contributions are licensed under the
MIT License that covers this repository.
