"""End-to-end: the real program in a virtual terminal (needs python-pyte).

The site is a local server with test pages; any internet access is blocked by a
non-existent proxy, so the tests never go online.
"""
import io

import pytest

pytest.importorskip("pyte")

from harness import FakeSite, TuiProcess, fixture, tiny_png  # noqa: E402

ARTICLE = fixture("en_scp173.html")
PAGES = {
    "/": (200, fixture("en_main.html")),
    "/scp-173": (200, ARTICLE),
    "/scp-174": (200, ARTICLE.replace("SCP-173", "SCP-174").replace("+9876", "+42")),
    "/scp-172": (200, ARTICLE.replace("SCP-173", "SCP-172")),
}


@pytest.fixture
def site():
    s = FakeSite(dict(PAGES))
    yield s
    s.close()


@pytest.fixture
def run(site, tmp_path):
    procs = []

    def start(*extra, cols=130, rows=30):
        tui = TuiProcess(["-b", "en", *extra], tmp_path, site.url, cols=cols, rows=rows)
        procs.append(tui)
        assert tui.wait_for("Welcome to the SCP") or extra, tui.screen_text()
        return tui

    yield start
    for p in procs:
        if p.proc.poll() is None:
            p.send("q", 0.3)
            p.exited()
        text = p.screen_text()
        p.close()
        assert "Traceback" not in text, text        # errors are printed after exit


def col(tui, y, n=1):
    """Row y of column n (0 bookmarks, 1 page); the whole row in a one-column window."""
    parts = tui.line(y).split("▎")
    return parts[n] if len(parts) > n else parts[0]


def header(tui):
    """The top line: "SCP Foundation", the branch, messages, the selected link."""
    return tui.line(0)


def goto(tui, text, title):
    tui.send("/")
    tui.send(text + "\r", 0.5)
    assert tui.wait_until(lambda: col(tui, 1).lstrip().startswith(title)), tui.screen_text()


def test_start_columns(run):
    tui = run()
    assert "SCP Foundation - en" in header(tui) and header(tui).rstrip().endswith("/")   # the page address
    assert col(tui, 1).lstrip().startswith("Welcome")               # first run: the main page
    assert "Bookmarks" in col(tui, 1, 0) and "(empty)" in col(tui, 2, 0)
    goto(tui, "173", "SCP-173")
    assert "rating: +9876" in tui.screen_text()


def test_kitty_keyboard_protocol(run):
    tui = run()
    assert b"\x1b[>29u" in tui.raw   # the "terminal" confirmed the protocol: it is on
    tui.send("\x1b[1090::110u", 1.1)  # «т» on the physical N key: forward (nowhere to go)
    assert "last page" in header(tui)
    tui.send("\x1b[46::47u")         # «.» on the physical "/" key: the search line
    assert "URL or words" in tui.screen_text()
    tui.send("\x1b[1089::99;1;1089u")    # «с» is typed as «с»
    assert "/ с" in tui.screen_text()
    tui.send("\x1b[27u")
    tui.send("\x1b[1081::113u", 0.5)     # й = q
    assert tui.exited() == 0
    assert b"\x1b[<u" in tui.raw and b"\x1b[?1049l" in tui.raw


def test_narrow_window_columns(run):
    tui = run()
    goto(tui, "172", "SCP-172")
    tui.resize(100, 20)                      # no bookmarks: their column is as narrow as its title
    tui.pump(0.4)
    assert tui.line(1).count("▎") == 1 and tui.line(1).index("▎") == len(" Bookmarks 0 ")
    tui.resize(55, 20)                       # narrow: only the focused column
    tui.pump(0.4)
    assert tui.line(1).lstrip().startswith("SCP-172") and "▎" not in tui.line(1)
    tui.send("\t")
    assert tui.line(1).lstrip().startswith("Bookmarks")
    tui.send("l")
    tui.resize(170, 40)
    tui.pump(0.4)
    assert "Bookmarks" in tui.line(1) and "SCP-172" in tui.line(1)


def test_offline_start(site, tmp_path):
    """The site does not answer: a clear message, no crash."""
    site.close()
    tui = TuiProcess(["-b", "en"], tmp_path, site.url)
    try:
        assert tui.wait_for("no connection", timeout=15) or tui.wait_for("Error", timeout=1)
        tui.send("q", 0.5)
        assert tui.exited() == 0
    finally:
        tui.close()


def image_page(site):
    site.pages["/img.png"] = (200, tiny_png(40, 30))
    site.pages["/pic"] = (200, '<div id="page-title">Pic</div><div id="page-content">'
                               '<p>before</p><img src="/img.png" alt="red"/><p>after</p>'
                               + "<p>more</p>" * 40 + "</div>" + " " * 300)


def test_sixel_inline_image(site, tmp_path):
    """A terminal that draws Sixel (foot): an image goes as Sixel strips after the text and
    again on scrolling."""
    pytest.importorskip("PIL")
    image_page(site)
    tui = TuiProcess(["-b", "en", "pic"], tmp_path, site.url, sixel=True)
    try:
        assert tui.wait_until(lambda: b"\x1bP0;1;0q" in tui.raw, timeout=10)
        tui.pump(0.3)
        frame = tui.raw[tui.raw.rfind(b"\x1b[?2026h"):]
        strips = frame.count(b"\x1bP0;1;0q")
        assert strips >= 2 and frame.count(b'"1;1;') == strips          # one strip per line
        assert "after" in tui.screen_text()
        n = tui.raw.count(b"\x1bP0;1;0q")
        tui.send("j", 0.4)                                              # scrolling sends the strips again
        assert tui.raw.count(b"\x1bP0;1;0q") > n
        tui.send("q", 0.5)
        assert tui.exited() == 0
    finally:
        tui.close()


def test_animation_redraws_itself(site, tmp_path):
    """An animated GIF on screen: its strips are sent again on a timer, without keys and
    without the text around."""
    Image = pytest.importorskip("PIL.Image")
    buf, frames = io.BytesIO(), [Image.new("RGB", (40, 30), (i * 40, 0, 0)) for i in range(6)]
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=50, loop=0)
    site.pages["/anim.gif"] = (200, buf.getvalue())
    site.pages["/anim"] = (200, '<div id="page-title">Anim</div><div id="page-content"><p>before</p>'
                                '<img src="/anim.gif" alt="anim"/><p>after</p></div>' + " " * 300)
    tui = TuiProcess(["-b", "en", "anim"], tmp_path, site.url, sixel=True)
    try:
        assert tui.wait_until(lambda: b"\x1bP0;1;0q" in tui.raw, timeout=10)
        tui.pump(0.3)
        n, mark = tui.raw.count(b"\x1bP0;1;0q"), len(tui.raw)
        tui.pump(0.5)                                               # no keys pressed
        ticks = tui.raw[mark:]
        assert tui.raw.count(b"\x1bP0;1;0q") > n + 4               # redrawn several times
        assert b"before" not in ticks and b"after" not in ticks     # the strips alone
        tui.send("q", 0.5)
        assert tui.exited() == 0
    finally:
        tui.close()


def test_no_sixel_image_in_characters(site, tmp_path):
    """A terminal without Sixel: the image is drawn with "▀" characters, no Sixel is sent."""
    image_page(site)
    tui = TuiProcess(["-b", "en", "pic"], tmp_path, site.url)
    try:
        assert tui.wait_for("▀▀▀▀")
        tui.send("j", 0.4)
        assert b"\x1bP" not in tui.raw and "after" in tui.screen_text()
        tui.send("q", 0.5)
        assert tui.exited() == 0
    finally:
        tui.close()


def test_terminal_without_kitty_protocol(site, tmp_path):
    """No kitty keyboard protocol (xterm, cool-retro-term): plain bytes and the usual sequences."""
    tui = TuiProcess(["-b", "en"], tmp_path, site.url, kitty=False)
    try:
        assert tui.wait_until(lambda: col(tui, 1).lstrip().startswith("Welcome"))
        assert b"\x1b[>29u" not in tui.raw
        tui.send(b"/")
        tui.send(b"173\r", 0.5)
        assert tui.wait_until(lambda: col(tui, 1).lstrip().startswith("SCP-173")), tui.screen_text()
        tui.send(b"\x1b[6~\x1b[5~")                  # PgDn, PgUp
        tui.send("Т".encode(), 0.3)                   # Russian layout: «Т» is N, back
        assert tui.wait_until(lambda: col(tui, 1).lstrip().startswith("Welcome")), tui.screen_text()
        tui.send(b"/")
        tui.send("с".encode())                        # typed as «с» in the search line
        assert "/ с" in tui.screen_text()
        tui.send(b"\x1b", 0.3)                        # a lone ESC: Esc
        tui.send("й".encode(), 0.5)                   # й = q
        assert tui.exited() == 0 and b"\x1b[?1049l" in tui.raw
    finally:
        tui.close()
