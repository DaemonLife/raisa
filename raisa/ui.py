"""Drawing. A frame is built as one string and written at once (no flicker): the header
(with messages), the columns (bookmarks ▎ page), the search line at the bottom."""
import time

from .style import BAR, BAR_OFF, CUR, DIM, ERR, INFO, LINK, SEL, B, sgr
from .text import fit, sw, wrap_plain
from .views import article_head

TEXT_WIDTH = 80      # article text width: about 80 characters is the most comfortable to read
ARTICLE_MIN = 46     # a narrower page: only the focused column
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
EMPTY = " (empty)"     # in the bookmarks column while there are none


def pane_layout(W: int, focus: str, want: int = 0) -> dict[str, tuple[int, int]]:
    """{column: (x, width)}: the bookmarks and the page. The bookmarks are as wide as they want (the
    longest title), but at most 0.3 of the window. A narrow window shows only the focused column."""
    side = min(want, int(W * 0.3))
    if W - side - 1 < ARTICLE_MIN:
        return {focus: (0, max(1, W))}
    return {"bookmarks": (0, side), "article": (side + 1, W - side - 1)}


def _title(text: str, right: str, w: int, focused: bool) -> str:
    """A column title: text on the left, `right` on the right."""
    attr, right = BAR | B if focused else BAR_OFF, f" {right} " if right else ""
    room = max(w - sw(right), 1)
    return fit([(text, attr, None)], room, attr, True) + fit([(right, attr, None)], w - room, attr)


def draw_marks(app, w: int, body: int) -> list[str]:
    """The bookmarks, long titles wrapped; the open page in colour; dim while the text is focused."""
    lv, focused = app.marks, app.focus == "bookmarks"
    blocks = [wrap_plain(it.label, w - 1, "  ") for it in lv.items]
    lv.top = min(lv.top, lv.sel)            # the first bookmark on screen: the cursor stays visible
    while lv.top < lv.sel and sum(map(len, blocks[lv.top:lv.sel + 1])) > body:
        lv.top += 1
    rows = [_title(f" {lv.title}", str(len(lv.items)), w, focused)]
    if not lv.items:
        rows.append(fit([(EMPTY, DIM, None)], w))
    for j in range(lv.top, len(lv.items)):
        if len(rows) > body:
            break
        sel = SEL if focused and j == lv.sel else 0
        attr = sel | CUR * (lv.items[j].value == app.current_page()) | DIM * (not focused)
        rows += [fit([(" " + line, attr, None)], w, sel, True) for line in blocks[j]]
    return (rows + [fit([], w)] * body)[:body + 1]


def draw_article(app, w: int, body: int, pics: list) -> list[str]:
    av, images, focused = app.art, app.images, app.focus == "article"
    if av is None:      # nothing opened (offline and so on)
        return [_title("", "", w, focused)] + [fit([], w)] * body
    pg = av.page
    tw = max(min(w - 3, TEXT_WIDTH), 20)            # centred text; the scroll bar on the right
    margin = max(1, (w - 1 - tw) // 2)

    def geometry(idx):      # a picture as wide as the text and at most ⅔ of the page high
        url, _, small = pg.images[idx]
        return images.geometry(url, tw, body * 2 // 3, *app.cell, small) if images.shown else None

    def note(idx):          # "loading…", why it failed, "press i"; under a still animation "GIF"
        st = images.info(pg.images[idx][0])
        if st.get("status") == "ok":
            return "GIF: too heavy to play here, press i to watch" if st.get("still") else ""
        if st.get("status") == "error":
            return f"failed to load: {st['err']}"
        return "loading…" if st.get("status") == "loading" else "press i to open"

    states = tuple((images.info(u).get("status"), images.info(u).get("still")) for u, *_ in pg.images)
    av.layout(tw, body, (tw, app.cell, states), article_head(pg), geometry, note)
    pos = f"{app.pos + 1}/{len(app.trail)} · " if len(app.trail) > 1 else ""
    mark = "bookmarked · " if app.store.is_bookmarked(pg.branch, pg.slug) else ""
    rows = [_title(f" {pg.title}", f"{mark}{pos}{av.percent()}%", w, focused)]
    total = len(av.lines)           # the scroll bar thumb: the visible part of the article
    size = max(1, body * body // total) if total > body else 0
    start = (body - size) * av.top // max(1, av.max_top())
    for i in range(body):
        bar = sgr(DIM) + ("▌" if start <= i < start + size else " ") + "\x1b[0m"
        ln = av.lines[av.top + i] if av.top + i < total else None
        if ln and ln.pic:           # keep image space empty: render() outputs the Sixel strip
            r, cols, nrows = ln.pic
            pics.append((i + 1, margin + (tw - cols) // 2, pg.images[ln.img][0], cols, nrows, r))
        if not ln or ln.pic:
            rows.append(" " * (w - 1) + bar)
            continue
        dim = DIM * (not focused)
        segs = [(t, a | dim | SEL * (link is not None and link == av.link), link) for t, a, link in ln.segs]
        rows.append(" " * margin + fit(segs, w - 1 - margin) + bar)
    return rows


def draw_header(app, W: int) -> str:
    """"SCP Foundation" and the branch; on the right loading, a message, or where the selected
    link leads (the page address otherwise). The background shows the object class."""
    av = app.art
    bg = av.page.class_bg if av else 0
    if app.busy_label:
        right, attr = f"{SPINNER[int(time.time() * 10) % len(SPINNER)]} {app.busy_label}…  Esc to cancel", INFO
    elif app.msg[0]:
        right, attr = app.msg[0], ERR if app.msg[2] else INFO
    elif av:
        right, attr = ("→ " + av.page.links[av.link][0], LINK) if av.link is not None else (av.page.url, DIM)
    else:
        right, attr = "", 0
    # on a class colour all text is plain black: bold black is grey in many themes ("bold is bright")
    left = [(" SCP Foundation", bg or B, None),
            (f" - {app.branch}", bg or DIM, None)]
    rw = max(0, min(sw(right) + 2, W - 22))      # the left part takes about 20 cells
    return fit(left, W - rw, bg) + fit([(right + " ", bg or attr, None)], rw, bg, True)


def draw_search(app, W: int, H: int) -> str:
    """The search line at the bottom: "/" and the input, a hint while it is empty."""
    text = app.search[-W:]
    while sw(text) > W - 4:                     # long input: show its end
        text = text[1:]
    segs = [(" /", B, None), (" " + text, 0, None), (" ", SEL, None)] + [(" 173, URL or words", DIM, None)] * (not text)
    return f"\x1b[{H};1H" + fit(segs, W)


def draw_pics(app, moving_only=False) -> str:
    """Image strips at their places (app.pics). Text erases Sixel under it, so they go after the
    text in every frame; on an animation tick only the moving ones go, at the frame of this moment."""
    now = time.monotonic()
    return "".join(f"\x1b[{y};{x}H" + s for y, x, url, cols, rows, r in app.pics
                   if (s := app.images.strip(url, cols, rows, r, app.cell, now, moving_only)))


def render(app) -> str:
    """Build a frame."""
    W, H, cwpx, chpx = app.term.size()
    app.cell, app.body_h, body = (cwpx, chpx), H - 2, H - 2
    want = max([sw(f" {app.marks.title} {len(app.marks.items)} "), sw(EMPTY)]
               + [sw(it.label) + 2 for it in app.marks.items])     # the longest title and the margins
    panes = pane_layout(W, app.focus, want)
    pics: list = []         # image strips: (page row, offset, url, cols, rows, strip number)
    draw = {"bookmarks": lambda w: draw_marks(app, w, body), "article": lambda w: draw_article(app, w, body, pics)}
    cols = [draw[name](w) for name, (_, w) in panes.items()]
    out = ["\x1b[?2026h\x1b[H", draw_header(app, W)]     # after the article: its class is known
    x = panes.get("article", (0, 0))[0]
    # Not on the last screen row: after a Sixel image the cursor goes below it, and the screen would scroll.
    app.pics = [(y + 2, x + left + 1, *rest) for y, left, *rest in pics if y + 2 < H]
    sep = sgr(DIM) + "▎\x1b[0m"      # block elements, not "│": fonts draw them full height, so lines are solid
    out += [f"\x1b[{y + 2};1H" + sep.join(c[y] for c in cols) for y in range(body + 1)]
    out.append(draw_pics(app))
    out.append((draw_search(app, W, H) if app.search is not None else "") + "\x1b[?2026l")
    return "".join(out)
