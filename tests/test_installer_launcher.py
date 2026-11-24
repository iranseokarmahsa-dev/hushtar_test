"""Tests for the packaged application's loopback health check."""

import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from installer.launcher import _wait_for_server


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def log_message(self, format, *args):
        pass


def test_health_check_bypasses_system_proxy():
    server = HTTPServer(("127.0.0.1", 0), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # An unreachable proxy reproduces the Windows proxy configuration failure.
        with patch.dict(os.environ, {"http_proxy": "http://127.0.0.1:1", "no_proxy": ""}):
            _wait_for_server(thread, f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
