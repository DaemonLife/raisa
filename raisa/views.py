"""Screen state: a list with a cursor (the bookmarks), a scrolled article.
Drawing lives in ui.py."""
from typing import NamedTuple

from .parse import Page
from .style import DIM
from .text import Line, Para, wrap


class Item(NamedTuple):
    """A bookmark in the list: its title and (branch, slug)."""
    label: str
    value: object


class ListView:
    def __init__(self, title: str, items: list[Item]):
        self.title, self.items = title, items
        self.sel = self.top = 0         # selected and first visible item
        self.move(0)

    def current(self) -> Item | None:
        return self.items[self.sel] if 0 <= self.sel < len(self.items) else None

    def move(self, delta: int):
        self.sel = max(0, min(self.sel + delta, len(self.items) - 1))


class ArticleView:
    """An article on screen: wrapped lines, scrolling, the selected link."""

    def __init__(self, page: Page):
        self.page = page
        self.lines: list[Line] = []
        self.top, self.height = 0, 10       # first visible line, number of visible lines
        self.link: int | None = None        # selected link (index in page.links)
        self.links_at: list[tuple[int, int]] = []   # (line, link) in order
        self._key, self._anchor = None, (0, 0)

    def layout(self, width, height, key, head_paras, image_geometry=None, image_note=None):
        """Re-wrap the text if something changed (width, images), staying on the same
        paragraph (anchor/restore)."""
        self.height = max(height, 1)
        if key != self._key:
            anchor = self.anchor()
            self.lines = wrap(head_paras + self.page.paras, width, image_geometry, image_note)
            self.links_at = list(dict.fromkeys((j, link) for j, ln in enumerate(self.lines)
                                               for _, _, link in ln.segs if link is not None))
            self._key = key
            self.restore(anchor)
        self.scroll(0)

    def anchor(self) -> tuple[int, int]:
        """Reading position: (paragraph of the top line, how many of its lines are above)."""
        if not self.lines:
            return self._anchor
        top = min(self.top, len(self.lines) - 1)
        para = self.lines[top].para
        return para, sum(1 for ln in self.lines[:top] if ln.para == para)

    def restore(self, anchor: tuple[int, int]):
        self._anchor = anchor
        para, offset = anchor
        first = next((j for j, ln in enumerate(self.lines) if ln.para >= para), len(self.lines))
        same = sum(1 for ln in self.lines[first:first + offset + 1] if ln.para == para)
        self.top = first + max(0, min(offset, same - 1))
        self.scroll(0)

    def max_top(self) -> int:
        return max(0, len(self.lines) - self.height)

    def scroll(self, delta: int):
        self.top = max(0, min(self.top + delta, self.max_top()))

    def percent(self) -> int:
        return 100 if self.max_top() == 0 else self.top * 100 // self.max_top()

    def visible(self) -> range:
        return range(self.top, min(self.top + self.height, len(self.lines)))

    def move_link(self, step: int):
        """J/K: next/previous link; if the selected one is off screen — the first (last) visible."""
        occ, vis = self.links_at, self.visible()
        if not occ:
            return
        cur = next((n for n, (j, link) in enumerate(occ) if link == self.link and j in vis), None)
        if cur is None:
            near = [n for n, (j, _) in enumerate(occ) if j >= self.top] if step > 0 else \
                   [n for n, (j, _) in enumerate(occ) if j < vis.stop][::-1]
            n = near[0] if near else (len(occ) - 1 if step > 0 else 0)
        else:
            n = cur + step
            while 0 <= n < len(occ) and occ[n][1] == self.link:   # a link spanning several lines
                n += step
            if not 0 <= n < len(occ):
                return
        j, self.link = occ[n]
        if not self.top <= j < self.top + self.height:
            self.top = j - 2 if j < self.top else j - self.height + 3
        self.scroll(0)

    def visible_images(self) -> list[int]:
        return sorted({self.lines[j].img for j in self.visible() if self.lines[j].img is not None})


def article_head(page: Page) -> list[Para]:
    """The "rating · tags" line above the text (the title is in the column title)."""
    info = [f"rating: {page.rating}"] * bool(page.rating) + ["#" + " #".join(page.tags)] * bool(page.tags)
    return [Para([["  ·  ".join(info), DIM, None]]), Para([], kind="blank")] if info else []
