"""Terminal: raw mode, alternate screen, keys in any layout, pastes.

With the kitty keyboard protocol (foot, kitty, WezTerm…) every key press comes as an escape
sequence: CSI code[:shifted[:base]] ; modifiers ; text u, the arrows CSI A / CSI B, PgUp / PgDn
CSI 5~ / CSI 6~, Home / End CSI H / CSI F, Shift-Tab CSI Z; plain text comes only inside a paste.
The "base" key is the key of the US layout, so a hotkey is the same physical key in any layout.
Other terminals (xterm, the Linux console, cool-retro-term) send plain bytes and the usual
sequences; there the letters of the Russian layout are turned into the keys of the US one by a
table. At start the terminal also tells whether it draws Sixel images. On exit (also on errors
and SIGTERM) the terminal is restored.
"""
import codecs
import contextlib
import fcntl
import os
import re
import select
import signal
import struct
import sys
import termios
import time
import tty
from typing import NamedTuple

from .text import one_line


class Key(NamedTuple):
    name: str           # for hotkeys: "j", "J", "enter", "ctrl+c", "shift+tab"
    text: str = ""      # what to type into the search line


# Protocol flags: 1 unambiguous codes, 4 base key, 8 all keys as codes, 16 text.
KITTY_FLAGS = 1 | 4 | 8 | 16
SPECIAL = {13: "enter", 9: "tab", 127: "backspace", 27: "esc", 57414: "enter"}   # 57414: keypad Enter
US_SHIFT = dict(zip("`1234567890-=[]\\;',./", '~!@#$%^&*()_+{}|:"<>?'))
CSI_KEYS = {"A": "up", "B": "down", "H": "home", "F": "end", "5~": "pgup", "6~": "pgdn", "Z": "shift+tab",
            "1~": "home", "7~": "home", "4~": "end", "8~": "end"}
# without the protocol: Russian letters → the keys of the US layout (not "." and ",": they are in both)
RU_US = str.maketrans("ёйцукенгшщзхъфывапролджэячсмитьбюЁЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ",
                      "`qwertyuiop[]asdfghjkl;'zxcvbnm,.~QWERTYUIOP{}ASDFGHJKL:\"ZXCVBNM<>")
PLAIN = {"\r": "enter", "\n": "enter", "\t": "tab", "\x7f": "backspace", "\x08": "backspace"}
_PARTIAL = re.compile(r"\x1b(O|\[[0-9;:<>?=]*)?$")
_FINAL = re.compile(r"[\x40-\x7e]")       # the final character of a CSI sequence
PASTE_START, PASTE_END = "\x1b[200~", "\x1b[201~"
# alternate screen, hide the cursor, no line wrap, bracketed paste
ENTER_SEQ = "\x1b[?1049h\x1b[?25l\x1b[?7l\x1b[?2004h\x1b[2J"
LEAVE_SEQ = "\x1b[0m\x1b[?2004l\x1b[?7h\x1b[?25h\x1b[?1049l"


def _chr(n: str) -> str:
    n = int(n) if n.isdigit() else 0
    return chr(n) if 0 < n < 0x110000 and not 0xD800 <= n < 0xE000 else ""


def kitty_key(params: str) -> Key | None:
    """Parse CSI … u parameters (None: skip it — a lone Shift and so on)."""
    fields = params.split(";") + ["", ""]
    code, shifted, base = (fields[0].split(":") + ["", ""])[:3]
    mods = fields[1].split(":")[0]
    mods = max(int(mods) - 1, 0) if mods.isdigit() else 0
    shift, ctrl, num = mods & 1, mods & 4, int(code) if code.isdigit() else 0
    if num in SPECIAL:
        return Key("shift+tab" if num == 9 and shift else SPECIAL[num])
    ch = _chr(code)
    if not ch or num >= 57344:
        return None
    if (ch.isascii() and ch.isalnum()) or not _chr(base):     # Latin: as is (Dvorak, QWERTZ)
        phys, up = ch, _chr(shifted) or ch.upper()
    else:                                                 # Cyrillic, punctuation: the QWERTY key
        phys = _chr(base)
        up = US_SHIFT.get(phys, phys.upper())
    if ctrl:
        return Key("ctrl+" + phys.lower())
    text = "".join(_chr(c) for c in fields[2].split(":"))
    return Key(up if shift else phys, text or ((_chr(shifted) or ch.upper()) if shift else ch))


class Term:
    def __init__(self):
        self.fd, self.out = sys.stdin.fileno(), sys.stdout.buffer
        self.buf = ""
        self.dec = codecs.getincrementaldecoder("utf-8")("replace")
        self.kitty_keys = False     # the terminal confirmed the kitty keyboard protocol
        self.sixel: bool | None = None  # it draws Sixel images (None: no answer yet)

    def __enter__(self):
        self.old = termios.tcgetattr(self.fd)
        tty.setraw(self.fd)
        # Ask about the kitty protocol (CSI ? u), then the device attributes (CSI c), which
        # every terminal answers: the protocol is supported if its reply came first.
        self.write(ENTER_SEQ + "\x1b[?u\x1b[c")
        end = time.time() + 2
        while self.sixel is None and time.time() < end:
            self.events(0.1)
        self.sixel = bool(self.sixel)   # no reply at all: no Sixel (and a lone ESC is the Esc key)
        for sig in (signal.SIGTERM, signal.SIGHUP):     # window closed: exit normally
            signal.signal(sig, lambda n, f: sys.exit(128 + n))
        return self

    def __exit__(self, *exc):
        with contextlib.suppress(SystemExit):
            self.write(("\x1b[<u" if self.kitty_keys else "") + LEAVE_SEQ)
        with contextlib.suppress(termios.error):     # the terminal may be closed already
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)

    def write(self, s: str):
        try:
            self.out.write(s.encode("utf-8", "replace"))
            self.out.flush()
        except OSError as e:     # the terminal is gone
            raise SystemExit(1) from e

    def size(self) -> tuple[int, int, int, int]:
        """(columns, rows, cell width and height in pixels)."""
        try:
            rows, cols, xp, yp = struct.unpack("HHHH", fcntl.ioctl(self.fd, termios.TIOCGWINSZ, b"\0" * 8))
        except OSError:
            rows, cols, xp, yp = 24, 80, 0, 0
        cols, rows = max(cols, 20), max(rows, 5)
        return cols, rows, xp // cols or 10, yp // rows or 20

    def events(self, timeout: float) -> list:
        """Key presses and pastes: [("key", Key), ("paste", text)]."""
        if select.select([self.fd], [], [], timeout)[0]:
            self._read()
            # a sequence may arrive in parts: wait a little for the rest
            while (_PARTIAL.search(self.buf) or (PASTE_START in self.buf and PASTE_END not in self.buf)) \
                    and select.select([self.fd], [], [], 0.03)[0]:
                self._read()
        return self._parse()

    def _read(self):
        data = os.read(self.fd, 65536)
        if not data:
            raise SystemExit(0)      # input closed
        self.buf += self.dec.decode(data)

    def _parse(self) -> list:
        ev, s, i = [], self.buf, 0
        while i < len(s):
            if s.startswith(PASTE_START, i):
                end = s.find(PASTE_END, i)
                if end < 0:
                    break                           # wait for the end of the paste
                ev.append(("paste", one_line(s[i + len(PASTE_START):end])))
                i = end + len(PASTE_END)
            elif s.startswith("\x1b[", i):
                m = _FINAL.search(s, i + 2)
                if not m:
                    break                           # the rest comes later
                e = self._csi(s[i + 2:m.start()], m.group())
                ev += [e] if e else []
                i = m.end()
            elif s.startswith("\x1bO", i) and i + 2 < len(s):     # SS3 arrows (application mode)
                e = self._csi("", s[i + 2])
                ev += [e] if e else []
                i += 3
            elif s[i:] == "\x1b" and self.sixel is None:
                break                               # start-up: the replies are still coming
            else:                                   # without the protocol: plain bytes
                ch = s[i]
                i += 1
                if self.kitty_keys or (ch == "\x1b" and i < len(s)):     # Alt+key: just the key
                    continue
                if ch in PLAIN or ch == "\x1b":
                    ev.append(("key", Key(PLAIN.get(ch, "esc"))))
                elif ch < " ":
                    ev.append(("key", Key("ctrl+" + chr(ord(ch) + 96))))
                else:
                    ev.append(("key", Key(ch.translate(RU_US), ch)))
        self.buf = s[i:]
        return ev

    def _csi(self, seq: str, fin: str):
        if seq.startswith("?"):                     # replies to the start-up questions
            if fin == "u" and not self.kitty_keys:  # the kitty protocol is supported: turn it on
                self.kitty_keys = True
                self.write(f"\x1b[>{KITTY_FLAGS}u")
            elif fin == "c":                        # device attributes: "4" means Sixel
                self.sixel = "4" in seq[1:].split(";")
            return None
        if fin == "u":
            k = kitty_key(seq)
            return ("key", k) if k else None
        name = CSI_KEYS.get(seq.split(";")[0] + "~" if fin == "~" else fin)
        return ("key", Key(name)) if name else None
