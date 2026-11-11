"""Site search. Wikidot's own search is turned off; the Crom API (https://api.crom.avn.sh)
indexes every SCP wiki, but only finds titles (5 at most). The Russian engine searches the
text itself (/api/search, what its search page calls).

The results are built as an HTML page for parse.Page, so they are read, followed and bookmarked
like any page. Its address is search:site/q/WORDS, then /c/CURSOR for the next portion (the
cursor in hex: page addresses are lowercased, which base64 would not survive).
"""
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from . import sites
from .config import HTTP_TIMEOUT
from .net import HEADERS, HttpError, get

SEARCH_SLUG = "search:site/q/"
CROM = "https://api.crom.avn.sh/graphql"
QUERY = ("query($q: String!, $b: [String!]) { searchPages(query: $q, filter: {anyBaseUrl: $b}) "
         "{ url wikidotInfo { title } alternateTitles { title } } }")
HIDDEN = {"deleted", "draft", "sandbox", "wip"}     # unfinished and deleted pages: not in the results


def crom(query: str, **variables) -> dict:
    """Run a GraphQL query on Crom (the query text is fixed, user text goes in variables)."""
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(CROM, body, {"Content-Type": "application/json", "User-Agent": HEADERS["User-Agent"]})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        e.close()
        raise HttpError(e.code, CROM) from e
    if data.get("errors") or not isinstance(data.get("data"), dict):
        raise RuntimeError(f"search service: {(data.get('errors') or [{}])[0].get('message', 'bad answer')}")
    return data["data"]


def site_search(base: str, text: str, cursor: str | None) -> tuple[list, str | None, bool]:
    """The Russian site's full-text search, one portion: (results, next cursor, stuck?).
    The server fails (HTTP 500) on some results: then it is asked for fewer, and at a bad
    result the search stops ("stuck")."""
    for limit in (10, 5, 2, 1):
        q = {"text": text, "limit": limit, "mode": "plain"} | ({"cursor": cursor} if cursor else {})
        try:
            data = json.loads(get(f"{base}/api/search?{urllib.parse.urlencode(q)}"))
        except HttpError as e:
            if e.code != 500:
                raise
            continue
        return data.get("results") or [], data.get("cursor"), False
    return [], None, True


def _titles(branch: str, text: str) -> list[tuple[str, tuple]]:
    """Crom: up to 5 pages whose title matches, as ("SCP-173 - The Sculpture", (branch, slug))."""
    items = []
    for r in crom(QUERY, q=text, b=[sites.crom_base(branch)]).get("searchPages") or []:
        target, title = sites.link_target(r["url"]), (r.get("wikidotInfo") or {}).get("title") or ""
        alt = next((a["title"] for a in r.get("alternateTitles") or [] if a.get("title")), "")
        if target and target[0] == branch:
            items.append((f"{title} - {alt}" if alt and alt not in title else title or target[1], target))
    return items


def _result(r: dict) -> str:
    """A full-text result: the title as a link, the rating, two excerpts with the found words in bold."""
    esc = html.escape
    words = "|".join(sorted(map(re.escape, map(esc, r.get("words") or [])), key=len, reverse=True))
    rating = r.get("rating") or {}
    fmt = {"stars": "{:.1f}", "updown": "{:+.0f}"}.get(rating.get("mode"), "")
    score = fmt.format(rating["value"]) if isinstance(rating.get("value"), (int, float)) else ""
    excerpts = [esc(" ".join(ex.split())) for ex in (r.get("excerpts") or [])[:2]]
    excerpts = [re.sub(words, lambda m: f"<b>{m[0]}</b>", ex, flags=re.I) if words else ex for ex in excerpts]
    return (f'<p><a href="/{esc(r["pageId"])}">{esc(r.get("title") or r["pageId"])}</a> {score}'
            f'<div class="blockquote">{"<br>".join(f"…{ex}…" for ex in excerpts)}</div></p>')


def results_page(branch: str, slug: str) -> str:
    """The page of results as HTML: title matches (Crom), and on the Russian branch the site's
    full-text search, 10 results a page."""
    esc = html.escape
    text, _, cursor = slug[len(SEARCH_SLUG):].partition("/c/")
    cursor = bytes.fromhex(cursor).decode() if cursor else None
    out = [f'<h1 id="page-title">Search: {esc(text)}</h1><div id="page-content">']
    if not cursor:
        links = [f'<a href="{esc(sites.page_url(*t))}">{esc(label)}</a>' for label, t in _titles(branch, text)]
        out.append(f"<h2>Titles</h2><p>{'<br>'.join(links) or '—'}</p>")
        if branch != "ru":
            return "".join(out) + "<p>Wikidot's own search is turned off: only titles are searched.</p></div>"
        out.append("<h2>Text</h2>")
    found, stuck = [], False
    while len(found) < 10 and not stuck:
        results, cursor, stuck = site_search(sites.BRANCHES[branch].base, text, cursor)
        found += [r for r in results if ":" not in r["pageId"] or r["pageId"].split(":")[0] not in HIDDEN]
        if not (results and cursor):
            break
    out += map(_result, found) if found else ["<p>—</p>"]
    if stuck:
        out.append("<p>The site's search failed on the next results.</p>")
    elif cursor:
        more = f"/{SEARCH_SLUG}{urllib.parse.quote(text)}/c/{cursor.encode().hex()}"
        out.append(f'<p><a href="{more}">More results »</a></p>')
    return "".join(out) + "</div>"
