import codecs

from raisa.term import Key, Term, kitty_key


def test_kitty_key_layouts():
    assert kitty_key("106") == Key("j", "j")
    assert kitty_key("1086::106") == Key("j", "о")            # «о» on the J key
    assert kitty_key("1086:1054:106;2") == Key("J", "О")      # Shift
    assert kitty_key("46::47") == Key("/", ".")               # Russian «.» on the «/» key
    assert kitty_key("46:44:47;2") == Key("?", ",")
    assert kitty_key("1078:1046:59;2") == Key(":", "Ж")       # Shift+Ж → «:»
    assert kitty_key("104::106") == Key("h", "h")             # Dvorak: the layout's letter
    assert kitty_key("106;65").name == "j"                          # Caps Lock does not matter
    assert kitty_key("106;1;74").text == "J"                        # text from the terminal
    assert kitty_key("99;5") == Key("ctrl+c", "")
    assert kitty_key("1089::99;5") == Key("ctrl+c", "")       # Ctrl+С in the Russian layout
    assert kitty_key("13").name == "enter" and kitty_key("27").name == "esc"
    assert kitty_key("127").name == "backspace"
    assert kitty_key("9").name == "tab" and kitty_key("9;2").name == "shift+tab"
    assert kitty_key("57441;2") is None                             # Shift alone
    assert kitty_key("garbage;;") is None


def make_term():
    t = Term.__new__(Term)       # no real terminal
    t.buf, t.kitty_keys, t.sixel = "", False, None
    t.dec = codecs.getincrementaldecoder("utf-8")("replace")
    t.written = []
    t.write = t.written.append
    return t


def parse(t, s):
    t.buf += s
    return t._parse()


def test_term_parse_foot_keys():
    """What foot 1.16 really sends with the protocol on (recorded with wtype)."""
    t = make_term()
    t.kitty_keys = True
    ev = parse(t, "\x1b[A\x1b[B\x1b[57441u\x1b[5~\x1b[6~\x1b[H\x1b[F\x1b[27u\x1b[13u\x1b[127u"
                  "\x1b[32;;32u\x1b[99;5u\x1b[122;5u\x1b[100;5u\x1b[106;2u\x1b[71;;71u\x1b[58;;58u\x1b[63;;63u")
    assert [e[1].name for e in ev] == ["up", "down", "pgup", "pgdn", "home", "end", "esc", "enter", "backspace",
                                       " ", "ctrl+c", "ctrl+z", "ctrl+d", "J", "G", ":", "?"]
    assert parse(t, "j\r\x03") == []                         # bare bytes are not expected: skipped


def test_term_parse_plain_keys():
    """Without the protocol (xterm, cool-retro-term): bytes and the usual sequences."""
    t = make_term()
    t.sixel = False                                         # the start-up replies are over
    ev = parse(t, "jJоО/.?\r\x7f\x04\x03\x1bOA\x1b[1;5B\x1b[5~\x1b[1~\x1b[4~\x1bx\t\x1b[Z")
    assert [e[1].name for e in ev] == ["j", "J", "j", "J", "/", ".", "?", "enter", "backspace", "ctrl+d",
                                       "ctrl+c", "up", "down", "pgup", "home", "end", "x", "tab", "shift+tab"]
    assert ev[2][1].text == "о"                             # the search line gets the Russian letter
    assert parse(t, "\x1b") == [("key", Key("esc"))]       # a lone ESC: the Esc key
    t.sixel = None
    assert parse(t, "\x1b") == [] and t.buf == "\x1b"      # at start-up: a reply may follow


def test_term_parse_paste_and_replies():
    t = make_term()
    ev = parse(t, "\x1b[200~http://x/\nscp-173\x1b[201~")
    assert ev == [("paste", "http://x/ scp-173")]
    assert parse(t, "\x1b[?0u\x1b[?62;4;22c") == [] and t.kitty_keys and t.written == ["\x1b[>29u"]
    assert t.sixel                                          # "4" in the device attributes: Sixel
    assert parse(t, "\x1b[1086::106u")[0][1].name == "j"
    assert parse(t, "\x1b[?62;22c") == [] and t.sixel is False


def test_term_parse_partial_sequences():
    t = make_term()
    assert parse(t, "\x1b[1;5") == [] and t.buf == "\x1b[1;5"
    assert parse(t, "B")[0][1].name == "down"
    assert parse(t, "\x1b[200~half") == []
    assert parse(t, " done\x1b[201~") == [("paste", "half done")]
    assert parse(t, "\x1b") == [] and parse(t, "[27u")[0][1].name == "esc"
