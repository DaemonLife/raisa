"""SCP Wiki branches and page addresses.

A branch is a separate site in its own language. All but the Russian one run on
Wikidot and look alike, so a new branch is one line in _ALL.
A "slug" is the last part of the address: scp-173 in https://scp-wiki.wikidot.com/scp-173.
"""
import re
import urllib.parse
from dataclasses import dataclass, field


@dataclass
class Branch:
    code: str                       # en, ru, jp…
    base: str                       # site address without a trailing "/"
    obj: str = ""                   # the branch's own objects: "scp-ru-{n}", "scp-{n}-jp"
    hosts: list = field(default_factory=list)    # other domains of the site

    def __post_init__(self):
        self.hosts.insert(0, urllib.parse.urlsplit(self.base).netloc.lower())


_ALL = [   # English (the original wiki) first, then alphabetically; International last
    Branch("en", "https://scp-wiki.wikidot.com", "scp-{n}",
           ["scpwiki.com", "www.scpwiki.com", "scp-wiki.net", "www.scp-wiki.net"]),
    Branch("cn", "https://scp-wiki-cn.wikidot.com", "scp-cn-{n}"),
    Branch("cs", "https://scp-cs.wikidot.com", "scp-cs-{n}"),
    Branch("de", "https://scp-wiki-de.wikidot.com", "scp-{n}-de"),
    Branch("es", "https://lafundacionscp.wikidot.com", "scp-es-{n}"),
    Branch("fr", "https://fondationscp.wikidot.com", "scp-{n}-fr"),
    Branch("it", "https://fondazionescp.wikidot.com", "scp-{n}-it"),
    Branch("jp", "https://scp-jp.wikidot.com", "scp-{n}-jp"),
    Branch("ko", "https://scpko.wikidot.com", "scp-{n}-ko"),
    Branch("pl", "https://scp-pl.wikidot.com", "scp-pl-{n}"),
    Branch("pt", "https://scp-pt-br.wikidot.com", "scp-{n}-pt"),
    # The Russian branch moved off Wikidot to its own engine; old links lead here too.
    Branch("ru", "https://scpfoundation.net", "scp-{n}-ru",
           ["www.scpfoundation.net", "scp-ru.wikidot.com", "scpfoundation.ru", "www.scpfoundation.ru"]),
    Branch("th", "https://scp-th.wikidot.com", "scp-th-{n}"),
    Branch("ua", "https://scp-ukrainian.wikidot.com", "scp-ua-{n}"),
    Branch("vn", "https://scp-vn.wikidot.com", "scp-vn-{n}"),
    Branch("zh", "https://scp-zh-tr.wikidot.com", "scp-zh-{n}"),
    # English translations of the other branches' articles
    Branch("int", "https://scp-int.wikidot.com"),
]
BRANCHES: dict[str, Branch] = {b.code: b for b in _ALL}

# an object number: 173, scp-173, 682-j, ru-1001, 001-jp
NUMBER_RE = re.compile(r"(?:scp-?)?(?:([a-z]{2,3})-?)?(\d{1,5})(-[a-z]+)?")
OLD_RU_FILES = ("scp-ru.wdfiles.com", "scp-ru.wikidot.com")     # Russian branch files before the move


def page_url(branch: str, slug: str) -> str:
    return f"{BRANCHES[branch].base}/{urllib.parse.quote(slug, safe='/:-_.~%')}"


def link_target(url: str) -> tuple[str, str] | None:
    """Does the link lead to a page of some branch? Then (branch, slug)."""
    try:
        u = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    host = u.netloc.lower().rsplit("@", 1)[-1]
    code = next((b.code for b in BRANCHES.values() if {host, host.split(":")[0]} & set(b.hosts)), None)
    path = urllib.parse.unquote(u.path).strip("/")
    if u.scheme not in ("http", "https") or not code or path.startswith(("local--", "forum", "printer--")):
        return None     # files and the forum open in the browser
    return code, path.lower()      # on Wikidot /SCP-173 and /scp-173 are the same page


def image_candidates(url: str) -> list[str]:
    """The image URL encoded for a request (spaces, Cyrillic); for old Russian branch
    addresses also the same path on the new site."""
    u, safe = urllib.parse.urlsplit(url.strip()), "/:@!$&'()*+,;=-._~%"
    path = urllib.parse.quote(u.path, safe=safe)
    out = [urllib.parse.urlunsplit((u.scheme, u.netloc, path, urllib.parse.quote(u.query, safe=safe + "?"), ""))]
    if u.netloc.lower() in OLD_RU_FILES and path.startswith("/local--files/"):
        out.append(BRANCHES["ru"].base + path)
    return out


def _native(code: str, num: str) -> list[tuple[str, str]]:
    """The branch's own object: scp-ru-1001 but scp-001-jp, so try both orders."""
    n = f"{int(num):03d}"
    names = [BRANCHES[code].obj.format(n=n)] + ([f"scp-{code}-{n}", f"scp-{n}-{code}"] if code != "en" else [])
    return [(code, s) for s in dict.fromkeys(names)]


def crom_base(code: str) -> str:
    """The branch address as the Crom index knows it (the Russian branch: its old Wikidot site)."""
    return "http://" + next((h for h in BRANCHES[code].hosts if h.endswith(".wikidot.com")), BRANCHES[code].hosts[0])


def parse_target(text: str, branch: str, words: bool = False) -> list[tuple[str, str]] | None:
    """Input → candidates (branch, slug): 173, scp-173, 682-j, ru-1001, 001-jp, a URL or
    domain, a bare slug. [] — not understood (e.g. a foreign site). words=True (the search
    window): None for anything but a number or a URL — words to search for."""
    s = text.strip()
    if s.startswith(("http://", "https://")) or ("." in s.split("/")[0] and " " not in s):
        t = link_target(s if "://" in s else "https://" + s)
        return [t] if t else []
    s = re.sub(r"\s+", "-", s.lower())
    m = NUMBER_RE.fullmatch(s)
    if not m:
        return None if words else [(branch, s)] if s else []
    pre, num, suffix = m.group(1), m.group(2), m.group(3) or ""
    code = pre or suffix[1:]
    if code in BRANCHES and BRANCHES[code].obj and not (pre and suffix):
        return _native(code, num)
    return [(branch, f"scp-{pre}-{num}{suffix}" if pre else f"scp-{int(num):03d}{suffix}")]
