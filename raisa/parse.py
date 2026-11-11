"""Parsing Wikidot HTML pages (and the similar engine of the Russian branch) with BeautifulSoup.

First the article is cleaned: what the site hides or draws by itself (scripts, rating widgets,
hover tips, blocks hidden by a style) is removed from the tree. Then the tree is walked: block
elements make paragraphs, inline ones (bold, links…) give attributes to pieces of text.
"""
import html
import os
import re
import urllib.parse

from bs4 import BeautifulSoup, NavigableString, Tag

from . import sites
from .style import CODE, DIM, EUCLID, IMG, KETER, LINK, SAFE, THAUMIEL, B, H, I, S, U
from .text import Para, clean, one_line, sw

BLOCK = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol", "blockquote", "table", "tr",
         "pre", "dl", "dt", "dd", "center", "section", "article", "caption", "figure", "figcaption",
         "tbody", "thead", "tfoot", "details", "summary"}       # start a new paragraph…
SPACED = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "table", "pre", "ul", "ol", "dl", "figure"}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}                 # …these also add a blank line
SKIP_TAGS = {"script", "style", "noscript", "iframe", "form", "button", "select", "textarea", "svg",
             "object", "audio", "video", "template"}
SKIP_CLASSES = {"modalcontainer", "footer-wikiwalk-nav", "page-rate-widget-box", "creditrate", "credit-back",
                "rate-box-with-credit-button", "modal-wrapper", "collapsible-block-unfolded-link",
                "wiki-content-table-of-contents-hidden", "info-container-close",
                "multibranch-wrapper", "overwritting", "not-to-display",   # zh-tr parts hidden by CSS
                "w-stars-rate-module", "apcs-tooltip", "apcs-info-tooltips", "tooltiptext"}   # ru: rating, hover tips
CELL = [" | ", DIM, None]   # between table cells
TABLE_MAX = 76              # a wider table is not lined up: the text is at most 80 wide
# a border in a style: a framed block gets a line above and below
FRAME = re.compile(r"(^|;)border:(?!none|0|unset|initial|inherit|hidden)")
# Object class → header colour; the words as the branches write them in tags and in the class line.
CLASSES = {**dict.fromkeys(("safe", "sicher", "seguro", "sûr", "안전", "bezpieczne", "безопасный", "безпечний"),
                           SAFE),
           **dict.fromkeys(("euclid", "euclídeo", "euclide", "유클리드", "евклид", "евклід"), EUCLID),
           **dict.fromkeys(("keter", "케테르", "кетер"), KETER),
           **dict.fromkeys(("thaumiel", "taumiel", "타우미엘", "таумиэль"), THAUMIEL)}
# the label of the class line: "Object Class", "Classe", "Klasa", "项目等级", "Класс объекта"…
CLASS_LABELS = ("clas", "klas", "třída", "phân loại", "クラス", "등급", "等级", "等級", "ระดับ", "клас")
INLINE_CLASSES = {"class-category", "class-text"}     # ACS bar: "Containment Class: Euclid" on one line
STYLE_TAGS = {"b": B, "strong": B, "i": I, "em": I, "u": U, "ins": U, "s": S, "strike": S, "del": S,
              "code": CODE, "tt": CODE, "kbd": CODE, "samp": CODE, "th": B}


def _text(runs) -> str:
    return "".join(r[0] for r in runs)


def _classes(el: Tag) -> set[str]:
    return {c.lower() for c in el.get("class", [])}


def _style(el: Tag) -> str:
    return el.get("style", "").replace(" ", "").lower()


def _join(base: str, href: str) -> str:
    """The full address of a link; "" for a malformed one ("http://[x")."""
    try:
        return urllib.parse.urljoin(base, href)
    except ValueError:
        return ""


def _clean(root: Tag):
    """Remove what a reader of the site does not see as text. Blocks hidden by a style hold template
    leftovers like {$comments2}, but the folded part of a collapsible block and inactive tabs
    are hidden the same way and stay."""
    for el in root.find_all(True):
        if el.decomposed:
            continue
        cls = _classes(el)
        hidden = "display:none" in _style(el) and "collapsible-block-unfolded" not in cls and \
            "yui-content" not in _classes(el.parent)
        avatar = el.name == "img" and el.find_parent(class_="printuser")
        license_box = any(c.startswith("licensebox") for c in cls)
        if el.name in SKIP_TAGS or cls & SKIP_CLASSES or license_box or hidden or avatar:
            el.decompose()


class Walker:
    """Paragraphs of an element: pieces [text, attrs, link number], indents, links, images."""

    def __init__(self, base_url: str, root: Tag | None, css: str = ""):
        self.base_url = base_url
        self.paras: list[Para] = []
        self.links: list[list] = []                 # [url, link text]
        self.images: list[tuple[str, str, bool]] = []   # (url, caption, a link: a logo or a button)
        self.runs: list[list] = []                  # pieces of the current paragraph
        self.attr = 0                               # attributes of the text here
        self.link: int | None = None                # the link we are inside
        self.ctx: list[list] = []       # indents: ["q"] quote, ["li", width, bullet]
        self.lists: list[list] = []     # open lists: [numbered?, counter]
        self.pre = 0                    # inside <pre>
        self.cells = 0                  # cells so far in the table row
        self.framed = {c.lower() for c, rule in re.findall(r"\.([\w-]+)\s*\{([^}]*)\}", css)
                       if FRAME.search(rule.replace(" ", "").lower())}     # classes the CSS draws with a border
        if root is None:
            return
        _clean(root)
        self.walk(root)
        self._flush()
        while self.paras and self.paras[-1].kind == "blank":     # _blank never adds a leading one
            self.paras.pop()
        self.links = [(u, one_line(t) or u) for u, t in self.links]

    # ---------- paragraphs
    def _emit(self, text, attr=None, link=-1):
        self.runs.append([text, self.attr if attr is None else attr, self.link if link == -1 else link])

    def _prefix(self) -> tuple[str, str, bool]:
        """Indents from quotes and lists: (first line, other lines, was the "•" bullet used)."""
        base, bullet = "", None
        for i, c in enumerate(self.ctx):
            if c[0] == "q":
                base += "▏ "
                continue
            if c[2] is not None and all(x[0] != "li" for x in self.ctx[i + 1:]):
                bullet = (len(base), c[2])
            base += " " * c[1]
        if not bullet:
            return base, base, False
        pos, b = bullet
        return base[:pos] + b + base[pos + len(b):], base, True

    def _flush(self):
        """Finish the current paragraph."""
        runs, self.runs = self.runs, []
        if not _text(runs).strip():
            return
        first, rest, bullet = self._prefix()
        if bullet:      # the bullet goes only on the first paragraph of an item
            next(c for c in reversed(self.ctx) if c[0] == "li")[2] = None
        self.paras.append(Para(runs, first, rest, "pre" if self.pre else "text"))

    def _blank(self):
        self._flush()
        if self.paras and self.paras[-1].kind != "blank":
            self.paras.append(Para([], kind="blank"))

    def _line(self):
        """A horizontal line across the text (<hr>, the frame of a block), indented like the text;
        between two framed blocks one line, not two."""
        self._flush()
        if next((p.kind for p in reversed(self.paras) if p.kind != "blank"), "") != "hr":
            self.paras.append(Para([], self._prefix()[1], kind="hr"))

    def _break(self, tag, nested_list):
        self._blank() if tag in SPACED and not nested_list else self._flush()

    def _align(self, start: int):
        """Line up the columns of the table whose rows begin at paras[start], if it fits the text."""
        rows = [p for p in self.paras[start:] if p.kind == "text" and CELL in p.runs]
        table = []
        for p in rows:          # the cells of a row, without the spaces at their edges
            cells = [[]]
            for r in p.runs:
                cells.append([]) if r == CELL else cells[-1].append(list(r))
            for c in filter(None, cells):
                c[0][0], c[-1][0] = c[0][0].lstrip(), c[-1][0].rstrip()
            table.append(cells)
        n = max(map(len, table), default=0)
        width = [max((sw(_text(row[i])) for row in table if i < len(row)), default=0) for i in range(n)]
        if sum(width) + 3 * (n - 1) > TABLE_MAX:
            return              # too wide: the cells stay one after another, wrapped as text
        for p, row in zip(rows, table):
            runs = []
            for i, cell in enumerate(row + [[]] * (n - len(row))):
                runs += cell + [[" " * (width[i] - sw(_text(cell))), 0, None]] + [list(CELL)] * (i < n - 1)
            p.runs, p.kind = runs, "pre"     # "pre": the spaces stay, the lines are not re-wrapped

    # ---------- the tree
    def walk(self, el: Tag):
        for node in el.children:
            if type(node) is NavigableString:   # not a comment, a doctype…
                self.text(str(node))
            elif isinstance(node, Tag):
                self.tag(node)

    def text(self, data: str):
        data = clean(data)
        if not self.pre:
            data = re.sub(r"\s+", " ", data)
            if not data.strip() and (not self.runs or self.runs[-1][0].endswith(" ")):
                return
        if self.link is not None:
            self.links[self.link][1] += data
        self._emit(data)

    def tag(self, el: Tag):
        name, cls, style = el.name, _classes(el), _style(el)
        if "yui-navset" in cls:
            return self.tabs(el)
        inline = bool(cls & INLINE_CLASSES)     # does not break the line
        block = name in BLOCK and not inline
        nested = name in ("ul", "ol") and bool(self.lists)
        framed = name == "div" and bool(FRAME.search(style) or cls & self.framed)
        saved = self.attr, self.link, len(self.ctx), len(self.lists)
        if "class-text" in cls and self.runs and not self.runs[-1][0].endswith(" "):
            self._emit(" ", None, None)
        if block:
            self._break(name, nested)
        if framed:
            self._line()        # a framed block (a notice, a log): a line above it and below it
        self.attr |= STYLE_TAGS.get(name, 0) | H * (name in HEADINGS) | S * ("line-through" in style) \
            | DIM * ("collapsible-block-folded" in cls or name == "summary")
        if name == "blockquote" or "blockquote" in cls:
            self.ctx.append(["q"])
        if name in ("ul", "ol"):
            self.lists.append([name == "ol", 0])
        if name == "li":
            lst = self.lists[-1] if self.lists else [False, 0]
            lst[1] += 1
            bullet = f"{lst[1]}. " if lst[0] else "• "
            self.ctx.append(["li", len(bullet), bullet])
        start = len(self.paras)
        if name == "tr":
            self.cells = 0
        if name in ("td", "th"):
            if self.cells:
                self._emit(*CELL)
            self.cells += 1
        if name == "br":
            self._flush()
        if name == "hr":
            self._line()
        if name == "img":
            self.image(el)
        note = (name == "sup" and "footnoteref" in cls) or "fncon" in cls   # fncon: ru, a tip shown on hover
        if note:
            self._emit(" [" if "fncon" in cls else "[", None, None)
        href = el.get("href", "") if name == "a" else ""
        url = _join(self.base_url, href) if href and not href.startswith(("javascript:", "#", "mailto:")) else ""
        if url and "newpage" not in cls:    # a "red" link (newpage) leads to a missing page: show it as text
            self.links.append([url, ""])
            self.link, self.attr = len(self.links) - 1, self.attr | LINK
        self.pre += name == "pre"
        self.walk(el)
        if block:
            self._break(name, nested and len(self.lists) > 1)
        self.link = saved[1]
        if note:
            self._emit("]", None, None)
        if name == "table":
            self._align(start)
        if name == "pre":
            self._flush()
            self.pre -= 1
        if len(self.ctx) > saved[2]:       # the end of a quote or a list item
            self._flush()
            del self.ctx[saved[2]:]
        del self.lists[saved[3]:]
        self.attr = saved[0]
        if framed:
            self._line()
            self._blank()

    def tabs(self, el: Tag):
        """yui tabs: every tab under a heading "══ Name ══"."""
        nav = el.find(class_="yui-nav")
        titles = [one_line(li.get_text()) for li in (nav.find_all("li") if nav else [])]
        self._blank()
        content = el.find(class_="yui-content")
        for i, pane in enumerate(content.find_all("div", recursive=False) if content else []):
            self._blank()
            self._emit(f"══ {titles[i] if i < len(titles) else f'#{i + 1}'} ══", H | B, None)
            self._flush()
            self.tag(pane)
        self._blank()

    def image(self, el: Tag):
        src = el.get("src") or el.get("data-src") or " ".join(el.get("srcset", "").split()[:1])
        url = "" if src.startswith("data:") else _join(self.base_url, src)
        if not src or not url:
            return
        alt = one_line(el.get("alt", "")) or os.path.basename(urllib.parse.urlsplit(url).path)
        self.images.append((url, alt, self.link is not None))
        self._flush()
        first, rest, _ = self._prefix()
        self.paras.append(Para([[f"▣ [IMG {len(self.images)}] {alt}", IMG, None]], first, rest, "img",
                               len(self.images) - 1))


class Page:
    """A parsed page."""

    def __init__(self, branch: str, slug: str, html: str):
        self.branch, self.slug, self.url = branch, slug, sites.page_url(branch, slug)
        soup = BeautifulSoup(html, "html.parser")
        title, tags = soup.find(id="page-title"), soup.find(class_="page-tags")
        rating = soup.find(class_=("page-rate-widget-box", "rate-points", "w-stars-rate-rating"))   # en, ru
        rating = rating and rating.find(class_=("number", "w-stars-rate-number"))
        self.title = (title and one_line(title.get_text())) or slug or "SCP Foundation"
        self.rating = one_line(rating.get_text()) if rating else ""
        self.tags = [t for a in (tags.find_all("a") if tags else []) if not a.get("href", "").startswith("javascript")
                     and (t := one_line(a.get_text()))]
        css = "".join(s.get_text() for s in soup.find_all("style"))     # the site's and the article's
        content = soup.find(id="page-content")
        if not slug and content:        # the main page: the site menu on top, to start exploring from
            content.insert(0, BeautifulSoup(_menu(branch, soup.find(id="side-bar")), "html.parser"))
        w = Walker(self.url, content, css)
        self.paras, self.links, self.images = w.paras, w.links, w.images
        self.class_bg = self._object_class()

    def _object_class(self) -> int:
        """Header colour by the object class: from the tags, else from the first word after
        "Object Class:" / "项目等级：" / "Класс объекта:" near the top; 0 — not an object, or another class."""
        labels = [re.split("[:：]", p.text(), maxsplit=1) for p in self.paras[:30] if re.search("[:：]", p.text()[:40])]
        values = [v.split()[0].strip(".*") for k, v in labels
                  if v.split() and any(c in k.lower() for c in CLASS_LABELS)]
        return next((CLASSES[w.lower()] for w in self.tags + values if w.lower() in CLASSES), 0)


def _menu(branch: str, side: Tag | None) -> str:
    """The site menu (the side bar) as HTML: its headings and the links to pages of this branch;
    the rest (social networks, forms, other sites) is left out. A heading without links goes."""
    w, esc = Walker(sites.page_url(branch, ""), side), html.escape
    sections, seen = [["", []]], set()
    for para in w.paras:
        links = [n for _, _, n in para.runs if n is not None]
        if para.kind == "text" and not links:
            sections.append([one_line(para.text()), []])
        for n in dict.fromkeys(links):
            t = sites.link_target(w.links[n][0])
            if t and t[0] == branch and t[1] and t[1] not in seen and not set(":/") & set(t[1]):
                seen.add(t[1])
                sections[-1][1].append(f'<a href="{esc(sites.page_url(*t))}">{esc(w.links[n][1])}</a>')
    menu = "".join(f"<p>{f'<b>{esc(h)}:</b> ' if h else ''}{' · '.join(links)}</p>" for h, links in sections if links)
    return menu + "<hr>" * bool(menu)
