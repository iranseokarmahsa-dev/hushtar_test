"""Tests for the packaged application's loopback health check."""

import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from installer.launcher import _prepare_data_dir, _wait_for_server


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


def test_prepare_data_dir_installs_seed_only_for_new_database(tmp_path):
    data_dir = tmp_path / "Hooshyar"
    resource_dir = tmp_path / "resources"
    seed_dir = resource_dir / "seed"
    seed_dir.mkdir(parents=True)
    seed_database = seed_dir / "hooshyar.sqlite3"
    seed_database.write_bytes(b"seed database")

    _prepare_data_dir(data_dir, resource_dir)

    target_database = data_dir / "data" / "hooshyar.sqlite3"
    assert target_database.read_bytes() == b"seed database"

    target_database.write_bytes(b"user database")
    _prepare_data_dir(data_dir, resource_dir)
    assert target_database.read_bytes() == b"user database"
