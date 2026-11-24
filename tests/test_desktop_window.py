"""Tests for the native desktop-window launcher."""

import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

from installer.launcher import _show_desktop_window


class FakeEvent:
    def __iadd__(self, callback):
        self.callback = callback
        return self


def test_desktop_window_stops_server_when_closed():
    closed = FakeEvent()
    window = SimpleNamespace(events=SimpleNamespace(closed=closed))
    webview = SimpleNamespace(
        create_window=Mock(return_value=window),
        start=Mock(),
    )
    server = SimpleNamespace(should_exit=False)

    with patch.dict(sys.modules, {"webview": webview}):
        _show_desktop_window("http://127.0.0.1:12345", server)

    webview.create_window.assert_called_once_with(
        "Hooshyar",
        "http://127.0.0.1:12345",
        width=1280,
        height=850,
        min_size=(800, 600),
        background_color="#f3f6f2",
    )
    webview.start.assert_called_once_with(gui="edgechromium")
    closed.callback()
    assert server.should_exit is True
