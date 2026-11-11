import pytest

from raisa import sites


@pytest.mark.parametrize("text,branch,expected", [
    ("173", "en", [("en", "scp-173")]),
    ("scp-173", "ru", [("ru", "scp-173")]),
    ("SCP 173", "en", [("en", "scp-173")]),
    ("1", "en", [("en", "scp-001")]),
    ("682-j", "en", [("en", "scp-682-j")]),
    ("173-ex", "en", [("en", "scp-173-ex")]),
    ("ru-1001", "en", [("ru", "scp-1001-ru"), ("ru", "scp-ru-1001")]),
    ("scp-ru-1001", "ru", [("ru", "scp-1001-ru"), ("ru", "scp-ru-1001")]),
    ("001-jp", "en", [("jp", "scp-001-jp"), ("jp", "scp-jp-001")]),
    ("cn-1", "en", [("cn", "scp-cn-001"), ("cn", "scp-001-cn")]),
    ("xx-12", "en", [("en", "scp-xx-12")]),
    ("tales-hub", "en", [("en", "tales-hub")]),
    ("https://scp-wiki.wikidot.com/scp-173", "ru", [("en", "scp-173")]),
    ("scp-ru.wikidot.com/scp-173", "en", [("ru", "scp-173")]),
    ("http://www.scpwiki.com/", "ru", [("en", "")]),
    ("https://example.com/scp-173", "en", []),
    ("   ", "en", []),
])
def test_parse_target(text, branch, expected):
    assert sites.parse_target(text, branch) == expected


def test_link_target():
    assert sites.link_target("https://scpfoundation.net/scp-173#toc") == ("ru", "scp-173")
    assert sites.link_target("http://scp-wiki.wikidot.com/system:page-tags/tag/euclid") == \
        ("en", "system:page-tags/tag/euclid")
    assert sites.link_target("https://scp-wiki.wikidot.com/local--files/scp-173/x.jpg") is None
    assert sites.link_target("javascript:alert(1)") is None
    assert sites.link_target("file:///etc/passwd") is None


def test_page_url_quotes():
    assert sites.page_url("en", "scp-173") == "https://scp-wiki.wikidot.com/scp-173"
    assert sites.page_url("ru", "объект 1") == "https://scpfoundation.net/%D0%BE%D0%B1%D1%8A%D0%B5%D0%BA%D1%82%201"


def test_search_window_input():
    """words=True: a number or a URL is an address, anything else is words to search for."""
    assert sites.parse_target("173", "en", words=True) == [("en", "scp-173")]
    assert sites.parse_target("scp-wiki.wikidot.com/scp-173", "en", words=True) == [("en", "scp-173")]
    assert sites.parse_target("https://example.com/x", "en", words=True) == []
    for s in ("plague doctor", "tales-hub", "Dr. Bright"):
        assert sites.parse_target(s, "en", words=True) is None, s


def test_branches_are_consistent():
    hosts = {}
    for b in sites.BRANCHES.values():
        assert b.base.startswith("https://") and not b.base.endswith("/")
        if b.obj:
            assert "{n}" in b.obj
        for h in b.hosts:
            assert h not in hosts, h
            hosts[h] = b.code


def test_urls():
    assert sites.link_target("https://scp-wiki.wikidot.com/SCP-173") == ("en", "scp-173")
    assert sites.image_candidates("https://h/local--files/p/фото 1.jpg") == [
        "https://h/local--files/p/%D1%84%D0%BE%D1%82%D0%BE%201.jpg"]
    assert sites.image_candidates("https://h/a%20b.jpg?x=1&y=2") == ["https://h/a%20b.jpg?x=1&y=2"]
    assert sites.image_candidates("http://scp-ru.wdfiles.com/local--files/scp-ru-1/a.png") == [
        "http://scp-ru.wdfiles.com/local--files/scp-ru-1/a.png",
        "https://scpfoundation.net/local--files/scp-ru-1/a.png"]
