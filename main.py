"""Python Currency Calc (CLI).

Interactive command-line converter built on the same live-rate engine
(`converter.py`) that powers the web UI in `app.py`.

Usage:  python main.py
"""

import difflib
import re
from decimal import Decimal

from converter import (
    CurrencyError,
    convert,
    fetch_rates,
    format_money,
    format_rate,
    get_rate,
)

CONVERSION_RE = re.compile(
    r"^\s*(?P<amount>[\d.,]+)?\s*(?P<from>[A-Za-z]{3})\s*"
    r"(?:to|->|→|in|=)\s*(?P<to>[A-Za-z]{3}(?:\s*,\s*[A-Za-z]{3})*)\s*$"
)

BANNER = """
╔══════════════════════════════════════════════════════╗
║        Python Currency Calc  ·  live rates           ║
╚══════════════════════════════════════════════════════╝"""

HELP = """\
  100 usd to cop          convert 100 US dollars to Colombian pesos
  250 eur in jpy,cad      one amount into several currencies at once
  list eur                show supported currencies matching "eur"
  refresh                 force a fresh download of the rate table
  help                    show this help
  quit                    exit"""


def suggest(code: str, names: dict[str, str]) -> str:
    """Closest matching currency codes for a typo'd input."""
    candidates = list(names) + [n.lower() for n in names.values()]
    matches = difflib.get_close_matches(code.lower(), candidates, n=3, cutoff=0.6)
    return ", ".join(m.upper() for m in matches) or "try `list` to browse codes"


def show_list(query: str, rates: dict, names: dict[str, str]) -> None:
    codes = sorted(rates["rates"])
    hits = [
        code for code in codes
        if not query
        or query in code.lower()
        or query in names.get(code, "").lower()
    ]
    if not hits:
        print(f"No currency matches {query!r}.")
        return
    print(f"{len(hits)} supported currencies" + (f" matching {query!r}:" if query else ":"))
    for i in range(0, len(hits), 3):
        row = hits[i:i + 3]
        print("   " + "".join(f"{c}  {(names.get(c, '')[:26]):<28}" for c in row))


def do_conversion(text: str, rates: dict, names: dict[str, str]) -> None:
    match = CONVERSION_RE.match(text)
    if not match:
        print("Didn't catch that. Example:  100 usd to cop   (or `help`)")
        return

    amount_text, from_cur, targets = match.group("amount", "from", "to")
    from_cur = from_cur.upper()
    if from_cur not in rates["rates"]:
        print(f"Unknown currency {from_cur}. Did you mean: {suggest(from_cur, names)}?")
        return

    if not amount_text:
        try:
            amount_text = input("  Amount: ")
        except EOFError:
            return
        match2 = re.match(r"^\s*([\d.,]+)\s*$", amount_text)
        if not match2:
            print("That's not a number.")
            return
        amount_text = match2.group(1)

    try:
        amount = Decimal(amount_text.replace(",", ""))
        for target in (t.strip().upper() for t in targets.split(",")):
            result = convert(amount, from_cur, target, rates)
            rate = get_rate(from_cur, target, rates)
            print(
                f"  {format_money(from_cur, amount)} {from_cur}"
                f" = {format_money(target, result)} {target}"
                f"   (1 {from_cur} = {format_rate(rate)} {target})"
            )
    except CurrencyError as exc:
        print(f"  {exc}")
    except ArithmeticError:
        print("  That amount doesn't look like a number.")


def main() -> None:
    print(BANNER)
    try:
        rates = fetch_rates()
    except CurrencyError as exc:
        print(f"Error: {exc}")
        return
    rates["names"] = rates.get("names", {})
    names = rates["names"]
    print(f"  {len(rates['rates'])} currencies · updated {rates['updated']} · {rates['source']}")
    print(HELP)

    while True:
        try:
            text = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            return

        if not text:
            continue
        command = text.lower()
        if command in {"quit", "exit", "q"}:
            print("Bye!")
            return
        if command in {"help", "?"}:
            print(HELP)
            continue
        if command.startswith("list"):
            show_list(command[4:].strip(), rates, names)
            continue
        if command in {"refresh", "r"}:
            try:
                rates = fetch_rates(force=True)
                rates["names"] = rates.get("names", {})
                names = rates["names"]
                print(f"  Refreshed · updated {rates['updated']} · {rates['source']}")
            except CurrencyError as exc:
                print(f"  {exc}")
            continue
        do_conversion(text, rates, names)


if __name__ == "__main__":
    main()
