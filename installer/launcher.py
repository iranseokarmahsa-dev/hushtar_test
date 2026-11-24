"""Start Hooshyar's local web server and open it in the default browser."""

import logging
import os
import shutil
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


def _show_error(message: str) -> None:
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, "Hooshyar", 0x10)
    else:
        print(message, file=sys.stderr)


def _resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[1]


def _data_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Hooshyar"
    return Path.home() / "AppData" / "Local" / "Hooshyar"


def _prepare_data_dir(data_dir: Path, resource_dir: Path) -> None:
    database_dir = data_dir / "data"
    database_dir.mkdir(parents=True, exist_ok=True)
    database_path = database_dir / "hooshyar.sqlite3"
    if database_path.exists():
        return
    seed_path = resource_dir / "seed" / "hooshyar-seed.sqlite3"
    if seed_path.is_file():
        shutil.copy2(seed_path, database_path)


def _available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_server(thread: threading.Thread, url: str) -> None:
    deadline = time.monotonic() + 30
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline:
        if not thread.is_alive():
            raise RuntimeError("The Hooshyar local server stopped during startup.")
        try:
            with opener.open(f"{url}/health", timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.25)
    raise RuntimeError("Hooshyar did not start within 30 seconds.")


def main() -> None:
    data_dir = _data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    _prepare_data_dir(data_dir, _resource_dir())
    os.environ.setdefault("HOOSHYAR_DATA_DIR", str(data_dir / "data"))
    os.environ.setdefault("ENVIRONMENT", "development")
    resource_dir = _resource_dir()
    if str(resource_dir) not in sys.path:
        sys.path.insert(0, str(resource_dir))
    install_dir = (
        Path(sys.executable).resolve().parent
        if getattr(sys, "frozen", False)
        else resource_dir
    )
    graphviz_bin = install_dir / "graphviz" / "bin"
    if graphviz_bin.is_dir():
        os.environ["PATH"] = f"{graphviz_bin}{os.pathsep}{os.environ.get('PATH', '')}"
    os.chdir(resource_dir)
    logging.basicConfig(
        filename=data_dir / "hooshyar.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        from app.main import app
        import uvicorn

        port = (
            int(os.environ["HOOSHYAR_PORT"])
            if "HOOSHYAR_PORT" in os.environ
            else _available_port()
        )
        url = f"http://127.0.0.1:{port}"
        server = threading.Thread(
            target=uvicorn.run,
            args=(app,),
            kwargs={
                "host": "127.0.0.1",
                "port": port,
                "log_level": "warning",
                "log_config": None,
                "access_log": False,
            },
            daemon=True,
        )
        server.start()
        _wait_for_server(server, url)
        if os.environ.get("HOOSHYAR_NO_BROWSER") != "1" and not webbrowser.open(url):
            raise RuntimeError(
                f"Hooshyar is running, but a browser could not be opened.\nOpen {url} manually."
            )
        while server.is_alive():
            server.join(timeout=1)
    except Exception as exc:
        logging.exception("Hooshyar failed to start")
        _show_error(
            f"Hooshyar could not start.\n\n{exc}\n\n"
            f"Details were saved to:\n{data_dir / 'hooshyar.log'}"
        )


if __name__ == "__main__":
    main()
