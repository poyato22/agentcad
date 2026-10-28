"""A tiny fake PrusaLink v1 server on 127.0.0.1 (status + file upload) for tests."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API_KEY = "fake-key-abc"


class FakePrusaLink:
    def __init__(self, state: str = "IDLE", api_key: str = API_KEY):
        self.state = state
        self.api_key = api_key
        self.uploads: list[dict] = []
        self.requests: list[tuple[str, str]] = []
        self.upload_status = 201
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: D401
                pass

            def _send(self, code, body=b"", ctype="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authed(self):
                outer.requests.append((self.command, self.path))
                if self.headers.get("X-Api-Key") != outer.api_key:
                    self._send(401, b'{"message":"Unauthorized"}')
                    return False
                return True

            def do_GET(self):
                if not self._authed():
                    return
                if self.path == "/api/v1/status":
                    body = {"printer": {"state": outer.state, "temp_nozzle": 215.0, "temp_bed": 60.0}}
                    if outer.state == "PRINTING":
                        body["job"] = {"id": 7, "progress": 42.0, "time_remaining": 900}
                    self._send(200, json.dumps(body).encode())
                elif self.path == "/api/version":
                    self._send(200, b'{"api":"2.0.0","server":"2.1.2","text":"PrusaLink"}')
                else:
                    self._send(404)

            def do_PUT(self):
                if not self._authed():
                    return
                n = int(self.headers.get("Content-Length", 0))
                data = self.rfile.read(n)
                if not self.path.startswith("/api/v1/files/usb/"):
                    self._send(404)
                    return
                outer.uploads.append({"path": self.path, "data": data,
                                      "print_after": self.headers.get("Print-After-Upload"),
                                      "overwrite": self.headers.get("Overwrite"),
                                      "ctype": self.headers.get("Content-Type")})
                self._send(outer.upload_status, b"")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.host = f"127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.server.shutdown()
        self.server.server_close()
