#!/bin/sh
# Stub for `mises serve`. Env: MISES_HOME (required), STUB_MODE=serve|crash|fork, STUB_DELAY (s before
# writing server.json), STUB_PIDFILE (fork mode: grandchild pid, then own pid).
case "${STUB_MODE:-serve}" in
  crash) echo "stub: boom" >&2; exit 1 ;;
  fork) sleep 300 & echo $! > "$STUB_PIDFILE"; echo $$ >> "$STUB_PIDFILE" ;;
esac
exec python3 - <<'PY'
import http.server, json, os, time
home = os.environ["MISES_HOME"]; token = "stub-token"
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        ok = self.path == "/health" and self.headers.get("Authorization") == "Bearer " + token
        self.send_response(200 if ok else 401); self.end_headers()
        self.wfile.write(b'{"ok":true}' if ok else b'{"error":"unauthorized"}')
    def log_message(self, *a): pass
srv = http.server.HTTPServer(("127.0.0.1", 0), H)
time.sleep(float(os.environ.get("STUB_DELAY", "0")))
os.makedirs(home, exist_ok=True)
path = os.path.join(home, "server.json")
fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.write(fd, json.dumps({"port": srv.server_address[1], "token": token, "pid": os.getpid()}).encode()); os.close(fd)
os.replace(path + ".tmp", path)
srv.serve_forever()
PY
