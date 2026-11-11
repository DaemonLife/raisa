"""Start-up: command-line arguments and launching the interface."""
import argparse
import sys

from . import VERSION, sites

# The same list is in man raisa and in the README.
KEYS = """\
j/k, arrow keys           - scroll by a line
d/u, ^d/^u, PgDn/PgUp     - scroll by half a screen
Space                     - scroll by a whole screen
g/G, Home/End             - go to the top or the bottom
n, p/N                    - go to the next or the previous page or position
J/K                       - select the next or the previous link
Tab/Shift-Tab, h/l        - switch between the bookmarks and the text

Enter                     - open the selected link or bookmark
Esc                       - close the search

/                         - search the site
m                         - open the main page of the site
o                         - open the current page in the browser
i                         - open the image in an external viewer

b                         - bookmark the current page, or remove the bookmark
B                         - bookmark the selected link, or remove the bookmark
x                         - remove the selected bookmark

q                         - quit"""


def main():
    keys = "\n".join(k and "  " + k for k in KEYS.splitlines())
    ap = argparse.ArgumentParser(prog="raisa", description="Terminal client for the SCP Wiki",
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=f"keys:\n{keys}\n\nMore: man raisa")
    ap.add_argument("target", nargs="?", help="173, scp-682, 173-j, 001-jp, page slug or URL")
    ap.add_argument("-b", "--branch", choices=list(sites.BRANCHES), default="en",
                    help="wiki branch (default: en)")
    ap.add_argument("--version", action="version", version=f"raisa {VERSION}")
    args = ap.parse_args()
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        sys.exit("raisa: needs a terminal")
    try:
        from .app import App
    except ModuleNotFoundError as e:
        if e.name != "bs4":
            raise
        sys.exit("raisa: needs BeautifulSoup (python-beautifulsoup4, or pip install beautifulsoup4)")
    from .term import Term
    with Term() as term:
        app = App(term, args.branch)
        app.run(args.target)
    sys.stderr.write("".join(app.errors))      # the terminal is restored: now it can be read
