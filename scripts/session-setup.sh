#!/usr/bin/env bash
# session-setup.sh: SessionStart hook for the Agentic Trading Desk.
# Makes sure jq is installed (the order guard needs it) and smoke-tests
# hooks/mandate-order-guard.sh with a stop_market probe that must be denied.
# It never blocks the session: the guard already fails closed without jq,
# so a failed setup only means every order is denied until it is fixed.
set -uo pipefail
dir="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
guard="$dir/hooks/mandate-order-guard.sh"

if ! command -v jq >/dev/null 2>&1; then
  (apt-get install -y jq >/dev/null 2>&1 || (apt-get update >/dev/null 2>&1 && apt-get install -y jq >/dev/null 2>&1)) \
    || echo "session-setup: could not install jq; the order guard will deny every order until it is installed." >&2
fi
chmod +x "$guard" 2>/dev/null || true

probe='{"tool_input":{"symbol":"SPY","side":"buy","type":"stop_market","dollar_amount":"10","time_in_force":"gfd"}}'
if printf '%s' "$probe" | bash "$guard" | grep -q '"deny"'; then
  echo "session-setup: order guard active (stop_market probe denied)."
else
  echo "session-setup: WARNING, the order guard did not deny a stop_market probe. Do not place orders until this is fixed." >&2
fi
exit 0
