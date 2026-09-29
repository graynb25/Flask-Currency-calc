"""Flask backend for the Currency Converter web UI.

Run with:  python app.py   (opens http://127.0.0.1:5000 in your browser)
"""

from __future__ import annotations

import os
import threading
import time
import webbrowser
from collections import defaultdict, deque

from flask import Flask, jsonify, render_template, request

import converter

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Per-IP rate limiting on /api/*.
# Stops a runaway browser tab or script from hammering the upstream rate APIs
# through this app. It is not a defence against a real network-level DDoS;
# that is the job of a reverse proxy or hosting firewall. Deliberately keyed
# on remote_addr only: trusting X-Forwarded-For would let clients rotate
# fake IPs and bypass the limit unless a trusted proxy is in front.
# ---------------------------------------------------------------------------
RATE_WINDOW_SECONDS = 60
DEFAULT_API_LIMIT = 120          # requests per IP per window
STRICT_API_LIMITS = {            # heavier endpoints get tighter caps
    "/api/history": 30,          # may fetch ~30 upstream snapshots when cold
    "/api/refresh": 10,          # forces an upstream re-fetch
}
_api_hits: dict[str, deque] = defaultdict(deque)
_api_hits_lock = threading.Lock()


@app.before_request
def enforce_rate_limits():
    limit = 0
    if request.path.startswith("/api/"):
        limit = DEFAULT_API_LIMIT
        for prefix, strict_limit in STRICT_API_LIMITS.items():
            if request.path.startswith(prefix):
                limit = strict_limit
                break
    if not limit:
        return None
    ip = request.remote_addr or "unknown"
    now = time.monotonic()
    with _api_hits_lock:
        bucket = _api_hits[ip]
        while bucket and bucket[0] <= now - RATE_WINDOW_SECONDS:
            bucket.popleft()
        if len(_api_hits) > 4096:  # bound memory; drop idle buckets first.
            # Before the limit check, not after: a client that is being
            # throttled would otherwise never reach this, and the rejected
            # requests are exactly the ones filling the dict.
            cutoff = now - RATE_WINDOW_SECONDS
            for key in [k for k, v in _api_hits.items() if not v or v[-1] <= cutoff]:
                _api_hits.pop(key, None)
        if len(bucket) >= limit:
            resp = jsonify(
                {"error": f"Rate limit reached ({limit} requests/min). Try again shortly."}
            )
            resp.status_code = 429
            resp.headers["Retry-After"] = str(RATE_WINDOW_SECONDS)
            return resp
        bucket.append(now)
    return None


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    return resp


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/currencies")
def api_currencies():
    """Everything the UI needs to build its dropdowns and quick conversions."""
    try:
        rates = converter.refresh_names(converter.fetch_rates())
    except converter.CurrencyError as exc:
        return jsonify({"error": str(exc)}), 503
    names = rates.get("names", {})
    currencies = [
        {"code": code, "name": names.get(code, code), "flag": converter.FLAGS.get(code, "🏳️")}
        for code in sorted(rates["rates"])
    ]
    return jsonify(
        {
            "updated": rates["updated"],
            "source": rates["source"],
            "stale": rates.get("stale", False),
            "currencies": currencies,
            "rates": rates["rates"],
        }
    )


@app.route("/api/convert")
def api_convert():
    try:
        rates = converter.fetch_rates()
        # Narrow to float once, up front: the amount drives every result below,
        # and a value that cannot survive the float64 rounding must be rejected
        # before any work is done rather than halfway through the loop.
        amount_value = converter.finite_float(request.args.get("amount", "1"))
        from_cur = _currency_arg("from", rates)
        to_raw = request.args.get("to", "EUR")
        targets = [t.strip().upper() for t in to_raw.split(",") if t.strip()]
        if not targets:
            return jsonify({"error": "No target currency was given."}), 400
        results = {}
        rate_map = {}
        for target in targets:
            if target not in rates["rates"]:
                return jsonify({"error": f"Unsupported currency code: {target}"}), 400
            rate = converter.get_rate(from_cur, target, rates)
            results[target] = converter.finite_float(
                converter.convert(amount_value, from_cur, target, rates), "Converted amount"
            )
            rate_map[target] = converter.finite_float(rate, "Exchange rate")
        return jsonify(
            {
                "amount": amount_value,
                "from": from_cur,
                "results": results,
                "rates": rate_map,
                "updated": rates["updated"],
                "source": rates["source"],
            }
        )
    except (converter.CurrencyError, ArithmeticError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/history")
def api_history():
    from_cur = request.args.get("from", "USD").upper()
    to_cur = request.args.get("to", "EUR").upper()
    try:
        rates = converter.fetch_rates()  # cached; also guards against garbage codes
        if from_cur not in rates["rates"] or to_cur not in rates["rates"]:
            return jsonify({"error": "Unsupported currency code."}), 400
        try:
            days = min(max(int(request.args.get("days", 30)), 7), 90)
        except ValueError:
            days = 30
        points = converter.fetch_history(from_cur, to_cur, days)
    except converter.CurrencyError as exc:
        return jsonify({"error": str(exc)}), 503
    return jsonify({"from": from_cur, "to": to_cur, "points": points})


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    try:
        rates = converter.fetch_rates(force=True)
        return jsonify(
            {
                "updated": rates["updated"],
                "source": rates["source"],
                "stale": rates.get("stale", False),
            }
        )
    except converter.CurrencyError as exc:
        return jsonify({"error": str(exc)}), 503


def _currency_arg(name: str, rates: dict) -> str:
    code = request.args.get(name, "").upper()
    if code not in rates["rates"]:
        raise converter.CurrencyError(f"Unsupported currency code: {code or '(empty)'}")
    return code


def _open_browser() -> None:
    threading.Timer(1.2, lambda: webbrowser.open("http://127.0.0.1:5000")).start()


if __name__ == "__main__":
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        _open_browser()  # only in the reloader's parent, so it opens once
    # The Werkzeug debugger lets anyone who can reach this port run code in
    # the interpreter, with no authentication. Fine on loopback, fatal the
    # moment this is bound to anything else - so it is opt-out, not opt-in,
    # and says so on startup rather than leaving it to be discovered.
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    if debug:
        print(
            "  Flask debug mode ON - the interactive debugger is unauthenticated.\n"
            "  Keep this on loopback only, or set FLASK_DEBUG=0 to disable it."
        )
    app.run(debug=debug)
