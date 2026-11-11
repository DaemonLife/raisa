"""Test helpers: a local "site" and the program in a virtual terminal.

FakeSite — an HTTP server on 127.0.0.1 serving pages from a dict
{path: (status, html)}, to GET and POST alike; point() makes a branch use it instead of the
real site.

TuiProcess — raisa in a pseudo-terminal (pty), the English branch pointed at a
FakeSite; OFFLINE in its environment keeps it off the real network. Everything the program prints is
"drawn" by the pyte terminal emulator, and a test reads the screen as text:
tui.screen_text(), tui.wait_for("SCP-173"). Like foot, the "terminal" confirms the
kitty keyboard protocol (and Sixel if asked), and send() types keys the way foot does.
"""
import contextlib
import fcntl
import json
import os
import pty
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from raisa import sites

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
# Environment that keeps the tests off the internet: a proxy that does not exist for everything
# but the local FakeSite.
OFFLINE = dict.fromkeys(("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"), "http://127.0.0.1:9") \
    | {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text("utf-8")


def point(code: str, url: str):
    """Make a branch use another site address (a local FakeSite)."""
    b = sites.BRANCHES[code]
    b.base = url
    b.hosts.insert(0, url.split("://")[1])


# run the program with the English branch pointed at argv[1] (as point() does)
LAUNCH = ("import sys; from raisa import sites; b = sites.BRANCHES['en']; b.base = sys.argv.pop(1); "
          "b.hosts.insert(0, b.base.split('://')[1]); from raisa.cli import main; main()")


_SEQ = re.compile(r"\x1b\[200~.*?\x1b\[201~|\x1b\[[0-9;:?]*[\x40-\x7e]", re.S)
_CODES = {"\r": "13", "\t": "9", "\x1b": "27", "\x7f": "127"}


def kitty(s: str) -> str:
    """Keys as foot sends them with the kitty keyboard protocol: "j" → CSI 106;;106 u,
    "J" → CSI 106;2 u, Ctrl-Z → CSI 122;5 u; escape sequences and pastes pass as is."""
    out, i, s = [], 0, s.replace("\x1b[Z", "\x1b[9;2u")       # Shift-Tab
    while i < len(s):
        m = _SEQ.match(s, i)
        ch = m.group() if m else s[i]
        i += len(ch)
        if m:
            out.append(ch)
        elif ch in _CODES:
            out.append(f"\x1b[{_CODES[ch]}u")
        elif ch < " ":
            out.append(f"\x1b[{ord(ch) + 96};5u")
        elif ch.isascii() and ch.isupper():
            out.append(f"\x1b[{ord(ch.lower())};2u")
        else:
            out.append(f"\x1b[{ord(ch)};;{ord(ch)}u")
    return "".join(out)


def tiny_png(w: int, h: int) -> bytes:
    """A red rectangle as PNG, written by hand (no pillow)."""
    raw = b"".join(b"\x00" + b"\xff\x00\x00\xff" * w for _ in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class FakeSite:
    def __init__(self, pages: dict[str, tuple[int, str | bytes]]):
        self.pages = pages
        self.requests: list[str] = []
        self.posted: list = []          # JSON bodies of POST requests
        site = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                site.requests.append(self.path)
                path = self.path.split("?")[0].rstrip("/") or "/"
                missing = "<html><body>This page doesn't exist" + " " * 300 + "</body></html>"
                code, body = site.pages.get(path, (404, missing))
                data = body.encode() if isinstance(body, str) else body
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                site.posted.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.do_GET()

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class TuiProcess:
    def __init__(self, args: list[str], home: Path, base: str, cols=130, rows=36, kitty=True, sixel=False):
        import pyte
        self.cols, self.rows = cols, rows
        # answers to the start-up questions, like foot: the kitty protocol, device attributes
        self.answers = {b"\x1b[?u": b"\x1b[?0u" * kitty, b"\x1b[c": b"\x1b[?62;4;22c" if sixel else b"\x1b[?62;22c"}
        self.screen = pyte.Screen(cols, rows)
        self.stream = pyte.ByteStream(self.screen)
        self.raw = b""
        e = dict(os.environ)
        e.update({"XDG_CACHE_HOME": str(home / "cache"), "XDG_DATA_HOME": str(home / "data"),
                  "TERM": "xterm-256color", "LANG": "C.UTF-8"} | OFFLINE)
        master, slave = pty.openpty()
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, cols * 10, rows * 20))
        self.proc = subprocess.Popen([sys.executable, "-c", LAUNCH, base] + args, cwd=ROOT, env=e,
                                     stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
        os.close(slave)
        self.pid, self.fd = self.proc.pid, master
        self.resize(cols, rows)

    def resize(self, cols, rows):
        self.cols, self.rows = cols, rows
        self.screen.resize(rows, cols)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, cols * 10, rows * 20))
        with contextlib.suppress(ProcessLookupError):
            os.kill(self.pid, signal.SIGWINCH)

    def pump(self, timeout=0.05):
        end = time.time() + timeout
        while True:
            left = end - time.time()
            r = select.select([self.fd], [], [], max(left, 0))[0]
            if not r:
                return
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                return
            if not data:
                return
            for question, answer in self.answers.items():
                if question in data:
                    os.write(self.fd, answer)
            self.raw += data
            self.stream.feed(data)

    def send(self, data: str | bytes, wait=0.15):
        """Type keys (a str, converted by kitty()) or send raw bytes."""
        os.write(self.fd, kitty(data).encode() if isinstance(data, str) else data)
        self.pump(wait)

    def screen_text(self) -> str:
        return "\n".join(self.screen.display)

    def line(self, y: int) -> str:
        return self.screen.display[y]

    def wait_until(self, pred, timeout=8.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            self.pump(0.1)
            if pred():
                return True
        return False

    def wait_for(self, text: str, timeout=8.0) -> bool:
        return self.wait_until(lambda: text in self.screen_text(), timeout)

    def exited(self, timeout=3.0):
        end = time.time() + timeout
        while time.time() < end:
            self.pump(0.05)
            code = self.proc.poll()
            if code is not None:
                return code
        return None

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        os.close(self.fd)
