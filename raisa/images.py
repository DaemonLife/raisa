"""Images: shown in the text if pillow is installed — via Sixel if the terminal draws it
(foot), otherwise with "▀" characters in 24-bit colour, a cell for two points (the top one in
the text colour, the bottom one in the background).
`i` opens an image in an external viewer.

Sixel is pixels right in the output stream (ESC P … q data ESC \\). The terminal
draws them over the cells from the cursor position; the image does not scroll with
the text, and text printed over it erases it. So an image is scaled once (in the
background) to the needed number of cells and cut into strips one text line high
(cut_frames), and ui.render outputs the visible strips again in every frame. This way
an image scrolls line by line and can be partly visible. An animation (GIF) shows its first
frame at once; if a frame is light enough, the other frames are cut in the background: at most
MAX_FRAMES in all and as many a second as RATE allows (up to FPS), taken evenly over its length,
as video players drop frames. While one is on screen its strips are redrawn on a timer. A heavy
animation (a long photo GIF) stays still, signed "GIF", and `i` opens it.
"""
import bisect
import math
import queue
import re
import shutil
import subprocess
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import ICON_PX, IMAGE_WORKERS
from .net import HttpError, NotAnImage, NotFound, fetch_image

try:
    from PIL import Image, ImageFile  # python-pillow: without it only the external viewer
    ImageFile.LOAD_TRUNCATED_IMAGES = True  # show as much of a truncated JPEG as there is
except ImportError:  # pragma: no cover
    Image = None

STRIP_CACHE = 200           # cut frames kept in memory (an image is one frame)
FPS, MAX_FRAMES = 24, 48    # animations: at most 24 frames a second, 48 frames in all
RATE, MIN_FPS = 2 << 20, 6  # at most 2 MB of Sixel a second; slower than 6 frames a second — still
_SIXEL_CHARS = bytes((n + 63) & 0xFF for n in range(256))   # 6 pixels of a column → a character
_RUN = re.compile(rb"(.)\1{3,}")                             # repeats → !<count><char>


def sixel(data: bytes, w: int, h: int, palette: list[int]) -> str:
    """Encode an image (colour index of every pixel by rows; palette — RGB triples).

    Sixel goes in bands 6 pixels high: for every colour of a band a row of characters
    whose 6 bits tell which pixels of the column have that colour; "$" returns to the
    band start (next colour), "-" moves to the next band.
    """
    out = [f'\x1bP0;1;0q"1;1;{w};{h}']        # size in pixels
    out += [f"#{c};2;" + ";".join(str(v * 100 // 255) for v in palette[3 * c:3 * c + 3]) for c in sorted(set(data))]
    for y in range(0, h, 6):
        bands: dict[int, bytearray] = {}
        for dy in range(min(6, h - y)):
            for x, c in enumerate(data[(y + dy) * w:(y + dy + 1) * w]):
                bands.setdefault(c, bytearray(w))[x] |= 1 << dy
        rows = (bytes(b).translate(_SIXEL_CHARS).rstrip(b"?") for b in bands.values())
        out.append("$".join(f"#{c}" + _RUN.sub(lambda m: b"!%d%c" % (len(m[0]), m[0][0]), r).decode()
                            for c, r in zip(bands, rows)) + "-")
    return "".join(out) + "\x1b\\"


def blocks(im, cols: int, rows: int) -> list[str]:
    """An RGB image, already cols × rows*2 points, as `rows` lines of "▀" characters in 24-bit colour."""
    px = im.load()
    return ["".join("\x1b[38;2;{};{};{};48;2;{};{};{}m▀".format(*px[x, 2 * y], *px[x, 2 * y + 1]) for x in range(cols))
            + "\x1b[0m" for y in range(rows)]


def cut_frames(path: Path, cols: int, rows: int, cellw: int, cellh: int, fps: float = FPS, sixel_on: bool = True
           ) -> tuple[float, list[list[str]], int]:
    """(seconds per frame, frames, frames in the file): the image on cols×rows cells, every frame
    cut into `rows` strips (Sixel, or "▀" lines if not sixel_on). An animation keeps its length
    with fewer frames (fps, MAX_FRAMES); fps=0: only the first frame."""
    with Image.open(path) as im:
        n, ends, total = getattr(im, "n_frames", 1), [], 0.0
        for i in range(n if fps else 0):        # when each frame ends (decodes them all: not for fps=0)
            im.seek(i)
            total += max(im.info.get("duration", 100), 20) / 1000
            ends.append(total)
        k = max(1, min(n, MAX_FRAMES, int(total * fps)))
        frames = []
        for j in range(k):                      # the frame on screen at time j·total/k
            im.seek(min(bisect.bisect_right(ends, j * total / k), n - 1))
            frames.append(_cut_frame(im, cols, rows, cellw, cellh, sixel_on))
    return total / k, frames, n


def _cut_frame(im, cols: int, rows: int, cellw: int, cellh: int, sixel_on: bool = True) -> list[str]:
    """One frame cut into strips. Transparent parts are white, as on the site's page: a black
    icon must not vanish on a dark terminal."""
    size = (cols, rows * 2) if not sixel_on else (cols * cellw, rows * cellh)
    im = im.convert("RGBA").resize(size, Image.LANCZOS)      # smaller first: the rest is faster
    im = Image.alpha_composite(Image.new("RGBA", im.size, "white"), im).convert("RGB")
    if not sixel_on:
        return blocks(im, cols, rows)
    pal = im.quantize(colors=256)
    palette, data, step = (pal.getpalette() or []) + [0] * 768, pal.tobytes(), cols * cellw * cellh
    return [sixel(data[r * step:(r + 1) * step], cols * cellw, cellh, palette) for r in range(rows)]


def drawable(path: Path) -> Path:
    """pillow cannot read SVG: convert it to PNG next to it with rsvg-convert (librsvg),
    if it is installed; otherwise the file stays for `i`."""
    with path.open("rb") as f:          # the start is enough: images can be big
        head = f.read(2048)
    if b"<svg" not in head or not shutil.which("rsvg-convert"):
        return path
    png = path.with_suffix(".svg.png")
    if not png.exists():
        subprocess.run(["rsvg-convert", "-o", png, path], check=True, timeout=30, capture_output=True)
    return png


class Images:
    """Image state: url → {"status": loading | ok | file | error, ...}. ok — shown in the text,
    size (w, h) known; file — downloaded but not shown (no Sixel, or a format pillow cannot
    draw, such as SVG): `i` opens it; error — not downloaded, the reason in err."""

    def __init__(self, sixel: bool):
        self.shown = Image is not None              # images are drawn in the text
        self.sixel = sixel
        self.state: dict[str, dict] = {}
        self.strip_cache: OrderedDict = OrderedDict()   # (url, cols, rows, cell) → strips
        self._done: queue.Queue = queue.Queue()         # results from background threads
        self._pool = ThreadPoolExecutor(max_workers=IMAGE_WORKERS)
        self._open_when_ready: set[str] = set()
        self.moving = False                             # an animation was drawn in the last frame

    def request(self, url: str, referer: str, open_after: bool = False):
        if open_after:
            self._open_when_ready.add(url)
        if self.info(url).get("status") in (None, "error"):
            self.state[url] = {"status": "loading"}
            self._pool.submit(self._work, url, referer)

    def _work(self, url, referer):
        st: dict = {"status": "error"}
        try:
            st["path"] = path = fetch_image(url, referer)
            st["status"] = "file"
            if self.shown:
                st["path"] = path = drawable(path)
                with Image.open(path) as im:
                    st.update(status="ok", w=im.width, h=im.height)
        except Exception as e:      # not fatal: a downloaded file pillow cannot draw stays "file"
            if st["status"] != "file":
                st["err"] = ("404" if isinstance(e, NotFound) else f"HTTP {e.code}" if isinstance(e, HttpError)
                             else "not an image" if isinstance(e, NotAnImage) else "network" if isinstance(e, OSError)
                             else type(e).__name__)
        self._done.put((url, st))

    def _cut(self, key, path):
        """The first frame at once; the rest of a light animation later. The result is
        (seconds per frame, frames, a still animation?)."""
        try:
            _, first, n = cut_frames(path, *key[1:3], *key[3], 0, self.sixel)
            fps = min(FPS, RATE / max(1, sum(map(len, first[0]))))
            self._done.put((key, (1.0, first, n > 1 and fps < MIN_FPS)))
            if n > 1 and fps >= MIN_FPS:
                self._done.put((key, (*cut_frames(path, *key[1:3], *key[3], fps, self.sixel)[:2], False)))
        except Exception:
            self._done.put((key, ()))       # failed: poll() leaves the image to `i`

    def poll(self) -> tuple[bool, list]:
        """Collect results → (anything changed?, what to open in the external viewer)."""
        changed, to_open = False, []
        while not self._done.empty():
            key, res = self._done.get()
            changed = True
            if isinstance(key, tuple):      # cut strips
                self.strip_cache[key] = res
                if not res:
                    self.state[key[0]]["status"] = "file"
                elif res[2]:
                    self.state[key[0]]["still"] = True     # a heavy animation: signed "GIF"
                while sum(len(v[1]) for v in self.strip_cache.values() if v) > STRIP_CACHE:
                    self.strip_cache.popitem(last=False)
            else:
                self.state[key] = res
                if key in self._open_when_ready:
                    self._open_when_ready.discard(key)
                    to_open.append(res.get("path") or key)
        return changed, to_open

    def info(self, url) -> dict:
        """{"status", "path", "err", "w", "h"}, whatever is known of the image."""
        return self.state.get(url, {})

    def geometry(self, url, width, maxrows, cellw, cellh, small=False) -> tuple[int, int] | None:
        """(cols, rows) of a ready image: a picture as wide as the text (width) and at most
        maxrows high — a taller one is scaled down whole, so it is narrower; an icon (ICON_PX)
        in its own size; a small one (a link: a logo, a button) too, but at most ¼ of the text."""
        st = self.info(url)
        if st.get("status") != "ok" or not st["w"] or not st["h"]:
            return None
        w, h = st["w"], st["h"]
        icon = small or max(w, h) <= ICON_PX
        cols = min(width // 4 if small else width, math.ceil(w / cellw)) if icon else width
        rows = max(1, math.ceil(h * cols * cellw / w / cellh))
        if rows > max(1, maxrows):
            rows = max(1, maxrows)
            cols = max(1, min(cols, math.ceil(w * rows * cellh / h / cellw)))
        return cols, rows

    def strip(self, url, cols, rows, row, cell, now: float, moving_only=False) -> str:
        """Strip `row` in Sixel of the frame shown at time `now`; "" while it is being cut (cutting
        starts in the background), or if moving_only and the image does not move. An animation on
        screen sets `moving`: redraw it on a timer."""
        key = (url, cols, rows, cell)
        if key not in self.strip_cache:
            self.strip_cache[key] = None            # "being cut"
            self._pool.submit(self._cut, key, self.state[url]["path"])
        got = self.strip_cache[key]
        self.strip_cache.move_to_end(key)
        if not got:
            return ""
        step, frames, _ = got
        self.moving |= len(frames) > 1
        if moving_only and len(frames) == 1:
            return ""
        frame = frames[int(now / step) % len(frames)]
        return frame[row] if row < len(frame) else ""

    def shutdown(self):
        self._pool.shutdown(wait=False, cancel_futures=True)
