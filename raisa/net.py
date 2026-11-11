"""Downloading pages and images (passing the Anubis check), disk cache, cookies.
All functions are safe to call from background threads.
"""
import contextlib
import hashlib
import http.cookiejar
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import anubis, sites
from .config import CACHE, HTTP_TIMEOUT, MAX_IMAGE_BYTES, MAX_PAGE_BYTES, PAGE_TTL


class NotFound(Exception):
    """The page does not exist (HTTP 404)."""


class HttpError(Exception):
    def __init__(self, code: int, url: str):
        super().__init__(f"HTTP {code}: {url}")
        self.code = code


class NotAnImage(Exception):
    """A page came instead of an image, or the file is too big."""


HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0",
           "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
           "Accept-Language": "en-US,en;q=0.5"}
COOKIE_FILE = CACHE / "cookies.txt"     # the Anubis pass survives restarts
_opener: urllib.request.OpenerDirector | None = None
_lock = threading.Lock()             # creating _opener
_anubis_lock = threading.Lock()      # solve one check at a time


def _open(url: str, headers: dict, limit: int = MAX_PAGE_BYTES) -> bytes:
    global _opener
    with _lock:
        if _opener is None:
            jar = http.cookiejar.MozillaCookieJar(str(COOKIE_FILE))
            with contextlib.suppress(OSError, http.cookiejar.LoadError):
                jar.load(ignore_discard=True)
            _opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
            _opener.jar = jar
    with _opener.open(urllib.request.Request(url, headers=headers), timeout=HTTP_TIMEOUT) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise NotAnImage(url)
    return data


def _solve(url: str, body: str, headers: dict):
    """Pass the Anubis check and save the cookie pass."""
    with _anubis_lock:
        anubis.solve(url, body, headers, _open)
        with contextlib.suppress(OSError):
            COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _opener.jar.save(ignore_discard=True)
            os.chmod(COOKIE_FILE, 0o600)


def get(url: str, binary: bool = False, headers: dict | None = None, limit: int = MAX_PAGE_BYTES):
    """Download a page (str) or a file (bytes); on an Anubis check solve it and retry."""
    headers = headers or HEADERS
    for attempt in range(2):
        try:
            data = _open(url, headers, limit)
        except urllib.error.HTTPError as e:
            with e:             # the error holds the connection: close it
                body = e.read(500_000).decode("utf-8", "replace") if attempt == 0 and e.code != 404 else ""
            if e.code == 404:
                raise NotFound(url) from e
            if "anubis" not in body.lower():
                raise HttpError(e.code, url) from e
            _solve(url, body, headers)
            continue
        is_html = data[:1024].lstrip().lower().startswith((b"<!doctype html", b"<html", b"<head"))
        if binary and not is_html:
            return data
        text = data.decode("utf-8", "replace")
        if attempt == 0 and anubis.is_challenge(text[:500_000]):
            _solve(url, text, headers)
            continue
        if binary:
            raise NotAnImage(url)
        if not anubis.is_challenge(text[:500_000]):
            return text
    raise anubis.AnubisError(f"challenge not accepted: {url}")


def cache_path(kind: str, key: str, ext: str) -> Path:
    """Cache file name: a readable part of the URL plus a hash."""
    h = hashlib.sha1(key.encode()).hexdigest()[:16]
    return CACHE / kind / f"{re.sub(r'[^a-zA-Z0-9._-]', '_', key)[-60:]}-{h}{ext}"


def _save(path: Path, data: bytes):
    """Write through a temporary file, so an interrupted write leaves no half file.
    The cache is optional: with a full disk work without it."""
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_bytes(data)
        tmp.replace(path)


def fetch_page(url: str) -> str:
    """A fresh cached copy, otherwise download and save; offline, a stale copy."""
    p = cache_path("pages", url, ".html")
    cached = p.read_text("utf-8", "replace") if p.exists() else None
    if cached and time.time() - p.stat().st_mtime < PAGE_TTL:
        return cached
    try:
        html = get(url)
    except NotFound:
        raise
    except Exception:
        if cached:              # offline: an old article is better than none
            return cached
        raise
    if len(html) < 200:
        raise NotFound(url)
    _save(p, html.encode())
    return html


def fetch_image(url: str, referer: str) -> Path:
    """Download an image into the cache (or take it from there). Retry once on network errors."""
    ext = os.path.splitext(url.split("?")[0])[1][:6].lower()
    p = cache_path("images", url, ext if re.fullmatch(r"\.[a-z0-9]+", ext) else ".img")
    if p.exists():
        return p
    headers = dict(HEADERS, Accept="image/*,*/*;q=0.5", Referer=referer)
    error: Exception = NotAnImage(url)
    for candidate in sites.image_candidates(url):
        for _ in range(2):              # a network error (OSError) gets one more try
            try:
                _save(p, get(candidate, binary=True, headers=headers, limit=MAX_IMAGE_BYTES))
                return p
            except Exception as e:
                error = e
                if not isinstance(e, OSError):
                    break
    raise error


def describe_error(e: BaseException) -> str:
    """A human-readable error message."""
    reason = getattr(e, "reason", e)
    if isinstance(reason, TimeoutError):
        return "the server did not answer in time"
    if isinstance(e, HttpError):
        return f"the server returned {e.code}"
    if isinstance(e, urllib.error.URLError):
        return f"no connection ({getattr(reason, 'strerror', None) or reason})"
    return str(e) or type(e).__name__
