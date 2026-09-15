from __future__ import annotations

import argparse
import functools
import http.client
import os
import shutil
import subprocess
import sys
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
PID_PATH = LOG_DIR / "preview.pid"
SITES = (
    ("Government AI Control", ROOT / "governmentaicontrol.com" / "dist", 8765),
    ("Government Gun Control", ROOT / "governmentguncontrol.com" / "dist", 8766),
    ("Government Healthcare Control", ROOT / "governmenthealthcarecontrol.com" / "dist", 8767),
)


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


def site_is_ready(name: str, port: int) -> bool:
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        connection.request("GET", "/")
        response = connection.getresponse()
        body = response.read(65536).decode("utf-8", errors="ignore")
        connection.close()
        return response.status == 200 and name in body
    except OSError:
        return False


def find_chrome() -> str:
    from_path = shutil.which("chrome") or shutil.which("chrome.exe")
    if from_path:
        return from_path
    candidates = [
        Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError("Google Chrome was not found. Install Chrome or add chrome.exe to PATH.")


def serve() -> None:
    servers: list[ThreadingHTTPServer] = []
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    try:
        for name, directory, port in SITES:
            if not (directory / "index.html").is_file():
                raise RuntimeError(f"Build output is missing for {name}: {directory}")
            handler = functools.partial(QuietHandler, directory=str(directory))
            server = ThreadingHTTPServer(("127.0.0.1", port), handler)
            Thread(target=server.serve_forever, daemon=True).start()
            servers.append(server)
        while True:
            time.sleep(3600)
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        PID_PATH.unlink(missing_ok=True)


def start_servers() -> None:
    ready = [site_is_ready(name, port) for name, _, port in SITES]
    if all(ready):
        return
    if any(ready):
        raise RuntimeError("Only some preview ports are available. Stop the existing preview processes and try again.")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_handle = open(LOG_DIR / "preview.log", "a", encoding="utf-8")
    creation_flags = 0
    if os.name == "nt":
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--serve"],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        creationflags=creation_flags,
        close_fds=True,
    )
    log_handle.close()

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if all(site_is_ready(name, port) for name, _, port in SITES):
            return
        time.sleep(0.2)
    raise RuntimeError(f"Preview servers did not start. Check {LOG_DIR / 'preview.log'}")


def launch_chrome() -> None:
    chrome = find_chrome()
    urls = [f"http://localhost:{port}/" for _, _, port in SITES]
    subprocess.Popen([chrome, *urls], close_fds=True)
    for name, url in zip((site[0] for site in SITES), urls):
        print(f"{name}: {url}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve all three builds and open them in Google Chrome.")
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve:
        serve()
        return
    start_servers()
    launch_chrome()


if __name__ == "__main__":
    main()
