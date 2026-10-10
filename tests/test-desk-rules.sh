#!/usr/bin/env bash
# Tests the desk rules in scripts/score.py (apply_desk_rules, market_ok_from_spy)
# and the read-only helper scripts/watchlist_run.py on a small synthetic fixture.
# Usage: bash tests/test-desk-rules.sh   (requires python3)

set -uo pipefail
cd "$(dirname "$0")/.."
command -v python3 >/dev/null || { echo "python3 is required"; exit 2; }

python3 - <<'PY'
import csv, json, os, subprocess, sys, tempfile
sys.path.insert(0, "scripts")
import score as S
import watchlist_run as W

fails = 0
def check(name, got, want):
    global fails
    if got == want:
        print(f"PASS  {name} ({got})")
    else:
        print(f"FAIL  {name}: expected {want!r}, got {got!r}"); fails += 1

def dec(action):
    return {"action": action, "rationale": "r", "framing": "f.", "flags": {}}

R = "RE-ENTRY (new cycle)"; T = "TACTICAL REBOUND (counter-trend)"
check("tactical blocked by default", S.apply_desk_rules(dec(T), False)["action"], "WATCH (counter-trend, not an entry)")
check("tactical raw kept", S.apply_desk_rules(dec(T), False)["raw_action"], T)
check("tactical allowed on request", S.apply_desk_rules(dec(T), False, allow_tactical=True)["action"], T)
check("re-entry passes", S.apply_desk_rules(dec(R), False, market_ok=True, days_to_earnings=12)["action"], R)
check("market filter blocks entry", S.apply_desk_rules(dec(R), False, market_ok=False)["action"], "WAIT (market filter)")
check("unknown market does not block", S.apply_desk_rules(dec(R), None, market_ok=None)["action"], R)
check("earnings guard blocks entry", S.apply_desk_rules(dec(R), False, days_to_earnings=3)["action"], "WAIT (earnings in 3 trading days)")
check("earnings guard edge 5 days", S.apply_desk_rules(dec(R), False, days_to_earnings=5)["action"], "WAIT (earnings in 5 trading days)")
check("earnings 6 days passes", S.apply_desk_rules(dec(R), False, days_to_earnings=6)["action"], R)
check("exit never blocked", S.apply_desk_rules(dec("EXIT"), True, market_ok=False, days_to_earnings=1)["action"], "EXIT")
check("holding gets earnings note", "Earnings in 2 trading days" in S.apply_desk_rules(dec("HOLD (ride the cycle)"), True, days_to_earnings=2)["framing"], True)
check("market ok above 200d", S.market_ok_from_spy([100.0] * 199 + [110.0]), True)
check("market off below 200d", S.market_ok_from_spy([100.0] * 199 + [90.0]), False)
check("market unknown short history", S.market_ok_from_spy([100.0] * 50), None)
check("trading days Fri->Thu", W.trading_days_between("2026-10-09", "2026-10-15"), 4)
check("trading days same day", W.trading_days_between("2026-10-09", "2026-10-09"), 0)
check("wilder rsi all gains", W.wilder_rsi([float(i) for i in range(30)]), 100.0)

# watchlist_run end to end on a synthetic fixture (dry run and real write)
import math
def bars(sym, f):
    out = []
    for i in range(260):
        d = f"2025-{(i // 28) % 12 + 1:02d}-{i % 28 + 1:02d}"
        out.append({"begins_at": d + "T00:00:00Z", "open_price": str(f(i)), "close_price": str(f(i))})
    return {"symbol": sym, "bars": out}
tmp = tempfile.mkdtemp()
bf = os.path.join(tmp, "bars.json")
json.dump({"data": {"results": [bars("SPY", lambda i: 100 + i * 0.2),
                                  bars("XLE", lambda i: 50 + 5 * math.sin(i / 9))]}}, open(bf, "w"))
with open(os.path.join(tmp, "agentic_watch.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow("as_of,symbol,group,price,day_change_pct,sma20,dist_to_sma20_pct,rsi14,score,decision,flag,note".split(","))
    w.writerow(["", "SPY", "Index"] + [""] * 9)
    w.writerow(["", "XLE", "Sector"] + [""] * 9)
json.dump({"positions": [{"symbol": "SPY", "qty": 0.1, "avg_cost": 140, "core": True},
                         {"symbol": "XLE", "qty": 1, "avg_cost": 50, "exit_level": 48}],
           "tsla_call": {"ask": 1}}, open(os.path.join(tmp, "agentic_meta.json"), "w"))
cmd = [sys.executable, "scripts/watchlist_run.py", "--date", "2026-01-05", "--time", "10:00",
       "--tracker", tmp, "--bars", bf, "--quote", "SPY=152:151.8", "XLE=51:50.5"]
r = subprocess.run(cmd + ["--dry-run"], capture_output=True, text=True)
check("dry run exits 0", r.returncode, 0)
check("dry run leaves csv", list(csv.DictReader(open(os.path.join(tmp, "agentic_watch.csv"))))[0]["price"], "")
r = subprocess.run(cmd, capture_output=True, text=True)
check("write run exits 0", r.returncode, 0)
rows = {x["symbol"]: x for x in csv.DictReader(open(os.path.join(tmp, "agentic_watch.csv")))}
check("held name flagged HOLDING", rows["XLE"]["flag"], "HOLDING")
check("core position noted", "core, held" in rows["SPY"]["note"], True)
meta = json.load(open(os.path.join(tmp, "agentic_meta.json")))
check("position P/L updated", meta["positions"][1]["pl"], 1.0)
check("stale tsla_call dropped", "tsla_call" in meta, False)
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
PY
