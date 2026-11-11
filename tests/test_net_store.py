import hashlib
import http.server
import json
import os
import shutil
import urllib.parse

import pytest
from harness import OFFLINE, FakeSite, tiny_png

from raisa import net, search
from raisa.images import Images
from raisa.store import Store


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(net, "CACHE", tmp_path)
    monkeypatch.setattr(net, "COOKIE_FILE", tmp_path / "cookies.txt")
    monkeypatch.setattr(net, "_opener", None)
    for name, value in OFFLINE.items():
        monkeypatch.setenv(name, value)
    return tmp_path


PAGE = "<html><div id='page-content'><p>hello</p></div>" + " " * 300 + "</html>"


def test_fetch_page_cache_and_offline(cache):
    site = FakeSite({"/a": (200, PAGE)})
    url = site.url + "/a"
    assert "hello" in net.fetch_page(url)
    assert "hello" in net.fetch_page(url)
    assert site.requests == ["/a"]                  # the second time: from the cache
    site.close()
    # the server is gone, but a stale copy is better than nothing
    for p in cache.glob("pages/*.html"):
        os.utime(p, (0, 0))
    assert "hello" in net.fetch_page(url)
    assert not list(cache.glob("pages/*.tmp"))


def test_fetch_page_errors(cache):
    site = FakeSite({"/err": (500, "oops")})
    with pytest.raises(net.NotFound):
        net.fetch_page(site.url + "/missing")
    with pytest.raises(net.HttpError) as e:
        net.fetch_page(site.url + "/err")
    assert e.value.code == 500
    assert net.describe_error(e.value) == "the server returned 500"
    site.close()


def test_search_service(cache, monkeypatch):
    """Crom: the user's text goes in the GraphQL variables; its errors are told apart."""
    site = FakeSite({"/ok": (200, '{"data": {"searchPages": []}}'), "/down": (503, ""),
                     "/bad": (200, '{"errors": [{"message": "boom"}]}')})
    monkeypatch.setattr(search, "CROM", site.url + "/ok")
    assert search.crom(search.QUERY, q='a "b"') == {"searchPages": []}
    assert site.posted == [{"query": search.QUERY, "variables": {"q": 'a "b"'}}]
    monkeypatch.setattr(search, "CROM", site.url + "/bad")
    with pytest.raises(RuntimeError, match="boom"):
        search.crom(search.QUERY)
    monkeypatch.setattr(search, "CROM", site.url + "/down")
    with pytest.raises(net.HttpError):
        search.crom(search.QUERY)
    site.close()


def test_site_search_asks_for_fewer_after_a_server_error(monkeypatch):
    asked, good = [], 2             # the server answers only when asked for at most `good` results

    def fake_get(url):
        limit = int(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["limit"][0])
        asked.append(limit)
        if limit > good:
            raise net.HttpError(500, url)
        return json.dumps({"results": [{"pageId": "a"}], "cursor": "next"})
    monkeypatch.setattr(search, "get", fake_get)
    assert search.site_search("https://h", "слово", None) == ([{"pageId": "a"}], "next", False) and asked == [10, 5, 2]
    good = 0                        # even one result fails: the search is stuck
    assert search.site_search("https://h", "слово", "c") == ([], None, True) and asked[3:] == [10, 5, 2, 1]


def test_anubis_flow(cache):
    """Check → solution → cookie → the real page; the pass is saved to a file."""
    challenge = {"challenge": {"randomData": "seed", "id": "c1", "method": "fast", "difficulty": 2}}
    body = f'<script id="anubis_challenge" type="application/json">{json.dumps(challenge)}</script>'
    site = FakeSite({})
    passed = {"ok": False}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            u = urllib.parse.urlsplit(self.path)
            if u.path.endswith("pass-challenge"):
                q = dict(urllib.parse.parse_qsl(u.query))
                good = hashlib.sha256(("seed" + q["nonce"]).encode()).hexdigest() == q["response"]
                self.send_response(302 if good else 403)
                if good:
                    self.send_header("Set-Cookie", "anubis-auth=yes; Path=/; Max-Age=3600")
                    self.send_header("Location", q["redir"])
                self.end_headers()
                passed["ok"] = good
                return
            authed = "anubis-auth=yes" in (self.headers.get("Cookie") or "")
            data = (PAGE if authed else body).encode()
            self.send_response(200 if authed else 401)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    site.server.RequestHandlerClass = Handler
    assert "hello" in net.fetch_page(site.url + "/scp-173")
    assert passed["ok"]
    assert "anubis-auth" in (cache / "cookies.txt").read_text()
    assert oct(os.stat(cache / "cookies.txt").st_mode & 0o777) == "0o600"
    site.close()


def test_store(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json")
    st = Store(path)
    assert st.rows() == [] and (tmp_path / "state.json.bad").exists()
    assert st.toggle("en", "scp-173", "The Sculpture\x1b") is True
    assert st.is_bookmarked("en", "scp-173")
    st.toggle("en", "scp-682", "SCP-682")
    st2 = Store(path)
    assert [x["slug"] for x in st2.rows()] == ["scp-173", "scp-682"]
    assert "\x1b" not in st2.rows()[0]["title"]
    assert st2.toggle("en", "scp-173", "") is False and not st2.is_bookmarked("en", "scp-173")
    st2.toggle("en", "scp-682", "")
    assert Store(path).rows() == []
    st2.set_last("jp", "scp-001-jp", (3, 1))
    assert Store(path).last("jp") == ("scp-001-jp", (3, 1)) and Store(path).last("en") is None
    # broken records are skipped, and so are duplicates
    path.write_text(json.dumps({"bookmarks": [{"branch": 1}, "x", {"branch": "en", "slug": "a"},
                                              {"branch": "en", "slug": "a"}]}))
    assert [x["slug"] for x in Store(path).rows()] == ["a"]


def _img_site():
    png = tiny_png(4, 3)
    challenge = ('<!DOCTYPE html><html><script id="anubis_challenge" type="application/json">'
                 '{"challenge":{"randomData":"r","id":"c","method":"fast","difficulty":1}}</script></html>')
    site = FakeSite({})
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            u = urllib.parse.urlsplit(self.path)
            if u.path.endswith("pass-challenge"):
                self.send_response(302)
                self.send_header("Set-Cookie", "ok=1; Path=/")
                self.send_header("Location", "/")
                self.end_headers()
                return
            if u.path == "/":
                data, ctype = b"<html>home</html>", "text/html"
            elif u.path.startswith("/guarded") and "ok=1" not in (self.headers.get("Cookie") or ""):
                data, ctype = challenge.encode(), "text/html"
            elif u.path.startswith("/error"):
                data, ctype = b"<!DOCTYPE html><html><body>Error</body></html>", "text/html"
            else:
                data, ctype = png, "image/png"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    site.server.RequestHandlerClass = Handler
    return site, seen, png


def test_fetch_image_cases(cache):
    site, seen, png = _img_site()
    ref = site.url + "/page"
    # an image behind the Anubis check (HTTP 200 with a page): pass the check and get the image
    p = net.fetch_image(site.url + "/guarded/a.png", ref)
    assert p.read_bytes() == png
    # an error page instead of an image: an error, nothing is cached
    with pytest.raises(net.NotAnImage):
        net.fetch_image(site.url + "/error/b.png", ref)
    assert not net.cache_path("images", site.url + "/error/b.png", ".png").exists()
    # Cyrillic and a space in the file name
    p = net.fetch_image(site.url + "/files/фото 1.png", ref)
    assert p.read_bytes() == png and "/files/%D1%84%D0%BE%D1%82%D0%BE%201.png" in seen
    # the second time: from the cache
    n = len(seen)
    assert net.fetch_image(site.url + "/files/фото 1.png", ref) == p and len(seen) == n
    site.close()


def test_store_change_applies_once_when_writing_fails(tmp_path):
    st = Store(tmp_path / "state.json")
    (tmp_path / f"state.{os.getpid()}.tmp").mkdir()                 # the temporary file cannot be written
    assert st.toggle("en", "scp-173", "A") is True and st.is_bookmarked("en", "scp-173")


def test_store_two_instances(tmp_path):
    """Two running copies of the program do not overwrite each other's bookmarks."""
    path = tmp_path / "state.json"
    a, b = Store(path), Store(path)
    a.toggle("en", "scp-173", "A")
    b.toggle("ru", "scp-682", "B")
    fresh = Store(path)
    assert [x["slug"] for x in fresh.rows()] == ["scp-173", "scp-682"]
    a.toggle("en", "scp-096", "C")                                 # a re-reads the file before writing
    assert [x["slug"] for x in Store(path).rows()] == ["scp-173", "scp-682", "scp-096"]
    a.set_last("en", "scp-173", (3, 1))
    b.set_last("ru", "scp-682", (0, 0))
    assert Store(path).last("en") == ("scp-173", (3, 1)) and Store(path).last("ru")[0] == "scp-682"


def test_image_states(cache, monkeypatch):
    """A file pillow cannot draw (SVG) is not an error: it stays for `i`; real failures say why."""
    pytest.importorskip("PIL")
    site = FakeSite({"/a.png": (200, tiny_png(4, 3)), "/b.svg": (200, '<svg xmlns="http://www.w3.org/2000/svg"/>'),
                     "/page.png": (200, "<!DOCTYPE html><html>an error page</html>")})
    im = Images(True)
    monkeypatch.setattr(shutil, "which", lambda name: None)       # no rsvg-convert
    for name in ("a.png", "b.svg", "page.png", "gone.png"):
        im._work(f"{site.url}/{name}", site.url)
    im.poll()
    st = {name: im.info(f"{site.url}/{name}") for name in ("a.png", "b.svg", "page.png", "gone.png")}
    im.shutdown()
    site.close()
    assert st["a.png"]["status"] == "ok" and (st["a.png"]["w"], st["a.png"]["h"]) == (4, 3)
    assert st["b.svg"]["status"] == "file" and "err" not in st["b.svg"]
    assert st["page.png"] == {"status": "error", "err": "not an image"}
    assert st["gone.png"] == {"status": "error", "err": "404"}


def test_svg_is_converted(cache):
    """With rsvg-convert (librsvg) installed an SVG is drawn like any image."""
    pytest.importorskip("PIL")
    if not shutil.which("rsvg-convert"):
        pytest.skip("no rsvg-convert")
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="48" height="50"><rect width="48" height="50"/></svg>'
    site = FakeSite({"/b.svg": (200, svg)})
    im = Images(True)
    im._work(site.url + "/b.svg", site.url)
    im.poll()
    st = im.info(site.url + "/b.svg")
    im.shutdown()
    site.close()
    assert st["status"] == "ok" and (st["w"], st["h"]) == (48, 50) and st["path"].suffix == ".png"
