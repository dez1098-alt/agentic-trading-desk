#!/usr/bin/env python3
"""
covered_call.py
===============

Read-only calculator for an EXISTING short covered call: what it costs to buy
it back, what a roll to another strike/expiry nets, and the result if assigned
versus if rolled.

It only does arithmetic on numbers you pass in (quotes you fetched from
Robinhood). It places, prepares or previews no orders; options orders are
always placed by the user, never by the desk.

Input JSON (prices are per share, as quoted; one contract = 100 shares):
  {
    "symbol": "TSLA",
    "underlying_price": 372.0,        # current price of the stock
    "strike": 375.0,                  # the short call you hold
    "expiry": "2026-10-09",
    "premium_collected": 7.15,        # per share, when you sold it
    "contracts": 2,
    "shares": 200,                    # shares you own
    "cost_basis": 300.0,              # per share
    "buyback_ask": 5.0,               # ask of the CURRENT call, per share
    "roll": {                         # optional: the call you would sell instead
      "strike": 385.0,
      "expiry": "2026-10-16",
      "bid": 9.0                      # bid of the NEW call, per share
    }
  }

Conservative fills: buy back at the ask, sell the new call at the bid.
Ignores commissions, fees and taxes. A "result" is dollars versus your cost
basis, including all premium kept so far; it is not a prediction.

stdlib only. Python 3.9+.
"""

from __future__ import annotations
import argparse
import json
import sys

MULT = 100


def _num(raw: dict, key: str, minimum: float = 0.0) -> float:
    if key not in raw:
        raise ValueError(f"missing field: {key}")
    v = float(raw[key])
    if v < minimum:
        raise ValueError(f"{key} must be >= {minimum}")
    return v


def analyze(raw: dict) -> dict:
    price = _num(raw, "underlying_price")
    strike = _num(raw, "strike")
    premium = _num(raw, "premium_collected")
    contracts = int(_num(raw, "contracts", 1))
    shares = _num(raw, "shares")
    basis = _num(raw, "cost_basis")
    ask = _num(raw, "buyback_ask")
    covered = contracts * MULT

    warnings: list[str] = []
    if shares < covered:
        warnings.append(f"only {shares:g} shares for {contracts} contract(s) ({covered} needed): call is not fully covered")

    premium_total = premium * covered
    buyback_cost = ask * covered
    itm = price > strike

    # Close it now without rolling: keep the shares, premium minus buy-back.
    close_net = premium_total - buyback_cost

    # Let it be assigned at the current strike.
    assigned = {
        "shares_sold": covered,
        "proceeds": strike * covered,
        "share_gain_vs_basis": (strike - basis) * covered,
        "premium_kept": premium_total,
        "total_result": (strike - basis) * covered + premium_total,
        "upside_given_up_vs_now": max(price - strike, 0.0) * covered,
    }
    if strike < basis:
        warnings.append("strike is below cost basis: assignment would lock in a loss on the shares")

    out = {
        "symbol": raw.get("symbol"),
        "expiry": raw.get("expiry"),
        "underlying_price": price,
        "strike": strike,
        "moneyness": "ITM" if itm else ("ATM" if price == strike else "OTM"),
        "contracts": contracts,
        "premium_collected_total": premium_total,
        "buyback_cost": buyback_cost,
        "close_now_net_premium": close_net,
        "if_assigned": assigned,
        "roll": None,
        "warnings": warnings,
    }

    r = raw.get("roll")
    if r:
        new_strike = _num(r, "strike")
        new_bid = _num(r, "bid")
        net_per_share = new_bid - ask
        net_total = net_per_share * covered
        banked = premium_total - buyback_cost + new_bid * covered  # all premium net of buy-back
        rolled_assigned = (new_strike - basis) * covered + banked
        if new_strike < basis:
            warnings.append("roll strike is below cost basis: assignment would lock in a loss on the shares")
        if net_total < 0:
            warnings.append(f"this roll is a net DEBIT of ${-net_total:,.2f}")
        if r.get("expiry") and raw.get("expiry") and r["expiry"] <= raw["expiry"]:
            warnings.append("roll expiry is not later than the current expiry")
        out["roll"] = {
            "strike": new_strike,
            "expiry": r.get("expiry"),
            "new_bid": new_bid,
            "net_per_share": net_per_share,
            "net_total": net_total,
            "kind": "credit" if net_total > 0 else ("debit" if net_total < 0 else "even"),
            "premium_banked_after_roll": banked,
            "if_assigned_at_new_strike": {
                "proceeds": new_strike * covered,
                "share_gain_vs_basis": (new_strike - basis) * covered,
                "total_result": rolled_assigned,
            },
            "assigned_now_vs_rolled_then_assigned": rolled_assigned - assigned["total_result"],
            "shares_kept_if_not_assigned": covered,
        }
    return out


def _usd(x: float) -> str:
    return f"-${-x:,.2f}" if x < 0 else f"${x:,.2f}"


def render(a: dict) -> str:
    L = [
        f"Covered call: {a['symbol'] or '?'}  {a['contracts']}x ${a['strike']:g} call exp {a['expiry'] or '?'}  "
        f"(stock ${a['underlying_price']:g}, {a['moneyness']})",
        "  READ-ONLY: arithmetic on the quotes you gave; nothing is ordered.",
        f"  Premium collected:      {_usd(a['premium_collected_total'])}",
        f"  Buy-back cost (ask):    {_usd(a['buyback_cost'])}",
        f"  Close now, keep shares: {_usd(a['close_now_net_premium'])} net premium",
        "  If assigned at current strike:",
        f"    sell {a['if_assigned']['shares_sold']} shares for {_usd(a['if_assigned']['proceeds'])}",
        f"    shares vs basis {_usd(a['if_assigned']['share_gain_vs_basis'])} + premium {_usd(a['if_assigned']['premium_kept'])}"
        f" = total {_usd(a['if_assigned']['total_result'])}",
    ]
    if a["if_assigned"]["upside_given_up_vs_now"]:
        L.append(f"    upside given up vs today's price: {_usd(a['if_assigned']['upside_given_up_vs_now'])}")
    r = a["roll"]
    if r:
        L += [
            f"  Roll to ${r['strike']:g} exp {r['expiry'] or '?'} (new call bid ${r['new_bid']:g}):",
            f"    net {r['kind']}: {_usd(r['net_total'])} ({_usd(r['net_per_share'])}/share)",
            f"    premium banked after roll: {_usd(r['premium_banked_after_roll'])}",
            f"    if assigned at new strike: total {_usd(r['if_assigned_at_new_strike']['total_result'])}",
            f"    rolled-then-assigned vs assigned now: {_usd(r['assigned_now_vs_rolled_then_assigned'])}",
            f"    if not assigned: you keep {r['shares_kept_if_not_assigned']} shares",
        ]
    for w in a["warnings"]:
        L.append(f"  ! {w}")
    return "\n".join(L)


SELFTEST = {
    "symbol": "SELFTEST", "underlying_price": 372.0, "strike": 375.0, "expiry": "2026-10-09",
    "premium_collected": 7.15, "contracts": 2, "shares": 200, "cost_basis": 300.0, "buyback_ask": 5.0,
    "roll": {"strike": 385.0, "expiry": "2026-10-16", "bid": 9.0},
}


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only covered call buy-back / roll / assignment calculator.")
    ap.add_argument("input", nargs="?", help="JSON file (see module docstring). Without file: self-test.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.input:
        with open(args.input) as f:
            raw = json.load(f)
    else:
        raw = SELFTEST
        print("[synthetic self-test]\n", file=sys.stderr)

    try:
        res = analyze(raw)
    except (ValueError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(json.dumps(res, indent=2, ensure_ascii=False) if args.json else render(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
