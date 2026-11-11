import re
from types import SimpleNamespace

import pytest
from harness import fixture

from raisa import images
from raisa.parse import Page
from raisa.ui import draw_marks, pane_layout
from raisa.views import ArticleView, Item, ListView, article_head


def test_list_cursor():
    lv = ListView("x", [Item("SCP-173", 1), Item("SCP-682", 2), Item("Main", "")])
    lv.move(1)
    assert lv.current().value == 2
    lv.move(10 ** 9)                                # stops at the ends
    assert lv.current().value == ""
    lv.move(-5)
    assert lv.current().value == 1
    empty = ListView("x", [])
    empty.move(1)                                   # an empty list does not break
    assert empty.current() is None


def test_columns_widths():
    assert pane_layout(160, "article", 20)["bookmarks"] == (0, 20)       # as wide as the longest title…
    assert pane_layout(160, "article", 70)["bookmarks"] == (0, 48)       # …but at most 0.3 of the window
    for W in range(70, 400, 3):
        res = pane_layout(W, "article", 1000)
        assert res["bookmarks"][1] == int(W * 0.3) and sum(w for _, w in res.values()) + 1 == W
    assert pane_layout(60, "bookmarks", 30) == {"bookmarks": (0, 60)}     # narrow: the focused column


def test_bookmarks_wrap_and_keep_the_cursor_visible():
    items = [Item(f"SCP-{i} - a rather long title of the page", ("en", f"scp-{i}")) for i in range(30)]
    app = SimpleNamespace(marks=ListView("Bookmarks", items), focus="bookmarks", current_page=lambda: None)
    rows = [re.sub(r"\x1b\[[0-9;]*m", "", r) for r in draw_marks(app, 20, 10)]
    assert rows[1].startswith(" SCP-0 - a rather") and rows[2].startswith("   long title") and len(rows) == 11
    app.marks.move(10 ** 9)                         # the last one: scrolled so that all its lines show
    rows = [re.sub(r"\x1b\[[0-9;]*m", "", r) for r in draw_marks(app, 20, 10)]
    text = "\n".join(r.rstrip() for r in rows).rstrip()
    assert "SCP-29 - a" in text and text.endswith("page")    # the whole last title is on screen


def layout(av, width, height=20):
    av.layout(width, height, (width,), article_head(av.page))


def test_article_anchor_survives_rewrap():
    av = ArticleView(Page("en", "scp-173", fixture("en_scp173.html")))
    layout(av, 40)
    target = next(j for j, ln in enumerate(av.lines) if ln.text().startswith("▏ Quoted"))
    av.top = target
    layout(av, 90)                       # a wider window: still on the same quote
    assert av.lines[av.top].text().startswith("▏ Quoted")
    layout(av, 25)
    assert av.lines[av.top].text().startswith("▏ Quoted")


def test_article_links():
    av = ArticleView(Page("en", "scp-173", fixture("en_scp173.html")))
    layout(av, 40, height=60)
    av.move_link(1)
    assert av.link == 0
    av.move_link(1)
    av.move_link(1)
    assert av.link == 2
    av.move_link(1)                      # no more links: stay
    assert av.link == 2
    av.move_link(-1)
    assert av.link == 1


def test_image_sizes():
    """An icon keeps its own size; a picture is as wide as the text and at most as high as given."""
    im = images.Images(False)
    im.state = {"icon": {"status": "ok", "w": 48, "h": 50}, "photo": {"status": "ok", "w": 400, "h": 300},
                "small": {"status": "ok", "w": 200, "h": 160}, "tall": {"status": "ok", "w": 300, "h": 2000}}
    assert im.geometry("icon", 80, 40, 10, 20) == (5, 3)        # 48 px: 5 cells
    assert im.geometry("photo", 80, 40, 10, 20) == (80, 30)     # the whole 80 cells
    assert im.geometry("small", 80, 40, 10, 20) == (80, 32)     # a small photo too
    assert im.geometry("tall", 80, 40, 10, 20) == (12, 40)      # too tall: 40 lines, narrower
    assert im.geometry("photo", 80, 20, 10, 20) == (54, 20)
    im.shutdown()


def test_image_blocks(tmp_path):
    """Without Sixel: "▀" lines, a cell for two points, colours of the top and the bottom point."""
    Image = pytest.importorskip("PIL.Image")
    im = Image.new("RGB", (4, 4), "white")
    im.paste((255, 0, 0), (0, 2, 4, 4))                       # the lower half red
    im.save(tmp_path / "a.png")
    _, frames, _ = images.cut_frames(tmp_path / "a.png", 4, 2, 10, 20, 0, sixel_on=False)
    top, bottom = frames[0]
    assert top.count("▀") == 4 and "\x1bP" not in top and top.endswith("\x1b[0m")
    assert "38;2;255;255;255;48;2;255;255;255" in top and "48;2;255;0;0" in bottom


def test_animation_frames(tmp_path):
    """A GIF keeps its length with at most 24 frames a second and 48 in all."""
    Image = pytest.importorskip("PIL.Image")
    cut_frames = images.cut_frames

    def gif(name, n, ms):
        frames = [Image.new("RGB", (8, 6), (i * 4 % 256, 0, 0)) for i in range(n)]
        frames[0].save(tmp_path / name, save_all=True, append_images=frames[1:], duration=ms, loop=0)
        return tmp_path / name
    step, frames, _ = cut_frames(gif("long.gif", 120, 50), 2, 2, 4, 3)      # 6 s at 20 frames a second
    assert len(frames) == 48 and abs(step - 6 / 48) < 1e-9 and len(frames[0]) == 2
    step, frames, n = cut_frames(gif("fast.gif", 30, 20), 2, 2, 4, 3)       # 0.6 s at 50 a second
    assert len(frames) == 14 and step >= 1 / 24                         # → 23 a second, not more than 24
    assert n == 30 and len(cut_frames(tmp_path / "fast.gif", 2, 2, 4, 3, 0)[1]) == 1    # fps 0: the first frame
    Image.new("RGB", (8, 6)).save(tmp_path / "still.png")
    assert len(cut_frames(tmp_path / "still.png", 2, 2, 4, 3)[1]) == 1


def test_heavy_animation_stays_still(tmp_path, monkeypatch):
    """A GIF whose frames are too heavy for the terminal: the first frame only, signed "GIF"."""
    Image = pytest.importorskip("PIL.Image")
    frames = [Image.new("RGB", (8, 6), (i * 40, 0, 0)) for i in range(5)]
    frames[0].save(tmp_path / "a.gif", save_all=True, append_images=frames[1:], duration=50, loop=0)
    for rate, moving in ((2 << 20, True), (10, False)):     # a normal budget; a tiny one
        monkeypatch.setattr(images, "RATE", rate)
        im = images.Images(True)
        im.state["u"] = {"status": "ok", "path": tmp_path / "a.gif", "w": 8, "h": 6}
        im._cut(("u", 2, 2, (4, 3)), tmp_path / "a.gif")
        im.poll()
        _, got, still = im.strip_cache[("u", 2, 2, (4, 3))]
        assert (len(got) > 1, still, im.info("u").get("still", False)) == (moving, not moving, not moving)
        im.shutdown()
