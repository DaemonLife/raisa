"""Text attributes and colours.

An attribute is a set of bit flags: `B | LINK` is a bold link, `attr & LINK`
asks "is it a link?". Colours are terminal SGR codes in THEME (1 bold, 2 dim,
3 italic, 4 underline, 7 reverse, 9 strike-through, 30-37 colours). Only the
16 terminal colours are used, so the program follows the terminal theme.
"""
from functools import cache

# flags set by the HTML parser
B, I, U, S, LINK, CODE, H, DIM, QUOTE, IMG = (1 << n for n in range(10))  # noqa: E741
# interface flags
SEL, BAR, BAR_OFF, ERR, INFO, CUR = (1 << n for n in range(10, 16))
# header background by object class: terminal green, yellow, red, magenta
SAFE, EUCLID, KETER, THAUMIEL = (1 << n for n in range(16, 20))

# Order matters: with several colour flags the lowest one wins.
THEME = {B: "1", DIM: "2", I: "3", U: "4", S: "9", QUOTE: "32", IMG: "1;35",
         CUR: "36", LINK: "4;36", H: "1;33", CODE: "7",
         INFO: "1;34", ERR: "1;31", BAR_OFF: "2;4", BAR: "7", SEL: "7",
         SAFE: "30;42", EUCLID: "30;43", KETER: "30;41", THAUMIEL: "30;45"}


@cache
def sgr(attr: int) -> str:
    """Terminal escape sequence for a set of flags."""
    return "\x1b[0;" + ";".join(c for f, c in THEME.items() if attr & f) + "m"
