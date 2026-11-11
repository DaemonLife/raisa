"""Passing the Anubis check (https://anubis.techaro.lol) on the Russian site.

Instead of a captcha the browser solves a puzzle and gets a cookie pass for about a week.
Variants: fast/slow — find a nonce such that SHA-256(randomData + nonce) starts with
`difficulty` zeros; preact — hash SHA-256(randomData) and wait; metarefresh — wait and
follow a link. The check is tied to the client IP. solve() gets open_url(url, headers)
and does no I/O itself, so it is testable offline.
"""
import hashlib
import json
import re
import time
import urllib.parse

PASS_PATH = "/.within.website/x/cmd/anubis/api/pass-challenge"
MAX_NONCE = 50_000_000      # searching further is pointless


class AnubisError(RuntimeError):
    pass


def is_challenge(body: str) -> bool:
    """Is this the check instead of an article? Its scripts always live in /.within.website/."""
    return "anubis_challenge" in body or "/.within.website/" in body


def _json(body: str, script_id: str):
    m = re.search(rf'<script[^>]*id="{script_id}"[^>]*>(.*?)</script>', body, re.S)
    try:
        return json.loads(m.group(1)) if m else None
    except ValueError:
        return None


def proof_of_work(rand: str, difficulty: int) -> tuple[int, str]:
    """A nonce such that SHA-256(rand + nonce) starts with `difficulty` hex zeros."""
    base = hashlib.sha256(rand.encode())
    full, half = divmod(difficulty, 2)      # two hex zeros make one zero byte
    for nonce in range(MAX_NONCE):
        h = base.copy()
        h.update(str(nonce).encode())
        d = h.digest()
        if d[:full] == bytes(full) and (not half or d[full] < 16):
            return nonce, d.hex()
    raise AnubisError(f"difficulty {difficulty} is too high")


def solve(url: str, body: str, headers: dict, open_url) -> None:
    """Solve the check in `body` and send the answer (the server then sets a cookie)."""
    u = urllib.parse.urlsplit(url)
    data = _json(body, "anubis_challenge")
    if not isinstance(data, dict):
        meta = re.search(r'http-equiv="?refresh"?[^>]+content="(\d+);\s*url=([^"]+)"', body, re.I)
        if not (meta and "anubis" in body.lower()):
            raise AnubisError("challenge data not found")
        time.sleep(min(int(meta.group(1)), 30))         # metarefresh
        return open_url(urllib.parse.urljoin(url, meta.group(2).replace("&amp;", "&")), headers)
    rules, ch = data.get("rules") or {}, data.get("challenge")
    ch = ch if isinstance(ch, dict) else {}
    rand, algo = str(ch.get("randomData") or ""), ch.get("method") or rules.get("algorithm") or "fast"
    try:
        diff = int(ch.get("difficulty") or rules.get("difficulty") or 4)
    except (TypeError, ValueError) as e:
        raise AnubisError("bad difficulty") from e
    if not rand:
        raise AnubisError("empty challenge")
    prefix = _json(body, "anubis_base_prefix")
    q, same_origin = {"redir": url}, dict(headers, **{"Sec-Fetch-Site": "same-origin"})
    if ch.get("id"):
        q["id"] = ch["id"]
    if algo == "preact":
        q["result"] = hashlib.sha256(rand.encode()).hexdigest()
        time.sleep(diff * 0.125 + 0.3)      # like the browser: an answer that comes too fast is rejected
        info = _json(body, "preact_info")   # Anubis 1.2x gives the ready pass link, as the browser uses it
        if isinstance(info, dict) and isinstance(info.get("redir"), str):
            link = urllib.parse.urljoin(url, info["redir"])
            query = urllib.parse.urlencode({"result": q["result"]})
            return open_url(link + ("&" if "?" in link else "?") + query, same_origin)
    elif algo in ("fast", "slow") and 0 < diff <= 16:
        t0 = time.time()
        nonce, q["response"] = proof_of_work(rand, diff)
        q.update(nonce=str(nonce), elapsedTime=str(max(int((time.time() - t0) * 1000), 1)))
    else:
        raise AnubisError(f"unsupported challenge '{algo}' ({diff})")
    base = f"{u.scheme}://{u.netloc}{prefix if isinstance(prefix, str) else ''}{PASS_PATH}"
    open_url(base + "?" + urllib.parse.urlencode(q), same_origin)
