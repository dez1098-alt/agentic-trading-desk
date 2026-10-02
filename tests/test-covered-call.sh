#!/usr/bin/env bash
# Tests scripts/covered_call.py (read-only covered call buy-back / roll / assignment math)
# against hand-computed numbers.
# Usage: bash tests/test-covered-call.sh   (requires jq and python3)

set -uo pipefail
cd "$(dirname "$0")/.."
SCRIPT=scripts/covered_call.py

command -v jq >/dev/null || { echo "jq is required"; exit 2; }
command -v python3 >/dev/null || { echo "python3 is required"; exit 2; }

fails=0
# check <name> <input json> <jq filter> <expected value>
check() {
  local name=$1 input=$2 filter=$3 want=$4 got
  got=$(printf '%s' "$input" | python3 "$SCRIPT" /dev/stdin --json 2>&1 | jq -r "$filter" 2>&1)
  if [ "$got" = "$want" ] || awk -v a="$got" -v b="$want" 'BEGIN{exit !(a ~ /^-?[0-9.]+$/ && b ~ /^-?[0-9.]+$/ && a+0 == b+0)}'; then
    echo "PASS  $name ($got)"
  else
    echo "FAIL  $name: expected $want, got $got"; fails=$((fails + 1))
  fi
}

# 2 contracts, strike 375, sold at 7.15, 200 shares, basis 300, stock 372, buy-back ask 5.00
BASE='"symbol":"TSLA","underlying_price":372,"strike":375,"expiry":"2026-10-09","premium_collected":7.15,"contracts":2,"shares":200,"cost_basis":300,"buyback_ask":5'
CREDIT="{$BASE,\"roll\":{\"strike\":385,\"expiry\":\"2026-10-16\",\"bid\":9}}"
DEBIT="{$BASE,\"roll\":{\"strike\":380,\"expiry\":\"2026-10-16\",\"bid\":3}}"

check "buy-back cost"              "{$BASE}" '.buyback_cost' 1000
check "premium collected"          "{$BASE}" '.premium_collected_total' 1430
check "close now net premium"      "{$BASE}" '.close_now_net_premium' 430
check "assigned total result"      "{$BASE}" '.if_assigned.total_result' 16430
check "OTM, no upside given up"    "{$BASE}" '.if_assigned.upside_given_up_vs_now' 0
check "no roll block without roll" "{$BASE}" '.roll' null
check "roll net credit"            "$CREDIT" '.roll.net_total' 800
check "roll kind credit"           "$CREDIT" '.roll.kind' credit
check "rolled then assigned"       "$CREDIT" '.roll.if_assigned_at_new_strike.total_result' 19230
check "roll vs assigned now"       "$CREDIT" '.roll.assigned_now_vs_rolled_then_assigned' 2800
check "roll net debit"             "$DEBIT"  '.roll.net_total' -400
check "debit warns"                "$DEBIT"  '.warnings | map(select(test("DEBIT"))) | length' 1

ITM='{"symbol":"X","underlying_price":110,"strike":100,"premium_collected":2,"contracts":1,"shares":100,"cost_basis":80,"buyback_ask":11}'
check "ITM upside given up"        "$ITM" '.if_assigned.upside_given_up_vs_now' 1000
check "ITM moneyness"              "$ITM" '.moneyness' ITM

BELOW='{"symbol":"X","underlying_price":90,"strike":85,"premium_collected":1,"contracts":1,"shares":100,"cost_basis":100,"buyback_ask":0.5}'
check "strike below basis warns"   "$BELOW" '.warnings | length' 1

UNCOV='{"symbol":"X","underlying_price":90,"strike":95,"premium_collected":1,"contracts":2,"shares":100,"cost_basis":80,"buyback_ask":0.5}'
check "uncovered warns"            "$UNCOV" '.warnings | map(select(test("not fully covered"))) | length' 1

# Bad input exits non-zero
if printf '{"symbol":"X"}' | python3 "$SCRIPT" /dev/stdin >/dev/null 2>&1; then
  echo "FAIL  missing fields should exit non-zero"; fails=$((fails + 1))
else
  echo "PASS  missing fields exit non-zero"
fi

# Read-only: the script must not reference any order-placing tool
if grep -qiE 'place_option_order|review_option_order|exercise_option|cancel_option' "$SCRIPT"; then
  echo "FAIL  script references an order tool"; fails=$((fails + 1))
else
  echo "PASS  script references no order tools"
fi

if [ "$fails" -eq 0 ]; then echo "All tests passed"; else echo "$fails test(s) failed"; exit 1; fi
