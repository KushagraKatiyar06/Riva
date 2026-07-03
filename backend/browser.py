# Shared browser session state (used by ws_browse and ws_pipeline), Chrome
# launcher for the CDP fallback path, and the extract file writer.

import os
import re
import shutil
import socket
import subprocess
import time
import threading
import queue as tqueue
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .config import EXTRACTS_DIR
from .vectorize import _clear_domain_complete


class _Session:
    def __init__(self, expected: int):
        self.expected   = expected
        self.completed  = 0
        self.cancelled  = False
        self.domains:   list[str] = []
        self.ready_q    = tqueue.Queue()   # domains ready for vectorization
        self.done_event = threading.Event()
        self._lock      = threading.Lock()

    def mark_complete(self, domain: str):
        with self._lock:
            self.completed += 1
            if domain and domain not in self.domains:
                self.domains.append(domain)
                self.ready_q.put(domain)
            if self.completed >= self.expected:
                self.done_event.set()

    def cancel(self):
        with self._lock:
            self.cancelled = True
            self.done_event.set()  # unblock the pipeline thread


_sessions: dict[str, _Session] = {}


def _launch_bare_chrome(target_url: str, debug_port: int, user_data_dir: str):
    # Launch Chrome without Playwright's automation flags so Cloudflare Turnstile doesn't block us.
    import glob
    chrome_exe = shutil.which("chrome") or shutil.which("google-chrome") or shutil.which("chromium-browser") or shutil.which("chromium")
    if not chrome_exe:
        # Windows system Chrome
        for path in [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]:
            if os.path.exists(path):
                chrome_exe = path
                break
    if not chrome_exe:
        # Linux: fall back to Playwright's bundled Chromium
        for pattern in [
            "/root/.cache/ms-playwright/chromium-*/chrome-linux/chrome",
            os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux/chrome"),
        ]:
            matches = sorted(glob.glob(pattern))
            if matches:
                chrome_exe = matches[-1]  # latest version
                break
    if not chrome_exe:
        raise RuntimeError("Chrome executable not found")
    return subprocess.Popen([
        chrome_exe,
        f"--remote-debugging-port={debug_port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        target_url,
    ])


def _wait_for_debug_port(port: int, timeout: int = 15) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("localhost", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def save_extract(domain: str, intel_type: str, url: str, content: str) -> str:
    folder = EXTRACTS_DIR / domain
    folder.mkdir(parents=True, exist_ok=True)
    # clear the completion marker so this domain gets re-vectorized on next run
    _clear_domain_complete(folder)
    slug = re.sub(r'[^a-zA-Z0-9_-]', '_', urlparse(url).path.strip('/'))[:60] or 'index'
    filename = f"{intel_type}_{slug}.txt"
    filepath = folder / filename
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(f"URL      : {url}\n")
        f.write(f"Type     : {intel_type}\n")
        f.write(f"Captured : {datetime.now(timezone.utc).isoformat()}\n")
        f.write("=" * 80 + "\n\n")
        f.write(content)
    return str(filepath)
