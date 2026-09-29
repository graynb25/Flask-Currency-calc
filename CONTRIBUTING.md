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
.venv\Scripts\pip install -r requirements.txt
# macOS / Linux:
.venv/bin/pip install -r requirements.txt

# Web UI (opens http://127.0.0.1:5000):
.venv\Scripts\python app.py
# CLI:
.venv\Scripts\python main.py
```

Python 3.9 or newer. Only `flask` and `requests` are required, pinned in
`requirements.txt`. Do not add dependencies for things the standard library
already covers; if you do add one, add it to that file too.

If `.venv\Scripts\python.exe` reports that it cannot find its base
interpreter, the venv outlived the Python it was built against. Recreate it in
place; the base interpreter is all that is missing:

```bat
rmdir /s /q .venv
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

The CLI reconfigures its own output to UTF-8 on startup, so the box-drawing
banner renders on a default Windows console (`cp1252`) without a code page
change. If you add non-ASCII text to it, keep that `reconfigure` call.

## Before you open a pull request

- Keep it small: one bug fix or one feature per PR.
- Run the app and exercise your change in the UI, and in the CLI if it touches
  `converter.py`.
- Sanity checks worth running:
  - Convert in both directions (for example `100 USD to COP`, then swap) and
    confirm the round trip is exact.
  - Try both amount conventions, `1,234.56` and `1.234,56`, and confirm they
    give the *same* answer. A parser change that treats one as a thousands
    separator is a 1000x error that looks like a plausible result.
  - Ask for an amount the float64 range cannot hold (`amount=1e400`) and confirm
    a clean 400, never a 200 whose body is not valid JSON.
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
   round a carried amount to display decimals. Re-check finiteness *after*
   narrowing to `float` for JSON: `Decimal` accepts ranges float64 cannot
   hold, and an `Infinity` token is not valid JSON, so a response carrying one
   is unreadable rather than merely imprecise.
7. **Fail gracefully.** Every upstream call needs a timeout, a fallback, and a
   clean JSON error path. An outage should degrade the app, not crash it.
   Network reads go through the `_Upstream` gate, and a failure must never be
   cached as though a source had answered.
8. **One definition of user input.** A parser shared by the CLI and the web
   input lives in `converter.py`; if you change it, change `parseAmountText()`
   in `app.js` in the same PR.

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
