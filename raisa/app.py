"""The application: state, actions, keys, main loop (see docs/how-it-works.md).

Key → action via the BINDINGS table (method do_<name>). Everything that touches the
network goes through busy(): the work runs in the background while Esc can cancel it.
"""
import re
import shutil
import subprocess
import threading
import time
import traceback

from . import sites, ui
from .images import FPS, Images
from .net import NotFound, describe_error, fetch_page
from .parse import Page
from .search import SEARCH_SLUG, results_page
from .store import Store
from .views import ArticleView, Item, ListView

TRAIL_MAX = 100             # back/forward steps kept
INSIDE_STEPS = 4            # of them, jumps within one page (g, G) in a row
# Key → (action, arguments): "j": ("move", 1) calls do_move(1). The search line has its own keys
# (on_search_key); in the bookmarks Enter and x are their own too (on_key).
BINDINGS: dict[str, tuple] = {      # the keys: cli.KEYS (--help, man)
    "j": ("move", 1), "down": ("move", 1), "k": ("move", -1), "up": ("move", -1),
    "d": ("move", 1, "half"), "ctrl+d": ("move", 1, "half"), "pgdn": ("move", 1, "half"),
    "u": ("move", -1, "half"), "ctrl+u": ("move", -1, "half"), "pgup": ("move", -1, "half"),
    " ": ("move", 1, "page"), "g": ("move", -1, "end"), "home": ("move", -1, "end"),
    "G": ("move", 1, "end"), "end": ("move", 1, "end"),
    "n": ("trail", 1), "p": ("trail", -1), "N": ("trail", -1), "J": ("link", 1), "K": ("link", -1),
    "enter": ("follow",), "/": ("search",), "m": ("home",), "o": ("browser",), "i": ("image",),
    "b": ("bookmark",), "B": ("bookmark_link",), "q": ("quit",), "ctrl+c": ("quit",),
    "tab": ("focus",), "shift+tab": ("focus",), "h": ("focus",), "l": ("focus",),
}


class Cancelled(Exception):
    """Esc pressed while loading."""


# ---------- background work: only download and parse, never touch App
def _load_first(candidates):
    """The first existing page among candidates [(branch, slug)]; search results are a page too."""
    for branch, slug in candidates:
        try:
            if slug.startswith(SEARCH_SLUG):
                return Page(branch, slug, results_page(branch, slug))
            return Page(branch, slug, fetch_page(sites.page_url(branch, slug)))
        except NotFound:
            pass
    raise NotFound(candidates[0][1])


def _natural(text: str) -> list:
    """Sort key comparing numbers as numbers: SCP-173 before SCP-1000."""
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", text.lower())]


class App:
    def __init__(self, term, branch: str):
        self.term, self.branch = term, branch
        self.store = Store()
        self.images = Images(term.sixel)
        self.marks = ListView("Bookmarks", [])
        self.art: ArticleView | None = None
        self.focus = "article"              # or "bookmarks"
        self.trail: list[list] = []         # back/forward like in a browser: [slug, reading position]
        self.pos = -1                       # the current trail entry
        self.search: str | None = None      # the search line at the bottom: its text while open
        self.msg = ("", 0.0, False)         # (text, when shown, is error)
        self.busy_label: str | None = None
        self.cell, self.body_h, self.running = (10, 20), 10, True
        self.errors: list[str] = []         # details of errors, printed after exit
        self.pics: list = []                # image strips on screen (ui.render, ui.draw_pics)

    # ---------- small things
    def say(self, text: str, err: bool = False):
        self.msg = (text, time.time(), err)

    def error(self, e: BaseException):
        if isinstance(e, Cancelled):
            self.say("Cancelled")
        else:
            self.say(f"Error: {describe_error(e)}", True)

    def open_outside(self, target: str) -> bool:
        """Open a URL in the browser or a file in a viewer (xdg-open)."""
        if not shutil.which("xdg-open"):
            self.say("xdg-open not found (install xdg-utils)", True)
            return False
        subprocess.Popen(["xdg-open", target], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return True

    def toggle_bookmark(self, branch, slug, title):
        added = self.store.toggle(branch, slug, title)
        self.say("Added to bookmarks" if added else "Removed from bookmarks")

    def current_page(self):
        """(branch, slug) of the open page."""
        return (self.art.page.branch, self.art.page.slug) if self.art else None

    def render(self):
        # bookmarks of this branch, A→Z; the cursor stays in place: after a removal, on the next one
        marks = sorted((x for x in self.store.rows() if x["branch"] == self.branch), key=lambda x: _natural(x["title"]))
        self.marks.items = [Item(x["title"], (x["branch"], x["slug"])) for x in marks]
        self.marks.move(0)
        self.images.moving = False
        self.term.write(ui.render(self))

    def busy(self, label: str, fn, *args):
        """Run fn(*args) in a background thread; meanwhile a spinner runs and Esc cancels the wait."""
        box: dict = {}
        done = threading.Event()

        def work():
            try:
                box["ok"] = fn(*args)
            except BaseException as e:      # passed on to the main thread
                box["err"] = e
            done.set()

        threading.Thread(target=work, daemon=True).start()
        try:
            if not done.wait(0.05):     # from the cache it is usually fast enough: no flicker
                self.busy_label = label
                while not done.is_set():
                    self.render()
                    if any(e[0] == "key" and e[1].name in ("esc", "ctrl+c") for e in self.term.events(0.08)):
                        raise Cancelled()
        finally:
            self.busy_label = None
        if "err" in box:
            raise box["err"]
        return box["ok"]

    # ---------- loading and showing
    def load(self, candidates) -> Page | None:
        """Download and parse the first existing page of candidates [(branch, slug)]; if it fails,
        a message and None."""
        try:
            return self.busy(f"Loading {sites.page_url(*candidates[0])}", _load_first, candidates)
        except NotFound:
            self.say(f"Page not found: {candidates[0][1]}", True)
        except Exception as e:
            self.error(e)

    def open(self, branch, slug, push=True, candidates=None) -> bool:
        """Open a page; one of another branch opens in the browser. push: it is a new
        back/forward step (not a move with n/p)."""
        if branch != self.branch:
            if self.open_outside(sites.page_url(branch, slug)):
                self.say(f"Another branch ({branch}): opened in the browser; run raisa -b {branch} to read it here")
            return False
        page = self.load(candidates or [(branch, slug)])
        if not page:
            return False
        if push:
            self.push(page.slug)
        self.art, self.focus = ArticleView(page), "article"
        self.save_session()
        if self.images.shown:
            for url, *_ in page.images:
                self.images.request(url, page.url)
        return True

    def push(self, slug, inside=False):
        """A new back/forward step; it cuts off everything "ahead". inside: a jump within the
        page (g, G) — of those only the last INSIDE_STEPS stay, the oldest goes."""
        self.remember()
        self.trail = self.trail[:self.pos + 1][1 - TRAIL_MAX:] + [[slug, (0, 0)]]
        start = len(self.trail) - 1
        while inside and start > 0 and self.trail[start - 1][0] == slug:
            start -= 1                              # the page's run: where it was opened, then jumps
        if inside and len(self.trail) - start > INSIDE_STEPS + 1:
            del self.trail[start + 1]
        self.pos = len(self.trail) - 1

    def remember(self):
        """Keep the reading position of the open page in its back/forward entry."""
        if self.art and 0 <= self.pos < len(self.trail):
            self.trail[self.pos][1] = self.art.anchor()

    # ---------- actions (see BINDINGS)
    def do_quit(self):
        self.running = False

    def do_move(self, step: int, unit: str = "line"):
        """Move by a line, half a screen, a screen or to the start/end: the page or the cursor in the
        bookmarks."""
        n = step * {"line": 1, "half": max(1, self.body_h // 2), "page": max(1, self.body_h - 2), "end": 10 ** 9}[unit]
        if self.focus == "bookmarks":
            self.marks.move(n)
        elif self.art:
            if unit == "end" and self.art.top != (0 if step < 0 else self.art.max_top()):
                self.push(self.art.page.slug, inside=True)      # g / G: a back/forward step, like a link
            self.art.scroll(n)

    def do_search(self):
        self.search = ""

    def do_home(self):
        """m: the main page of the site."""
        self.open(self.branch, "")

    def do_focus(self):
        """Tab, Shift-Tab, h, l: the bookmarks or the text."""
        self.focus = "bookmarks" if self.focus == "article" else "article"

    def do_bookmark(self):
        """b: bookmark the open page, or remove it if it is already there."""
        if self.art:
            self.toggle_bookmark(*self.current_page(), self.art.page.title)

    def do_bookmark_link(self):
        """B: bookmark the page the selected link (J/K) leads to, or remove it if it is there.
        A bookmark is titled as the page calls itself, so a new one loads the page (not showing it)."""
        n = self.art.link if self.art else None
        target = sites.link_target(self.art.page.links[n][0]) if n is not None else None
        if not (target and target[0] == self.branch):
            return self.say("Select a link to a page of this branch with J/K", True)
        if self.store.is_bookmarked(*target):
            return self.toggle_bookmark(*target, "")
        page = self.load([target])
        if page:
            self.toggle_bookmark(*target, page.title)

    def do_link(self, step: int):
        """J/K: select the next/previous link."""
        if self.art:
            self.art.move_link(step)
            self.say("")                    # the link address must be visible in the header

    def do_follow(self):
        if self.art and self.art.link is not None:
            url = self.art.page.links[self.art.link][0]
            target = sites.link_target(url)
            if target:
                self.open(*target)
            elif url.startswith(("http://", "https://")):   # never open file:, steam: and the like
                self.open_outside(url)

    def do_trail(self, step: int):
        """n / N, p: forward / back, to the same reading position."""
        if not 0 <= self.pos + step < len(self.trail):
            return self.say("This is the first page" if step < 0 else "This is the last page")
        self.remember()
        self.pos += step
        if self.art and self.trail[self.pos][0] == self.art.page.slug:    # a place on the same page
            return self.art.restore(self.trail[self.pos][1])
        if self.open(self.branch, self.trail[self.pos][0], push=False):
            self.art.restore(self.trail[self.pos][1])
        else:
            self.pos -= step

    def do_image(self):
        """i: the first visible image in an external viewer (opens once downloaded)."""
        vis = self.art.visible_images() if self.art else []
        if vis:
            url = self.art.page.images[vis[0]][0]
            if self.images.info(url).get("path"):
                self.open_outside(str(self.images.info(url)["path"]))
            else:
                self.images.request(url, self.art.page.url, open_after=True)

    def do_browser(self):
        if self.art:
            self.open_outside(self.art.page.url)

    # ---------- input
    def on_key(self, key):
        if self.search is not None:
            return self.on_search_key(key)
        name, it = key.name, self.marks.current()
        if self.focus == "bookmarks":   # Enter opens the bookmark, x removes it; J K move like j k
            if name in ("enter", "x"):
                if it and name == "enter":
                    self.open(*it.value)
                elif it:
                    self.toggle_bookmark(*it.value, it.label)
                return
            name = {"J": "j", "K": "k"}.get(name, name)
        action = BINDINGS.get(name)
        if action:
            getattr(self, "do_" + action[0])(*action[1:])

    def on_search_key(self, key):
        """The search line: typing, Enter goes, Esc (or Backspace on an empty line) closes it."""
        name = key.name
        if name in ("esc", "ctrl+c") or (name == "backspace" and not self.search):
            self.search = None
        elif name == "enter":
            self.search_enter()
        elif name in ("backspace", "ctrl+u"):
            self.search = "" if name == "ctrl+u" else self.search[:-1]
        elif key.text.isprintable() and key.text:
            self.search += key.text

    def search_enter(self):
        """Enter in the search line: an object number or a URL opens the page; words open
        the page of search results."""
        text, self.search = self.search.strip(), None
        if not text:
            return
        cands = sites.parse_target(text, self.branch, words=True)
        if cands is None:
            return self.open(self.branch, SEARCH_SLUG + " ".join(text.replace("/", " ").split()))
        return self.open(*cands[0], candidates=cands) if cands else self.say(f"Nothing found: {text}", True)

    def dispatch(self, ev):
        if ev[0] == "paste":            # a paste goes into the search line, opening it
            if self.search is not None or ev[1]:
                self.search = (self.search or "") + ev[1]
            return
        if self.msg[0] and time.time() - self.msg[1] > 1:   # a message lives until the next key
            self.say("")
        self.on_key(ev[1])

    # ---------- the last open page of this branch
    def save_session(self):
        if self.art:
            self.store.set_last(self.branch, self.art.page.slug, self.art.anchor())

    def startup(self, target):
        """The page from the command line; otherwise the last page of this branch, at the same
        place; on the first run, the main page."""
        cands = sites.parse_target(target, self.branch) if target else []
        last = self.store.last(self.branch)
        if cands:
            self.open(*cands[0], candidates=cands)
        elif target:
            self.say(f"Nothing found: {target}", True)
        elif last and self.open(self.branch, last[0]):
            self.art.restore(last[1])
        else:
            self.open(self.branch, "")

    # ---------- main loop
    def guarded(self, fn, *args):
        """An error on one page must not crash the program: a message in the header now,
        the details in the terminal after exit."""
        try:
            fn(*args)
        except Exception as e:
            if not isinstance(e, Cancelled):
                self.errors.append(traceback.format_exc())
            self.error(e)

    def run(self, target=None):
        self.render()
        self.guarded(self.startup, target)
        dirty, size = True, None
        try:
            while self.running:
                changed, to_open = self.images.poll()
                for path in to_open:
                    self.open_outside(str(path))
                if (new := self.term.size()) != size:
                    size, changed = new, True
                    self.term.write("\x1b[2J")
                if dirty or changed:
                    self.guarded(self.render)
                elif self.images.moving:      # an animation: only its strips
                    self.term.write("\x1b[?2026h" + ui.draw_pics(self, True) + "\x1b[?2026l")
                had_msg = self.msg[0]
                events = self.term.events(1 / FPS if self.images.moving else 0.15)
                for ev in events:
                    self.guarded(self.dispatch, ev)
                    if not self.running:
                        break
                dirty = bool(events)
                # the message fades by itself, unless it is all there is to see (no page has opened)
                if had_msg and self.art and time.time() - self.msg[1] > 4:
                    self.say("")
                    dirty = True
        finally:
            self.guarded(self.save_session)
            self.images.shutdown()
