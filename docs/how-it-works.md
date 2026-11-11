# How raisa works

A short guide to the code: what lives where, how data gets from the site to the screen, and where to change things. The details are in the docstrings of the modules and functions.

## Files

| File | What it is for |
|---|---|
| `cli.py` | command-line arguments, the list of keys for `--help`, start-up |
| `app.py` | the state of the program, actions, keys, the main loop |
| `ui.py` | drawing a frame: the top line, the bookmarks and page columns, the search line |
| `views.py` | the cursor in the bookmarks list, scrolling the page, selecting links |
| `text.py` | cleaning text, character width, line wrapping |
| `style.py` | text attributes (bit flags) and colours (`THEME`) |
| `parse.py` | parsing the HTML of a page: paragraphs, links, images, the object class |
| `search.py` | search: the Crom index and the API of the Russian site, the page of results |
| `net.py` | downloading pages and images, the disk cache, cookies |
| `anubis.py` | the Anubis check on the Russian site |
| `sites.py` | the branches and parsing addresses ("173" → `scp-173`) |
| `store.py` | the bookmarks and the last page of every branch |
| `term.py` | the terminal: modes, keys in any layout, pastes, whether it draws Sixel |
| `images.py` | images: downloading in the background, cutting, Sixel and `▀` characters, animations |
| `config.py` | paths, how long the cache is fresh, limits |

## From start-up to the screen

1. `cli.main()` parses the arguments. `-b` sets the branch, which does not change while the program runs.
2. `Term` puts the terminal into raw mode and the alternate screen, and asks it about the kitty keyboard protocol and Sixel. On exit, also on an error or a signal, everything is restored.
3. `App.startup()` opens the page from the command line, otherwise the last page of the branch, otherwise the main page.
4. The main loop `App.run()`: an event (a key, a paste, a finished image) → an action → a new frame.

```
site ──net.fetch_page──▶ HTML ──parse.Page──▶ paragraphs (Para)
     (the disk cache)                            │
                                  text.wrap ◀────┘  to the width of the text
                                      │
                         screen lines (Line) ──ui.render──▶ terminal
```

A `Para` is a paragraph: a list of pieces `[text, attributes, link number]`, indents and a kind (text, code, a blank line, a rule, an image). A `Line` is a line of the screen; it remembers the number of its paragraph, so the reading position survives a change of the window width. Attributes are bit flags from `style.py`: `B | LINK` is a bold link.

## The main decisions

- **Parsing HTML.** BeautifulSoup builds the tree and repairs broken markup by itself. `_clean` removes what a reader of the site does not see (scripts, rating widgets, hidden blocks), then `Walker` walks the tree and collects the paragraphs. A framed block gets a rule above and below; the frame is looked for both in the block's `style` and in the CSS of the page. Series lists, hubs and the main page are pages like any article. The main page gets the site menu from its side bar inserted at the top (`_menu`).
- **Safety.** All text from a page goes through `text.clean()`: control characters are cut out, otherwise a page could command the terminal. A second check sits in `text.fit()`, right before the output.
- **Width and wrapping.** A character takes 0, 1 or 2 cells, so `sw()` measures a string, not `len()`. A link and the comma after it wrap together; CJK text may break after any character.
- **Keys.** With the kitty protocol the terminal reports the physical key, so the hotkeys do not depend on the layout (`kitty_key`). Without it plain bytes arrive, and the letters of the Russian layout are turned into the keys of the US one by the `RU_US` table. A key is looked up in the `BINDINGS` table and calls the method `App.do_<name>`.
- **A frame.** `ui.render()` builds the whole screen as one string and writes it at once, as a synchronized update: the screen does not flicker. The bookmarks column is as wide as the longest title, but at most 30% of the window (`pane_layout`).
- **Images.** Sixel is drawn over the cells and does not scroll by itself, so an image is cut into strips one text line high (`cut_frames`), and the visible strips are written again in every frame. Without Sixel a strip is a line of `▀` characters. The number of frames of an animation is limited by how much data the terminal can take (`RATE`, `MAX_FRAMES`); one that is too heavy stays on its first frame. Downloading and cutting run in a thread pool, and the main loop collects the results with `Images.poll()`.
- **Background loading.** Everything that touches the network goes through `App.busy()`: the work runs in a thread, a spinner turns on the screen, and `Esc` cancels the wait. Functions that run in the thread never touch `App`: they only download, parse and return the result.
- **Back and forward.** `App.trail` is the list of steps `[address, reading position]`, `App.pos` is the current one. A new page cuts off everything "ahead". Jumps with `g`/`G` are steps too, but at most `INSIDE_STEPS` of them are kept in a row. The reading position is the number of a paragraph, not of a line (`ArticleView.anchor`).
- **Search.** The results are an ordinary page: `search.results_page()` builds HTML, and the same `Page` parses it, so links, bookmarks and "back" work on the results. Crom finds titles (five at most); text is searched only by the API of the Russian site, which is asked again for fewer results when it answers with error 500.
- **Storage.** The bookmarks and the last pages live in `state.json`. Every change re-reads the file under a `flock` lock and writes it through a temporary file, so two copies of the program do not overwrite each other's data. The page cache is fresh for a day (`PAGE_TTL`); without a network a stale copy opens too.
- **Anubis.** If a check comes instead of a page, `net.get()` calls `anubis.solve()`, gets the cookie pass and repeats the request. The pass is kept in `cookies.txt` in the cache folder.

## Typical changes

- **A key.** The `BINDINGS` table in `app.py`: `"j": ("move", 1)` calls `do_move(1)`. The list of keys is repeated in `KEYS` (`cli.py`), in `raisa.1` and in the README; the test `test_keys_are_the_same_everywhere` checks that they match.
- **A new action.** A method `do_<name>` in `App` and a line in `BINDINGS`.
- **Colours.** The `THEME` dictionary in `style.py`; only the 16 terminal colours are used.
- **A branch.** A line in the `_ALL` list in `sites.py`: the code, the address and the pattern of the names of its own objects.
- **A block that should not show in articles.** Its CSS class goes into `SKIP_CLASSES` in `parse.py`.
- **Cache lifetime and text width.** `PAGE_TTL` in `config.py`, `TEXT_WIDTH` in `ui.py`.

## Tests

```sh
python -m pytest -q
python -m pytest tests/test_parse.py -k menu -q     # one file or test
```

| File | What it checks |
|---|---|
| `test_parse.py` | parsing HTML, on the pages in `tests/fixtures/` |
| `test_text.py` | wrapping, width, the Sixel encoder |
| `test_term.py` | parsing keys with the kitty protocol and without it |
| `test_sites.py` | addresses and branches |
| `test_views.py` | the bookmarks column, scrolling, image sizes, animations |
| `test_app.py` | navigation, bookmarks, search: the application in-process with a local "site" |
| `test_anubis.py`, `test_net_store.py` | the Anubis check, the network, the cache, search, the store |
| `test_tui.py` | the whole program in a virtual terminal (needs `pyte`) |

The tests never go online: `tests/harness.py` starts a local server and closes the way to the rest of the network. The test pages in `tests/fixtures/` are written by hand after the Wikidot markup: there are no wiki texts in the repository. If a page of the site is parsed wrongly, bring its markup into a test with a text of your own (the wiki texts are licensed under CC BY-SA 3.0), write a test modelled on `test_article_structure` and fix `parse.py` until it passes.

An error on one page does not crash the program: a message appears in the top line, and the details are printed to the terminal after exit (`App.guarded`).
