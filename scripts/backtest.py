#!/usr/bin/env python3
"""
backtest.py
===========
Replays the desk score day by day over saved Robinhood daily bars and compares
the signals with buy-and-hold. Read-only research tool: places no orders.

Each trade buys at the next session's open after an entry signal and sells at
the next open after an exit signal (score EXIT or EXIT / TRIM). No commissions,
no slippage, whole-position trades. Use it to test a rule change before it goes
live, not as a forecast.

Usage:
  python3 scripts/backtest.py BARS.json [BARS.json ...] [--rules desk|legacy] [--warmup 220]

  desk   = rules in force since 2026-10-10 (RE-ENTRY only, SPY 200-day filter)
  legacy = rules before 2026-10-10 (RE-ENTRY and TACTICAL REBOUND, no filter)

The earnings guard is not replayed (no historical earnings dates in the bars).
stdlib only.
"""
from __future__ import annotations
import argparse
import json
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import score as S  # noqa: E402


def load(files: list[str]) -> dict[str, tuple[list[str], list[float], list[float]]]:
    data = {}
    for f in files:
        with open(f) as fh:
            for r in json.load(fh)["data"]["results"]:
                b = [x for x in r["bars"] if not x.get("interpolated")]
                data[r["symbol"]] = ([x["begins_at"][:10] for x in b],
                                     [float(x["open_price"]) for x in b],
                                     [float(x["close_price"]) for x in b])
    return data


def simulate(dates, opens, closes, warmup, rules, spy_by_date):
    trades, pos = [], None
    for i in range(warmup, len(closes) - 1):
        hist = closes[:i + 1]
        if pos is not None:
            a = S.score_symbol(hist, holding=True)["decision"]["action"]
            if a.startswith("EXIT"):
                trades.append((dates[pos[0]], dates[i + 1], opens[i + 1] / pos[1] - 1))
                pos = None
        else:
            if rules == "desk":
                spy = spy_by_date.get(dates[i])
                a = S.score_symbol(hist, holding=False,
                                   market_ok=S.market_ok_from_spy(spy) if spy else None
                                   )["decision"]["action"]
                go = a.startswith("RE-ENTRY")
            else:
                a = S.score_symbol(hist, holding=False, allow_tactical=True)["decision"]["action"]
                go = a.startswith("RE-ENTRY") or a.startswith("TACTICAL")
            if go:
                pos = (i + 1, opens[i + 1])
    if pos is not None:
        trades.append((dates[pos[0]], dates[-1] + " (open)", closes[-1] / pos[1] - 1))
    return trades


def main() -> int:
    ap = argparse.ArgumentParser(description="Backtest the desk score against buy-and-hold.")
    ap.add_argument("bars", nargs="+")
    ap.add_argument("--rules", choices=["desk", "legacy"], default="desk")
    ap.add_argument("--warmup", type=int, default=220, help="bars before the first signal (EMA200 needs ~200)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    data = load(args.bars)
    spy_by_date = {}
    if "SPY" in data:
        d, _, c = data["SPY"]
        spy_by_date = {d[i]: c[:i + 1] for i in range(len(c))}

    per, all_r = [], []
    for sym, (d, o, c) in data.items():
        if len(c) <= args.warmup + 1:
            continue
        t = simulate(d, o, c, args.warmup, args.rules, spy_by_date)
        comp = 1.0
        for x in t:
            comp *= 1 + x[2]
        bh = c[-1] / c[args.warmup] - 1
        per.append({"symbol": sym, "trades": len(t), "strategy_pct": round((comp - 1) * 100, 1),
                    "buy_hold_pct": round(bh * 100, 1)})
        all_r += [x[2] for x in t]

    summary = {
        "rules": args.rules,
        "trades": len(all_r),
        "win_rate_pct": round(100 * sum(r > 0 for r in all_r) / len(all_r), 1) if all_r else None,
        "avg_trade_pct": round(st.mean(all_r) * 100, 2) if all_r else None,
        "median_name_strategy_pct": round(st.median([p["strategy_pct"] for p in per]), 1) if per else None,
        "median_name_buy_hold_pct": round(st.median([p["buy_hold_pct"] for p in per]), 1) if per else None,
        "names_beating_buy_hold": sum(p["strategy_pct"] > p["buy_hold_pct"] for p in per),
        "names": per,
    }
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        for p in per:
            print(f"{p['symbol']:6} trades {p['trades']:3}  strategy {p['strategy_pct']:+8.1f}%  buy&hold {p['buy_hold_pct']:+8.1f}%")
        print({k: v for k, v in summary.items() if k != "names"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
