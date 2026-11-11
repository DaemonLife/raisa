"""Navigation: the application in-process with a fake terminal.

Two local "branches", English and Russian (addresses replaced with harness.point);
cache and state.json in a temporary folder; the search service is faked.
"""
import pytest
from harness import OFFLINE, ROOT, FakeSite, fixture, point

from raisa import net, search, sites
from raisa import store as store_mod
from raisa.app import App
from raisa.cli import KEYS
from raisa.style import B
from raisa.term import Key

ARTICLE = fixture("en_scp173.html")


def ru_page(title, body):
    return (f'<html><div id="page-title">{title}</div><div id="page-content">{body}</div></html>'
            + " " * 300)


RU_MAIN = ru_page("Главная", "<p>Главная</p>")


class FakeTerm:
    sixel = False

    def size(self):
        return (160, 40, 10, 20)

    def write(self, s):
        pass

    def events(self, timeout):
        return []


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(net, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(net, "COOKIE_FILE", tmp_path / "cookies.txt")
    monkeypatch.setattr(net, "_opener", None)
    monkeypatch.setattr(store_mod, "DATA", tmp_path / "data")
    for name, value in OFFLINE.items():
        monkeypatch.setenv(name, value)
    en = FakeSite({
        "/": (200, fixture("en_main.html")),
        "/scp-series": (200, fixture("en_series.html")),
        "/scp-173": (200, ARTICLE),
        "/scp-172": (200, ARTICLE.replace("SCP-173", "SCP-172")),
        "/scp-174": (200, ARTICLE.replace("SCP-173", "SCP-174")),
    })
    ru = FakeSite({
        "/": (200, RU_MAIN),
        "/scp-173": (200, ru_page("SCP-173 — Скульптура", "<p>Объект перемещён в Зону-19. "
                                  '<a href="https://scp-wiki.wikidot.com/scp-174">Оригинал SCP-174</a></p>')),
    })
    saved = {c: (sites.BRANCHES[c].base, list(sites.BRANCHES[c].hosts)) for c in ("en", "ru")}
    point("en", en.url)
    point("ru", ru.url)
    # a link to the English site in the Russian article must lead to the local "English" server
    ru.pages["/scp-173"] = (200, ru.pages["/scp-173"][1].replace("https://scp-wiki.wikidot.com", en.url))
    yield tmp_path
    for c, (base, hosts) in saved.items():
        sites.BRANCHES[c].base, sites.BRANCHES[c].hosts = base, hosts
    en.close()
    ru.close()


def make_app(branch="en", target=None):
    a = App(FakeTerm(), branch)
    a.startup(target)
    a.render()
    return a


def press(a, *names):
    for n in names:
        a.on_key(Key(n, n if len(n) == 1 else ""))
        a.render()


def where(a):
    return a.current_page()


def typ(a, text):
    for ch in text:
        a.on_key(Key(ch, ch))
    a.render()


def goto(a, text):
    press(a, "/")
    typ(a, text)
    press(a, "enter")


def follow_link(a, slug):
    """Select the link to `slug` with J and follow it."""
    press(a, "g")
    for _ in range(20):
        press(a, "J")
        if a.art.link is not None and a.art.page.links[a.art.link][0].rstrip("/").endswith("/" + slug):
            return press(a, "enter")
    raise AssertionError(f"no link to {slug}")


def test_startup_and_steps_within_a_page(env):
    a = make_app()
    assert where(a) == ("en", "") and a.marks.items == []          # first run: the main page
    goto(a, "173")
    press(a, "G")                                   # g and G are back/forward steps (gg: one step)
    assert a.art.top == a.art.max_top() > 0
    press(a, "g", "g")
    assert a.art.top == 0 and [e[0] for e in a.trail] == ["", "scp-173", "scp-173", "scp-173"]
    press(a, "N")                                   # back: the end of the page, without reloading it
    assert a.art.top == a.art.max_top()
    press(a, "N", "N")
    assert where(a) == ("en", "")
    press(a, "n", "n")
    for _ in range(3):
        press(a, "G", "g")                          # many jumps within the page: only the last 4 stay
    assert [e[0] for e in a.trail] == ["", "scp-173", "scp-173", "scp-173", "scp-173", "scp-173"]
    press(a, "N", "N", "N", "N")
    assert a.pos == 1 and a.art.top == 0            # where the page was opened stays


def test_back_forward_and_truncate(env):
    a = make_app(target="scp-series")
    follow_link(a, "scp-172")
    follow_link(a, "scp-174")
    assert [e[0] for e in a.trail] == ["scp-series", "scp-172", "scp-174"]
    press(a, "j", "j", "j")
    top_174 = a.art.top
    press(a, "N")                                                   # back
    assert where(a)[1] == "scp-172"
    press(a, "n")                                                   # forward
    assert where(a)[1] == "scp-174" and a.art.top == top_174      # to the same place
    press(a, "n")
    assert where(a)[1] == "scp-174" and "last page" in a.msg[0]
    press(a, "p", "N")                                              # p is back too
    assert where(a)[1] == "scp-series"
    follow_link(a, "scp-174")                                       # a new step cuts off what was ahead
    assert [e[0] for e in a.trail] == ["scp-series", "scp-174"]


def test_movement_keys(env):
    a = make_app()
    goto(a, "173")
    press(a, "ctrl+d")
    assert a.art.top == min(a.body_h // 2, a.art.max_top()) > 0
    press(a, "G")
    assert a.art.top == a.art.max_top()
    press(a, "g", "g")                              # gg, like vim
    assert a.art.top == 0
    for n in range(40):
        a.store.toggle("en", f"scp-{n:03d}", f"SCP-{n:03d}")
    press(a, "tab")                                 # the bookmarks column: the same moves move the cursor
    press(a, "ctrl+d", "J", "j", "k")
    assert a.focus == "bookmarks" and a.marks.sel == a.body_h // 2 + 1
    press(a, "G")
    assert a.marks.sel == 39
    press(a, "g")
    assert a.marks.sel == 0 and a.art.top == 0      # the page did not move
    press(a, "l")
    assert a.focus == "article"
    press(a, "/")                                   # / is the search line everywhere
    assert a.search == ""


def test_bookmarks(env):
    """b: the open page goes in or out; the column: this branch only, A→Z, numbers as numbers."""
    a = make_app(target="scp-series")
    follow_link(a, "scp-172")
    press(a, "b")
    assert [it.label for it in a.marks.items] == ["SCP-172"]       # titled as the page calls itself
    press(a, "h", "b")                              # from the bookmarks column: still the open page
    assert a.marks.items == [] and "Removed" in a.msg[0]
    a.art.move_link(1)
    press(a, "enter", "x")                          # an empty list: nothing happens, the link is not followed
    assert where(a)[1] == "scp-172"
    press(a, "b")
    for slug, title in (("scp-1000", "SCP-1000"), ("scp-2", "SCP-2"), ("abc", "Abc")):
        a.store.toggle("en", slug, title)
    a.store.toggle("ru", "scp-173", "SCP-173 — Скульптура")         # another branch: not shown here
    a.render()
    assert [it.label for it in a.marks.items] == ["Abc", "SCP-2", "SCP-172", "SCP-1000"]
    press(a, "G", "enter")                          # open a bookmark (no such page here)…
    assert "not found" in a.msg[0] and a.focus == "bookmarks"
    press(a, "k", "enter")                          # …open one: the text gets the focus
    assert where(a)[1] == "scp-172" and a.focus == "article"
    press(a, "tab", "g", "j", "x")                  # x removes the bookmark under the cursor
    assert [it.label for it in a.marks.items] == ["Abc", "SCP-172", "SCP-1000"]
    assert "Removed" in a.msg[0] and a.marks.current().label.startswith("SCP-172")      # the next one
    press(a, "N", "N")                              # back/forward still work (172 was opened twice)
    assert where(a)[1] == "scp-series"


def test_bookmark_the_selected_link(env):
    a = make_app(target="scp-series")
    press(a, "B")                                   # no link selected
    assert "Select a link" in a.msg[0] and a.marks.items == []
    press(a, "J", "B")                              # a link to a page that does not exist: no bookmark
    assert "not found" in a.msg[0] and a.marks.items == []
    while not a.art.page.links[a.art.link][0].endswith("/scp-172"):
        press(a, "J")
    press(a, "B")                                   # the page is loaded for its title, but not opened
    assert "Added" in a.msg[0] and [(it.label, it.value) for it in a.marks.items] == [("SCP-172", ("en", "scp-172"))]
    assert where(a) == ("en", "scp-series")
    press(a, "B")                                   # again: removed
    assert "Removed" in a.msg[0] and a.marks.items == []


def test_search_line_and_main_page(env, monkeypatch):
    calls = []

    def fake_crom(query, **v):
        calls.append(v)
        return {"searchPages": [
            {"url": "http://scp-wiki.wikidot.com/scp-173", "wikidotInfo": {"title": "SCP-173"},
             "alternateTitles": [{"title": "The Sculpture"}]},
            {"url": "http://scp-wiki.wikidot.com/scp-174", "wikidotInfo": {"title": "SCP-174"},
             "alternateTitles": []}]}
    monkeypatch.setattr(search, "crom", fake_crom)
    a = make_app()
    goto(a, "173")
    press(a, "m")                                   # m: the main page of the site
    assert where(a) == ("en", "")
    press(a, "/")
    typ(a, "jo")
    press(a, "backspace", "backspace")
    assert a.search == ""
    press(a, "backspace")                           # Backspace on an empty line closes it
    assert a.search is None
    press(a, "/")
    typ(a, "sculpture")
    press(a, "enter")                               # words: a page of results
    assert calls[-1] == {"q": "sculpture", "b": ["http://scp-wiki.wikidot.com"]}
    assert where(a) == ("en", "search:site/q/sculpture") and a.art.page.title == "Search: sculpture"
    assert [t for _, t in a.art.page.links] == ["SCP-173 - The Sculpture", "SCP-174"]
    assert "only titles" in "".join(p.text() for p in a.art.page.paras)
    press(a, "J", "enter")                          # the results are links like on any page
    assert where(a) == ("en", "scp-173")
    goto(a, "174")                                  # a number: go there directly
    assert where(a) == ("en", "scp-174") and len(calls) == 1
    goto(a, "https://example.com/x")                # a foreign site: nothing to open, no search
    assert where(a) == ("en", "scp-174") and "Nothing found" in a.msg[0] and len(calls) == 1
    press(a, "/", "esc")
    assert a.search is None
    a.dispatch(("paste", "scp-wiki.wikidot.com/scp-172"))       # a paste opens the line with the text
    assert a.search == "scp-wiki.wikidot.com/scp-172"
    press(a, "enter")
    assert where(a) == ("en", "scp-172")


def test_full_text_search_ru(env, monkeypatch):
    """The Russian branch: titles from Crom, then the site's full-text search, 10 a page."""
    monkeypatch.setattr(search, "crom", lambda query, **v: {"searchPages": []})
    calls = []

    def fake_search(base, text, cursor):
        calls.append(cursor)
        n = len(calls)
        results = [{"pageId": f"scp-{n}{i}", "title": f"SCP-{n}{i}", "rating": {"mode": "stars", "value": 3.5},
                    "words": ["дерево"], "excerpts": ["растёт Дерево\nвсего"]} for i in range(6)]
        results.append({"pageId": "draft:x", "title": "Draft"})          # drafts are not shown
        return results, ("c" if n < 3 else None), False
    monkeypatch.setattr(search, "site_search", fake_search)
    a = make_app("ru")
    goto(a, "дерево всего")
    page = a.art.page
    assert where(a) == ("ru", "search:site/q/дерево всего") and calls == [None, "c"]   # 12 results ≥ 10
    text = "\n".join(p.text() for p in page.paras)
    assert "SCP-10 3.5" in text and "…растёт Дерево всего…" in text and "Draft" not in text
    assert any(r[0] == "Дерево" and r[1] & B for p in page.paras for r in p.runs)       # the word is bold
    assert page.links[-1][1] == "More results »"
    press(a, "G")
    a.art.link = len(page.links) - 1
    press(a, "enter")                               # the next page continues from the cursor
    assert calls[-1] == "c" and len(calls) == 3 and "More results »" not in [t for _, t in a.art.page.links]
    press(a, "N")
    assert a.art.page.title == "Search: дерево всего"


def test_other_branch_opens_in_browser(env, monkeypatch):
    opened = []
    monkeypatch.setattr(App, "open_outside", lambda self, url: opened.append(url) or True)
    a = make_app("ru")
    assert where(a) == ("ru", "")
    goto(a, "173")
    follow_link(a, "scp-174")                       # a link to the English branch
    assert where(a) == ("ru", "scp-173") and opened[-1].endswith("/scp-174")
    assert "-b en" in a.msg[0]


def test_browser_and_viewer(env, monkeypatch):
    opened = []
    monkeypatch.setattr(App, "open_outside", lambda self, target: opened.append(target) or True)
    a = make_app(target="173")
    press(a, "o")                                   # o: the open page in the browser
    assert opened == [a.art.page.url]
    follow_link(a, "Statue")                        # a link to another site goes there too
    assert opened[-1] == "https://en.wikipedia.org/wiki/Statue" and where(a) == ("en", "scp-173")
    a.images.state[a.art.page.images[0][0]] = {"status": "file", "path": "/cache/173.jpg"}
    press(a, "g", "i")                              # i: the downloaded image in a viewer
    assert opened[-1] == "/cache/173.jpg"


def test_session_restores_last_page_per_branch(env):
    a = make_app(target="scp-series")
    follow_link(a, "scp-172")
    press(a, "j", "j")
    top = a.art.top
    a.save_session()
    r = make_app("ru")                              # the Russian branch has its own last page
    assert where(r) == ("ru", "")
    b = make_app()
    assert where(b) == ("en", "scp-172") and b.art.top == top and len(b.trail) == 1


def test_error_details_are_kept_for_after_exit(env):
    a = make_app()
    a.guarded(lambda: 1 / 0)                        # a message now, the details after exit (cli.main)
    assert "ZeroDivisionError" in a.errors[0] and a.msg[2] and "division by zero" in a.msg[0]


def test_keys_are_the_same_everywhere():
    """raisa --help (cli.KEYS), man raisa and the README list the same keys."""
    assert KEYS in (ROOT / "README.md").read_text("utf-8")
    man = (ROOT / "raisa.1").read_text("utf-8")
    keys = man.split(".SH KEYS")[1].split(".PP")[0].split(".TP\n")[1:]
    pairs = [k.strip().split("\n") for k in keys]
    assert [(k.removeprefix(".B ").replace("\\-", "-"), v) for k, v in pairs] == \
        [tuple(x.strip() for x in line.split(" - ", 1)) for line in KEYS.splitlines() if line]
