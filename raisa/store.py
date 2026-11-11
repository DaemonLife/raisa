"""Bookmarks and the last open page of every branch in ~/.local/share/raisa/state.json:

    {"bookmarks": [{"branch": "en", "slug": "scp-173", "title": "SCP-173"}],
     "last": {"en": ["scp-173", [12, 0]]}}

Every change re-reads the file under a lock (flock) and writes it through a temporary
file, so two running copies never overwrite each other. A broken file is kept as state.json.bad.
"""
import contextlib
import fcntl
import json
import os
from pathlib import Path

from .config import DATA
from .text import one_line


class Store:
    def __init__(self, path: Path | None = None):
        self.path = path or DATA / "state.json"
        self.d = self._read()

    def _read(self) -> dict:
        try:
            d = json.loads(self.path.read_text("utf-8"))
        except FileNotFoundError:
            d = {}
        except (OSError, ValueError):
            with contextlib.suppress(OSError):
                self.path.replace(self.path.with_suffix(".json.bad"))
            d = {}
        d = d if isinstance(d, dict) else {}
        marks: dict = {}        # broken records are dropped, duplicates too
        for x in d.get("bookmarks") or []:
            if isinstance(x, dict) and isinstance(x.get("branch"), str) and isinstance(x.get("slug"), str):
                marks.setdefault((x["branch"], x["slug"]), one_line(str(x.get("title") or x["slug"])))
        d["bookmarks"] = [{"branch": b, "slug": s, "title": t} for (b, s), t in marks.items()]
        d["last"] = d["last"] if isinstance(d.get("last"), dict) else {}
        return d

    def _update(self, change):
        """Re-read the file under a lock, apply change(d), write it back. If that fails, the
        change is still applied (once), in memory."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path.with_name("state.lock"), "a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                self.d = self._read()
                result = change(self.d)
                with contextlib.suppress(OSError):      # a full disk
                    tmp = self.path.with_name(f"state.{os.getpid()}.tmp")
                    tmp.write_text(json.dumps(self.d, ensure_ascii=False, indent=1), "utf-8")
                    tmp.replace(self.path)
                return result
        except OSError:         # no data folder or no locks there
            return change(self.d)

    def rows(self) -> list[dict]:
        return self.d["bookmarks"]

    def is_bookmarked(self, branch, slug) -> bool:
        return any((x["branch"], x["slug"]) == (branch, slug) for x in self.d["bookmarks"])

    def toggle(self, branch, slug, title) -> bool:
        """Add the bookmark or remove it. True: it is bookmarked now."""
        def change(d):
            marks = [x for x in d["bookmarks"] if (x["branch"], x["slug"]) != (branch, slug)]
            added = len(marks) == len(d["bookmarks"])
            d["bookmarks"] = marks + [{"branch": branch, "slug": slug, "title": one_line(title)}] * added
            return added
        return self._update(change)

    def last(self, branch) -> tuple[str, tuple[int, int]] | None:
        """(slug, reading position) of the last open page of the branch."""
        try:
            slug, (para, offset) = self.d["last"][branch]
            return str(slug), (int(para), int(offset))
        except (KeyError, TypeError, ValueError):
            return None

    def set_last(self, branch, slug, anchor):
        if self.last(branch) != (slug, tuple(anchor)):
            self._update(lambda d: d["last"].__setitem__(branch, [slug, list(anchor)]))
