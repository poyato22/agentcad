#!/usr/bin/env bash
# Real-core smoke run: starts the Python core with a throwaway MISES_HOME, points the app at it
# (VITE_MISES_URL / VITE_MISES_TOKEN from its server.json) and runs Vite on :1420, the dev origin
# the core's CORS allows. Ctrl-C stops both.
#
#   scripts/smoke-real-core.sh              real agent: `mises serve` (needs an API key, or pick
#                                           an ollama/<model> in the new-run dialog)
#   scripts/smoke-real-core.sh --scripted   real server + tools, scripted agent (no model needed)
#
# Then open http://localhost:1420, or run `npm run tauri:dev` for the real window.
# Extra arguments go to vite. The token is never printed.
set -euo pipefail

cd "$(dirname "$0")/.."
APP="$(pwd)"
CORE="$(cd ../core && pwd)"

SCRIPTED=0
if [[ "${1:-}" == "--scripted" ]]; then SCRIPTED=1; shift; fi

export MISES_HOME="${MISES_HOME:-$(mktemp -d -t mises-smoke)}"
INFO="$MISES_HOME/server.json"
rm -f "$INFO"

if [[ $SCRIPTED == 1 ]]; then
  uv run --project "$CORE" python "$APP/scripts/smoke_core_scripted.py" --port 0 &
else
  uv run --project "$CORE" mises serve --port 0 &
fi
CORE_PID=$!
cleanup() { kill "$CORE_PID" 2>/dev/null || true; wait "$CORE_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

for _ in $(seq 1 600); do
  [[ -s "$INFO" ]] && break
  kill -0 "$CORE_PID" 2>/dev/null || { echo "core exited before writing $INFO" >&2; exit 1; }
  sleep 0.1
done
[[ -s "$INFO" ]] || { echo "timed out waiting for $INFO" >&2; exit 1; }

PORT="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["port"])' "$INFO")"
VITE_MISES_TOKEN="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["token"])' "$INFO")"
export VITE_MISES_URL="http://127.0.0.1:$PORT" VITE_MISES_TOKEN
echo "core on $VITE_MISES_URL (MISES_HOME=$MISES_HOME); app on http://localhost:1420" >&2

npx vite --port 1420 --strictPort "$@"
