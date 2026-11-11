"""Text: cleaning, on-screen character width, line wrapping, output.

A character takes 0, 1 or 2 terminal cells: Latin and Cyrillic one, CJK two,
combining marks (accents) zero. That is why sw() measures a string, not len().
"""
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from .style import DIM, QUOTE, sgr

# Control and invisible characters. An ESC in page text could "command" the
# terminal, so everything from the network goes through clean().
_JUNK = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u00ad\u061c\u180e\u200b-\u200f"
                   "\u202a-\u202e\u2060-\u206f\ufeff\ufff9-\ufffb]")


def clean(s: str) -> str:
    return _JUNK.sub("", s.replace("\t", "    "))


def one_line(s: str) -> str:
    return " ".join(clean(s).split())


@lru_cache(maxsize=8192)
def cw(ch: str) -> int:
    """Character width in cells: 0 (combining marks), 1 or 2 (CJK)."""
    return 0 if unicodedata.category(ch) in ("Mn", "Me", "Cf") else 2 if unicodedata.east_asian_width(ch) in "WF" else 1


def sw(s: str) -> int:
    return len(s) if s.isascii() else sum(map(cw, s))


def cut(s: str, room: int) -> tuple[str, str]:
    """The head of the string at most `room` cells wide, and the rest."""
    used = 0
    for i, ch in enumerate(s):
        used += cw(ch)
        if used > room:
            return s[:i], s[i:]
    return s, ""


@dataclass(slots=True)
class Para:
    """A paragraph of a page."""
    runs: list                  # pieces [text, attrs, link number]
    first: str = ""             # indent of the first line
    rest: str = ""              # indent of the other lines
    kind: str = "text"          # text | pre | blank | hr | img
    img: int | None = None      # image number

    def text(self) -> str:
        return "".join(r[0] for r in self.runs)


@dataclass(slots=True)
class Line:
    """A screen line."""
    segs: list                  # pieces, like Para.runs
    para: int                   # paragraph number: to come back to the same place after re-wrapping
    img: int | None = None      # image number
    pic: tuple | None = None    # (row, cols, rows) of a shown image

    def text(self) -> str:
        return "".join(s[0] for s in self.segs)


# CJK is written without spaces: a line may break after any character.
_CJK = ("\u2e80-\u2fff\u3000-\u303f\u3040-\u30ff\u3100-\u31ff\u3400-\u4dbf\u4e00-\u9fff"
        "\uf900-\ufaff\ufe30-\ufe4f\uff00-\uffef\U00020000-\U0002fa1f")
_TOKEN = re.compile(rf"(\s+)|([{_CJK}])|([^\s{_CJK}]+)")


def _words(runs):
    """Words (lists of pieces, so a link and the comma after it stay together) and
    spaces — tuples (attrs, link), so a link underline continues over its spaces."""
    out, word = [], []
    for text, a, link in runs:
        for space, cjk, plain in _TOKEN.findall(text):
            if plain:
                word.append([plain, a, link])
                continue
            if word:
                out.append(word)
                word = []
            out.append((a, link) if space else [[cjk, a, link]])
    return out + [word] if word else out


def _wrap(p: Para, pi: int, width: int) -> list[Line]:
    lines, cur, used = [], [], 0            # finished lines; pieces and cells of the current one
    quote = QUOTE if "▏" in p.first + p.rest else 0

    def room():
        return max(width - sw(p.rest if lines else p.first), 10)

    def newline():
        nonlocal used
        lines.append(Line([[p.rest if lines else p.first, quote, None], *cur], pi, p.img))
        cur.clear()
        used = 0

    def put(t, a, link):
        """Add a piece; wrap if it does not fit, cut it if it is longer than a line."""
        nonlocal used
        while t:
            head, t = cut(t, room() - used)
            if not head:
                if used:
                    newline()
                    continue
                head, t = t[0], t[1:]       # a character wider than the line: output it anyway
            if cur and cur[-1][1:] == [a, link]:
                cur[-1][0] += head
            else:
                cur.append([head, a, link])
            used += sw(head)
            if t:
                newline()

    if p.kind == "pre":                     # code: lines as is, long ones are cut
        for t, a, link in p.runs:
            for j, part in enumerate(t.split("\n")):
                if j:
                    newline()
                put(part, a, link)
        newline()
        return lines
    space = None
    for w in _words(p.runs):
        if isinstance(w, tuple):
            space = w if used else None
            continue
        if used and used + bool(space) + sum(sw(t) for t, _, _ in w) > room():
            newline()
            space = None
        if space:
            put(" ", *space)
            space = None
        for piece in w:
            put(*piece)
    if cur or not lines:
        newline()
    return lines


@lru_cache(maxsize=4096)
def wrap_plain(text: str, width: int, indent: str = "") -> tuple[str, ...]:
    """Plain text as lines `width` cells wide; lines after the first start with `indent`."""
    return tuple(ln.text() for ln in _wrap(Para([[text, 0, None]], "", indent), 0, width))


def wrap(paras: list[Para], width: int, image_geometry=None, image_note=None) -> list[Line]:
    """Lay paragraphs out into lines `width` cells wide.

    image_geometry(n) -> (cols, rows) or None: a ready image gets that many lines, with a
    blank line above and below. image_note(n) -> a note next to the placeholder ("loading…"),
    or under a shown image.
    """
    width = max(width, 20)
    lines: list[Line] = []
    pad = False
    for pi, p in enumerate(paras):
        if pad and p.kind != "blank":
            lines.append(Line([], pi))
        pad = False
        if p.kind == "blank":
            lines.append(Line([], pi))
        elif p.kind == "hr":
            lines.append(Line([[p.first, 0, None], ["─" * max(width - sw(p.first), 1), DIM, None]], pi))
        elif p.kind == "img" and image_geometry and (geo := image_geometry(p.img)):
            if lines and (lines[-1].segs or lines[-1].pic):
                lines.append(Line([], pi))
            lines += [Line([], pi, p.img, (r, *geo)) for r in range(geo[1])]
            if image_note and (note := image_note(p.img)):      # under a shown image: "GIF: i to watch"
                lines.append(Line([[p.first, 0, None], [note, DIM, None]], pi))
            pad = True
        else:
            note = image_note(p.img) if p.kind == "img" and image_note else ""
            if note:
                p = Para(p.runs + [[f" ({note})", DIM, None]], p.first, p.rest, "img", p.img)
            lines += _wrap(p, pi, width)
    return lines


def fit(segs, width: int, fill=0, ell=False) -> str:
    """A line exactly `width` cells wide; fill — attrs of the tail, ell — "…" when cut."""
    out, used = [], 0
    for k, (text, a, _) in enumerate(segs):
        if used >= width:
            break
        if not text.isprintable():      # the last guard against control characters
            text = "".join(ch if ch.isprintable() else " " * ch.isspace() for ch in text)
        piece, rest = cut(text, width - used)
        if ell and sw(piece) == width - used and (rest.strip() or any(s[0].strip() for s in segs[k + 1:])):
            piece = cut(piece, width - used - 1)[0] + "…"
        out.append(sgr(a) + piece)
        used += sw(piece)
        if rest:
            break
    return "".join(out) + sgr(fill) + " " * (width - used) + "\x1b[0m"
