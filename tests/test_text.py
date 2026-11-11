import re

from harness import fixture

from raisa.parse import Page
from raisa.style import LINK
from raisa.text import Para, clean, cut, fit, sw, wrap, wrap_plain

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def visible(s: str) -> str:
    return ANSI.sub("", s)


def test_clean_removes_terminal_controls():
    s = "a\x1b[2Jb\x07c\x9bd\u00ade\u200bf\ufeffg"   # soft hyphen, zero-width space, BOM
    assert clean(s) == "a[2Jbcdefg"      # ESC removed, the rest is harmless text
    assert clean("line\nnext\ttab") == "line\nnext    tab"


def test_widths():
    assert sw("abc") == 3
    assert sw("абв") == 3
    assert sw("日本") == 4
    assert sw("é") == 1            # e + a combining accent
    assert sw("กิ") == 1            # a Thai letter with a vowel mark
    assert cut("日本語", 3) == ("日", "本語")


def _check_width(lines, width):
    for ln in lines:
        assert sw(ln.text()) <= width, (width, ln.text())


def test_wrap_never_exceeds_width():
    page = Page("en", "scp-173", fixture("en_scp173.html"))
    jp = Page("jp", "x", fixture("jp_article.html"))
    for width in range(20, 90, 3):
        _check_width(wrap(page.paras, width), width)
        _check_width(wrap(jp.paras, width), width)


def test_wrap_cjk_fills_lines():
    jp = Page("jp", "x", fixture("jp_article.html"))
    lines = [ln.text() for ln in wrap(jp.paras, 30)]
    long_para = [t for t in lines if "収容" in t or "推奨" in t]
    # lines of a Japanese paragraph, but the last, are almost full
    assert all(sw(t) >= 28 for t in long_para[:-1])


def test_wrap_keeps_link_and_punctuation_together():
    p = Para([["see ", 0, None], ["SCP-173", LINK, 0], [", then", 0, None]])
    for width in range(20, 30):
        for ln in wrap([p], width):
            assert not ln.text().lstrip().startswith(",")


def test_wrap_long_word_and_pre():
    p = Para([["x" * 55, 0, None]])
    lines = wrap([p], 20)
    assert [sw(ln.text()) for ln in lines] == [20, 20, 15]
    pre = Para([["short\n" + "y" * 30, 0, None]], kind="pre")
    texts = [ln.text() for ln in wrap([pre], 20)]
    assert texts == ["short", "y" * 20, "y" * 10]


def test_wrap_plain_counts_cells():
    """Bookmark titles: wrapped by cells, so CJK text (two cells a character) fits the column."""
    assert wrap_plain("SCP-173 - The Sculpture", 12, "  ") == ("SCP-173 -", "  The", "  Sculpture")
    lines = wrap_plain("収容プロトコル" * 4, 19, "  ")
    assert all(sw(ln) <= 19 for ln in lines) and "".join(ln.strip() for ln in lines) == "収容プロトコル" * 4


def test_wrap_hr_spans_width():
    lines = wrap([Para([], kind="hr")], 33)
    assert sw(lines[0].text()) == 33


def test_fit_exact_width_and_safety():
    for w in (1, 5, 17, 40):
        out = fit([("abc\x1bdef", 0, None), ("日本語", 0, None)], w)
        assert sw(visible(out)) == w
        assert "\x1bd" not in out
    assert visible(fit([("About the SCP Foundation", 0, None)], 10, ell=True)) == "About the…"


def test_image_padding():
    """A blank line above and below an image, never a double one."""
    paras = [Para([["before", 0, None]]), Para([["img", 0, None]], kind="img", img=0),
             Para([["after", 0, None]]), Para([], kind="blank"),
             Para([["img2", 0, None]], kind="img", img=1), Para([], kind="blank")]
    lines = wrap(paras, 40, lambda n: (4, 2))
    kinds = ["img" if ln.pic else (ln.text() or "-") for ln in lines]
    assert kinds == ["before", "-", "img", "img", "-", "after", "-", "img", "img", "-"]


def test_sixel_encoder_roundtrip():
    """Encode an image to Sixel and decode it back: the pixels are in place."""
    from raisa.images import sixel
    w, h = 9, 8                                   # the height is not a multiple of 6: the second band is partial
    data = bytes((x * 3 + y) % 4 if (x + y) % 5 else 200 for y in range(h) for x in range(w))
    palette = [0, 0, 0, 255, 0, 0, 0, 255, 0, 0, 0, 255] + [9] * 756
    out = sixel(data, w, h, palette)
    assert out.startswith('\x1bP0;1;0q"1;1;9;8') and out.endswith("\x1b\\")
    body = out[out.index("q") + 1:-2]
    body = re.sub(r'^"[\d;]+', "", body)
    body = re.sub(r"#\d+;2;\d+;\d+;\d+", "", body)
    got = {}
    band, x, color = 0, 0, None
    for m in re.finditer(r"#(\d+)|!(\d+)(.)|([?-~])|(\$)|(-)", body):
        if m[1]:
            color = int(m[1])
        elif m[5]:
            x = 0
        elif m[6]:
            band, x = band + 1, 0
        else:
            n, ch = (int(m[2]), m[3]) if m[2] else (1, m[4])
            for dx in range(n):
                for bit in range(6):
                    if (ord(ch) - 63) >> bit & 1:
                        got[(x + dx, band * 6 + bit)] = color
            x += n
    want = {(x, y): data[y * w + x] for y in range(h) for x in range(w)}
    assert got == want
