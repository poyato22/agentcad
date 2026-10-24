# Mises Mac app

Tauri 2 shell + Preact UI over the Python core (`../core`). The shell spawns
`mises serve --port 0 --exit-on-stdin-eof` (from `<core>/.venv/bin/mises` when it
exists, otherwise via `uv run`), reads `server.json` for the port and token, and
stops the core when the app quits.

## Prerequisites

- Xcode (or Command Line Tools), Rust >= 1.77, Node 22, uv

```sh
brew install uv rust node
cd app && npm ci
cd ../core && uv sync
```

## Run

| Goal | Command |
| --- | --- |
| UI only, in a browser | `npm run dev` plus `npm run fake-core` (port 7400, token `dev-token`) |
| Full app with the real core | `npm run tauri:dev` |
| Unit tests / lint / e2e | `npm test` / `npm run lint` / `npm run e2e` |
| Real-core smoke run | `scripts/smoke-real-core.sh` |

## Build a dev `.app`

```sh
npm run build:app          # scripts/build-dev-app.sh: build, then verify
scripts/build-dev-app.sh --verify
MISES_BUNDLE_DEBUG=1 npm run build:app   # debug profile, faster
```

Output: `src-tauri/target/release/bundle/macos/Mises.app` (ad-hoc signed, bundle id
`app.mises.desktop`, macOS 13+). It is **not** notarized and has no bundled
Python: the core path is baked in at compile time from `MISES_CORE_DIR` (this
checkout's `core/`), so the `.app` only works on the machine that built it, with
`uv` installed. Opening a copy from another Mac will hit Gatekeeper.

If the build fails with `xattr: option -r not recognized`, a pip/conda `xattr`
is shadowing `/usr/bin/xattr`; the script puts `/usr/bin` first on PATH to avoid it.

## Data and keys

Data lives in `~/Library/Application Support/Mises` (parts, runs, `server.json`).
Store provider keys with:

```sh
cd core && uv run mises keys set anthropic
```

## Environment variables

| Variable | Effect |
| --- | --- |
| `MISES_HOME` | Data directory override (also where `server.json` is read) |
| `MISES_CORE_DIR` | Core checkout to run (runtime override; also read at compile time) |
| `MISES_CORE_CMD` | Full core command override (debug builds only) |
| `MISES_UV` | Path to the `uv` binary |

## Packaging follow-ups (not done in Plan 2)

- **Bundled core:** ship Python as a Tauri `externalBin` sidecar: either a
  python-build-standalone runtime plus a `uv`-synced venv in `Resources/`, or a
  frozen binary. Include build123d/OCP (OCCT) wheels per arch, and later the
  gmsh/CalculiX executables kept separate for the GPL. Measure size against the
  installer budget (design section 9).
- **Signing:** Apple Developer ID Application certificate ($99/yr), hardened
  runtime, entitlements (`com.apple.security.cs.disable-library-validation` for
  the embedded Python's native extensions; check whether `allow-jit` is needed),
  sign every nested Mach-O (Python dylibs/.so) inside out.
- **Notarization:** `xcrun notarytool submit --wait` with an App Store Connect
  API key, then `xcrun stapler staple`; `.dmg` target.
- **Updater:** Tauri updater plugin with signing keys and a hosted update feed.
- **GPL separation:** CalculiX and OpenFOAM (later) run as separate executables
  invoked by the core, never linked, shipped with their licenses and sources.
- **Sandbox:** the core uses `sandbox-exec` for build scripts, which only works
  outside the App Store sandbox, so distribution is Developer ID, not the Mac App Store.
- **Windows:** `platform.rs` paths, process-group kill to Job Objects, `.msi`,
  Authenticode.
