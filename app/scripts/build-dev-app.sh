#!/usr/bin/env bash
# Build an ad-hoc signed dev Mises.app that runs the core from this checkout via uv.
#   scripts/build-dev-app.sh            build, then verify
#   scripts/build-dev-app.sh --verify   verify an existing bundle only
#   MISES_BUNDLE_DEBUG=1 ...            pass --debug to tauri build (faster)
set -euo pipefail
cd "$(dirname "$0")/.."

PROFILE=release
[ "${MISES_BUNDLE_DEBUG:-}" = 1 ] && PROFILE=debug
APP="src-tauri/target/$PROFILE/bundle/macos/Mises.app"

verify() {
  local fail=0
  [ -d "$APP" ] || { echo "FAIL: $APP not found"; return 1; }
  local sig
  sig=$(codesign -dv "$APP" 2>&1 || true)
  echo "$sig" | grep -q 'Signature=adhoc' || { echo "FAIL: not ad-hoc signed"; fail=1; }
  local plist="$APP/Contents/Info.plist" id min
  id=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$plist")
  [ "$id" = app.mises.desktop ] || { echo "FAIL: CFBundleIdentifier=$id"; fail=1; }
  min=$(/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' "$plist")
  [ "$(printf '%s\n13.0\n' "$min" | sort -V | head -1)" = 13.0 ] || { echo "FAIL: LSMinimumSystemVersion=$min < 13.0"; fail=1; }
  [ $fail = 0 ] && echo "OK: $APP (id=$id, min=$min, adhoc)"
  return $fail
}

if [ "${1:-}" = --verify ]; then
  verify
  exit $?
fi

# Tauri shells out to `xattr -cr`; a pip/conda `xattr` earlier on PATH rejects -r.
export PATH="/usr/bin:$PATH"

EXTRA=()
[ "$PROFILE" = debug ] && EXTRA=(--debug)
MISES_CORE_DIR="$(cd ../core && pwd)" npm run tauri build -- --bundles app ${EXTRA[@]+"${EXTRA[@]}"}
verify
