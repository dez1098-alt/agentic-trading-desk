#!/usr/bin/env bash
# Tests the notional cap in hooks/mandate-order-guard.sh: orders below and at the
# cap are allowed, orders above it are denied. The cap is read from the guard
# itself, so the test follows whatever value the script currently enforces.
# Usage: bash tests/test-mandate-order-guard.sh   (requires jq)

set -uo pipefail
cd "$(dirname "$0")/.."
GUARD=hooks/mandate-order-guard.sh

command -v jq >/dev/null || { echo "jq is required"; exit 2; }

CAP=$(grep -oE 'tonumber\) > [0-9]+' "$GUARD" | grep -oE '[0-9]+$' | head -1)
[ -n "$CAP" ] || { echo "could not read cap from $GUARD"; exit 2; }
echo "cap under test: \$$CAP"

fails=0
# check <name> <allow|deny> <HHMM:D clock> <tool_input json>
check() {
  local name=$1 want=$2 clock=$3 input=$4 out got
  out=$(printf '{"tool_input":%s}' "$input" | MANDATE_FAKE_ET="$clock" bash "$GUARD")
  if printf '%s' "$out" | jq -e '.hookSpecificOutput.permissionDecision == "deny"' >/dev/null 2>&1; then
    got=deny
  else
    got=allow
  fi
  if [ "$got" = "$want" ]; then
    echo "PASS  $name ($got)"
  else
    echo "FAIL  $name: expected $want, got $got"; fails=$((fails + 1))
  fi
}

RTH="1100:2"   # Tuesday 11:00 ET, regular hours
AH="2000:2"    # Tuesday 20:00 ET, after hours

# Regular hours: market orders sized in dollars
for amt in $((CAP - 1)) "$CAP"; do
  check "market \$$amt <= cap" allow "$RTH" \
    "{\"symbol\":\"AAPL\",\"side\":\"buy\",\"type\":\"market\",\"dollar_amount\":\"$amt\",\"time_in_force\":\"gfd\"}"
done
check "market \$$((CAP + 1)) > cap" deny "$RTH" \
  "{\"symbol\":\"AAPL\",\"side\":\"buy\",\"type\":\"market\",\"dollar_amount\":\"$((CAP + 1))\",\"time_in_force\":\"gfd\"}"

# After hours: limit orders sized in shares (notional = quantity * limit_price)
check "limit notional below cap" allow "$AH" \
  "{\"symbol\":\"AAPL\",\"side\":\"buy\",\"type\":\"limit\",\"quantity\":\"1\",\"limit_price\":\"$((CAP - 1))\",\"time_in_force\":\"gfd\"}"
check "limit notional at cap" allow "$AH" \
  "{\"symbol\":\"AAPL\",\"side\":\"buy\",\"type\":\"limit\",\"quantity\":\"1\",\"limit_price\":\"$CAP\",\"time_in_force\":\"gfd\"}"
check "limit notional above cap" deny "$AH" \
  "{\"symbol\":\"AAPL\",\"side\":\"buy\",\"type\":\"limit\",\"quantity\":\"1\",\"limit_price\":\"$((CAP + 1))\",\"time_in_force\":\"gfd\"}"

# Fail closed: buys whose size cannot be checked are denied
check "market buy by quantity (no price) denied" deny "$RTH" \
  '{"symbol":"TSLA","side":"buy","type":"market","quantity":"1000","time_in_force":"gfd"}'
check "missing side treated as buy" deny "$RTH" \
  '{"symbol":"TSLA","type":"market","quantity":"1000","time_in_force":"gfd"}'
check "dollar_amount with \$ sign denied" deny "$RTH" \
  '{"symbol":"AAPL","side":"buy","type":"market","dollar_amount":"$5000","time_in_force":"gfd"}'
check "dollar_amount with comma denied" deny "$RTH" \
  '{"symbol":"AAPL","side":"buy","type":"market","dollar_amount":"5,000","time_in_force":"gfd"}'
check "quantity with units denied" deny "$AH" \
  '{"symbol":"AAPL","side":"buy","type":"limit","quantity":"10 shares","limit_price":"5","time_in_force":"gfd"}'
check "limit_price with \$ denied" deny "$AH" \
  '{"symbol":"AAPL","side":"buy","type":"limit","quantity":"10","limit_price":"$400","time_in_force":"gfd"}'
check "dollar_amount and quantity both set denied" deny "$RTH" \
  '{"symbol":"AAPL","side":"buy","type":"market","dollar_amount":"50","quantity":"1000","time_in_force":"gfd"}'

# Sells are exits: never capped, so a position that grew past $100 can still be closed
check "market sell by quantity allowed" allow "$RTH" \
  '{"symbol":"IWM","side":"sell","type":"market","quantity":"0.354761","time_in_force":"gfd"}'
check "after-hours sell above \$100 allowed" allow "$AH" \
  '{"symbol":"XLE","side":"sell","type":"limit","quantity":"2","limit_price":"65","time_in_force":"gfd"}'
check "sell with bad number still denied" deny "$RTH" \
  '{"symbol":"IWM","side":"sell","type":"market","quantity":"all","time_in_force":"gfd"}'

if [ "$fails" -eq 0 ]; then echo "All tests passed"; else echo "$fails test(s) failed"; exit 1; fi
