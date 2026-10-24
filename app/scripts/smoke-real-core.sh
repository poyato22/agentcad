#!/usr/bin/env bash
# Real-core smoke run: starts the Python core with a fresh throwaway MISES_HOME (always a new temp
# dir, removed on exit; an inherited MISES_HOME is ignored), points the app at it
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

# Always a fresh throwaway home: an inherited MISES_HOME (maybe the real one, with real runs,
# keys and server.json) is ignored. It must be under a system temp dir and is removed on exit.
SMOKE_HOME="$(cd "$(mktemp -d "${TMPDIR:-/tmp}/mises-smoke.XXXXXX")" && pwd -P)"
case "$SMOKE_HOME" in
  /tmp/*|/private/tmp/*|/var/folders/*|/private/var/folders/*) ;;
  *) rm -rf "$SMOKE_HOME"; echo "refusing to run: $SMOKE_HOME is not under a system temp dir" >&2; exit 1 ;;
esac
export MISES_HOME="$SMOKE_HOME"
INFO="$MISES_HOME/server.json"
CORE_PID=""
cleanup() {
  if [[ -n "$CORE_PID" ]]; then kill "$CORE_PID" 2>/dev/null || true; wait "$CORE_PID" 2>/dev/null || true; fi
  rm -rf "$SMOKE_HOME"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

if [[ "${MISES_SMOKE_DRY_RUN:-}" == 1 ]]; then echo "MISES_HOME=$MISES_HOME"; exit 0; fi  # tests

if [[ $SCRIPTED == 1 ]]; then
  uv run --project "$CORE" python "$APP/scripts/smoke_core_scripted.py" --port 0 &
else
  uv run --project "$CORE" mises serve --port 0 &
fi
CORE_PID=$!

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
