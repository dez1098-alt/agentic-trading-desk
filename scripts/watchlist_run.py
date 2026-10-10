#!/usr/bin/env python3
"""
watchlist_run.py
================
One watchlist check for the Agentic desk routines (read-only: places no orders).

Inputs:
  --bars FILE ...    Robinhood get_equity_historicals results saved as JSON
                     ({data: {results: [{symbol, bars: [...]}]}}), daily interval.
                     Only sessions before --date are used, so the live price is
                     never double counted.
  --quote SYM=PRICE:PREV_CLOSE ...   live price and prior close for each name.
  --earnings SYM=YYYY-MM-DD ...      optional upcoming earnings dates.
  --tracker DIR      folder holding agentic_watch.csv and agentic_meta.json.

For every name it computes the 20-day SMA and Wilder RSI14 on completed closes,
scores it with score.py (held names are scored as holdings so EXIT signals show),
applies the desk rules (no counter-trend entries, SPY 200-day market filter,
earnings guard), sets the dashboard flag, and rewrites the tracker files.
Positions come from agentic_meta.json; a position with "core": true is held and
never flagged for exit. Use --dry-run to print without writing.
stdlib only.
"""
from __future__ import annotations
import argparse
import csv
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import score as S  # noqa: E402


def load_bars(files: list[str], before: str) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for f in files:
        with open(f) as fh:
            for r in json.load(fh)["data"]["results"]:
                out[r["symbol"]] = [float(b["close_price"]) for b in r["bars"]
                                    if b["begins_at"][:10] < before and not b.get("interpolated")]
    return out


def wilder_rsi(c: list[float], n: int = 14) -> float:
    g = [max(c[k] - c[k - 1], 0.0) for k in range(1, len(c))]
    l = [max(c[k - 1] - c[k], 0.0) for k in range(1, len(c))]
    ag, al = sum(g[:n]) / n, sum(l[:n]) / n
    for k in range(n, len(g)):
        ag = (ag * (n - 1) + g[k]) / n
        al = (al * (n - 1) + l[k]) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def trading_days_between(start: str, end: str) -> int:
    """Weekdays after `start` up to and including `end` (holidays ignored)."""
    d0, d1 = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    n, d = 0, d0
    while d < d1:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def short_action(a: str) -> str:
    for x in (" (new cycle)", " (do not chase)", " / AVOID", " (ride the cycle)"):
        a = a.replace(x, "")
    return a


def run(args) -> int:
    quotes = {}
    for x in args.quote:
        sym, v = x.split("=")
        p, pc = v.split(":")
        quotes[sym] = (float(p), float(pc))
    earnings = dict(x.split("=") for x in (args.earnings or []))
    bars = load_bars(args.bars, args.date)

    watch_path = os.path.join(args.tracker, "agentic_watch.csv")
    meta_path = os.path.join(args.tracker, "agentic_meta.json")
    with open(watch_path) as f:
        rows = list(csv.DictReader(f))
    with open(meta_path) as f:
        meta = json.load(f)
    held = {p["symbol"]: p for p in meta.get("positions", [])}

    spy = bars.get("SPY", [])
    market_ok = S.market_ok_from_spy(spy)
    lines = [f"market filter: SPY {'above' if market_ok else 'below' if market_ok is False else 'n/a vs'} 200-day"]

    for x in rows:
        s = x["symbol"]
        if s not in quotes or s not in bars:
            x["note"] = "no quote or bars this run"
            continue
        p, pc = quotes[s]
        c = bars[s]
        sma = sum(c[-20:]) / 20
        r = wilder_rsi(c)
        pos = held.get(s)
        core = bool(pos and pos.get("core"))
        dte = trading_days_between(args.date, earnings[s]) if s in earnings else None
        card = S.score_symbol(c + [p], symbol=s, holding=bool(pos) and not core,
                              market_ok=market_ok, days_to_earnings=dte)
        dec = card["decision"]
        d20 = (p / sma - 1) * 100
        x.update({
            "as_of": f"{args.date} {args.time} ET", "price": round(p, 2),
            "day_change_pct": round((p / pc - 1) * 100, 2), "sma20": round(sma, 2),
            "rsi14": round(r, 1), "dist_to_sma20_pct": round(d20, 2),
            "score": card["pillar_total"], "decision": short_action(dec["action"]),
            "note": f"scored {args.time} live ({len(c)} bars)",
        })
        if dte is not None:
            x["note"] += f"; earnings {earnings[s]} ({dte} trading days)"
        if pos:
            x["flag"] = "HOLDING"
            x["note"] += "; core, held" if core else f"; held, info level {pos.get('exit_level')}"
        elif dec["raw_action"].startswith("RE-ENTRY") and dec["action"].startswith("RE-ENTRY"):
            x["flag"] = "TRIGGER"
        elif (p / pc - 1) * 100 <= -2.5:
            x["flag"] = "BIG MOVE"
        elif r < 40:
            x["flag"] = "RSI<40"
        elif abs(d20) <= 3:
            x["flag"] = "IN ZONE"
        else:
            x["flag"] = ""
        lines.append(f"{s:5} {p:9.2f} {x['day_change_pct']:+6.2f}% d20 {d20:+6.2f} rsi {r:5.1f} "
                     f"score {card['pillar_total']:+d} {x['decision']:<28} {x['flag']}")

    for pos in meta.get("positions", []):
        if pos["symbol"] in quotes:
            pr = quotes[pos["symbol"]][0]
            pos["price"] = round(pr, 2)
            pos["pl"] = round((pr - pos["avg_cost"]) * pos["qty"], 2)
    meta["as_of"] = f"{args.date} {args.time} ET"
    meta.pop("tsla_call", None)

    if not args.dry_run:
        with open(watch_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=1)
    print("\n".join(lines))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Agentic desk watchlist check (read-only).")
    ap.add_argument("--date", required=True, help="today, YYYY-MM-DD (bars before it are used)")
    ap.add_argument("--time", required=True, help="HH:MM ET, for the as_of stamp")
    ap.add_argument("--bars", nargs="+", required=True)
    ap.add_argument("--quote", nargs="+", required=True)
    ap.add_argument("--earnings", nargs="*")
    ap.add_argument("--tracker", default="/mnt/project-files/tracker")
    ap.add_argument("--dry-run", action="store_true")
    return run(ap.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
