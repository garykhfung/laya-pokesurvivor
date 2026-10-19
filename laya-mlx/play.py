"""Serve the sibling pokesurvivor checkout and steer a solo run.

    pip install -r laya-mlx/requirements.txt
    python laya-mlx/play.py
    python laya-mlx/play.py pikachu
    python laya-mlx/play.py 25

Opens dev.html on 127.0.0.1 in a throwaway browser profile. Never contacts
pokesurvivor.com. The Laya checkpoint needs Apple Silicon and is cached by
Hugging Face outside this repo.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from decide import decide, load_agent

ROOT = Path(__file__).resolve().parent.parent / "pokesurvivor"
BOT = Path(__file__).resolve().parent
PORT = 8765
AGENT = None
LOCK = threading.Lock()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt, *args):
        line = fmt % args
        if line.startswith("GET") and "dev.html" not in line and "__bot.js" not in line:
            return
        super().log_message(fmt, *args)

    def _bytes(self, code, body, content_type):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/__bot.js":
            self._bytes(200, (BOT / "bridge.js").read_bytes(), "text/javascript; charset=utf-8")
            return
        if path in ("/", "/dev.html"):
            html = (ROOT / "dev.html").read_text(encoding="utf-8")
            html = html.replace("</body>", '<script defer src="/__bot.js"></script>\n</body>', 1)
            self._bytes(200, html, "text/html; charset=utf-8")
            return
        super().do_GET()

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/decide":
            self.send_error(404)
            return
        n = int(self.headers.get("Content-Length") or 0)
        if n < 0 or n > 100_000:
            self.send_error(413)
            return
        try:
            snap = json.loads(self.rfile.read(n).decode("utf-8"))
            with LOCK:
                out = decide(AGENT, snap)
        except Exception as e:
            print("[bot] decide failed:", e, file=sys.stderr)
            self._bytes(500, json.dumps({"error": str(e)}), "application/json")
            return
        if out.get("action") != "move" or out.get("shielded"):
            print("[bot]", out.get("action"), out.get("label"), "shielded" if out.get("shielded") else "")
        self._bytes(200, json.dumps(out), "application/json")


def find_browser():
    mac = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    ]
    for path in mac:
        if os.path.isfile(path):
            return path
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge"):
        found = shutil.which(name)
        if found:
            return found
    return None


def launch(url):
    browser = find_browser()
    profile = tempfile.mkdtemp(prefix="ps-bot-")
    if not browser:
        print("No Chrome, Chromium, or Edge found. Open this in a fresh profile:\n ", url)
        return None
    args = [
        browser,
        "--user-data-dir=" + profile,
        "--no-first-run",
        "--disable-sync",
        "--autoplay-policy=no-user-gesture-required",
        "--window-size=1280,800",
        url,
    ]
    if sys.platform.startswith("linux"):
        args[1:1] = ["--no-sandbox", "--disable-dev-shm-usage"]
    print("browser", browser)
    print("profile", profile)
    return subprocess.Popen(args)


def serve(agent, open_browser=True):
    global AGENT
    AGENT = agent
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    mon = " ".join(sys.argv[1:]).strip()
    url = "http://127.0.0.1:%d/dev.html?bot=1" % PORT
    if mon:
        url += "&mon=" + urllib.parse.quote(mon)
    print("serving", url)
    proc = launch(url) if open_browser else None
    return httpd, proc


def main():
    agent = load_agent()
    httpd, _proc = serve(agent)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        httpd.shutdown()
        print("\nstopped")


if __name__ == "__main__":
    main()
