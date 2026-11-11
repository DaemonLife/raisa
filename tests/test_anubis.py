"""The Anubis solver on synthetic pages.

This checks our logic against the protocol description, not the real server:
the format of the check page on the site may change over time.
"""
import hashlib
import json
import urllib.parse

import pytest

from raisa import anubis


def challenge_page(challenge, rules=None, prefix=""):
    data = {"challenge": challenge, "rules": rules or {}}
    return (f'<html><script id="anubis_challenge" type="application/json">{json.dumps(data)}</script>'
            f'<script id="anubis_base_prefix" type="application/json">{json.dumps(prefix)}</script></html>')


def test_proof_of_work():
    nonce, digest = anubis.proof_of_work("abc", 3)
    assert digest == hashlib.sha256(f"abc{nonce}".encode()).hexdigest()
    assert digest.startswith("000")
    assert anubis.proof_of_work("abc", 4)[1].startswith("0000")


def test_solve_fast():
    calls = []
    body = challenge_page({"randomData": "r4nd", "id": "cid", "method": "fast", "difficulty": 2})
    anubis.solve("https://scpfoundation.net/scp-173", body, {"X": "1"}, lambda u, h: calls.append((u, h)))
    (url, headers), = calls
    u = urllib.parse.urlsplit(url)
    q = dict(urllib.parse.parse_qsl(u.query))
    assert u.path == anubis.PASS_PATH and u.netloc == "scpfoundation.net"
    assert q["response"] == hashlib.sha256(("r4nd" + q["nonce"]).encode()).hexdigest()
    assert q["response"].startswith("00") and q["id"] == "cid"
    assert q["redir"] == "https://scpfoundation.net/scp-173"
    assert headers["Sec-Fetch-Site"] == "same-origin" and headers["X"] == "1"


def test_solve_rules_and_prefix():
    calls = []
    body = challenge_page({"randomData": "r"}, {"algorithm": "fast", "difficulty": 1}, prefix="/anubis")
    anubis.solve("https://h/x", body, {}, lambda u, h: calls.append(u))
    assert calls[0].startswith("https://h/anubis" + anubis.PASS_PATH)


def test_solve_preact(monkeypatch):
    monkeypatch.setattr(anubis.time, "sleep", lambda s: None)
    calls = []
    body = challenge_page({"randomData": "zz", "id": "i1", "method": "preact", "difficulty": 1})
    anubis.solve("https://h/p", body, {}, lambda u, h: calls.append(u))
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(calls[0]).query))
    assert q["result"] == hashlib.sha256(b"zz").hexdigest() and q["id"] == "i1"


@pytest.mark.parametrize("body", [
    "<html>no challenge</html>",
    challenge_page({"randomData": "x", "method": "unknown-algo"}),
    challenge_page({"randomData": "x", "method": "fast", "difficulty": 99}),
    challenge_page({"randomData": "", "method": "fast"}),
    '<script id="anubis_challenge">{broken json</script>',
])
def test_solve_rejects_bad_challenges(body):
    with pytest.raises(anubis.AnubisError):
        anubis.solve("https://h/", body, {}, lambda u, h: None)


def test_solve_preact_info_link(monkeypatch):
    """Anubis 1.23 (scpfoundation.net, 2026): the ready pass link is in preact_info, as the browser uses it."""
    monkeypatch.setattr(anubis.time, "sleep", lambda s: None)
    info = {"challenge": "c0ffee", "difficulty": 5,
            "redir": "/.within.website/x/cmd/anubis/api/pass-challenge?id=01ab&redir=%2Fscp-173"}
    body = challenge_page({"randomData": "c0ffee", "id": "01ab", "method": "preact"}, {"algorithm": "preact"}) \
        + f'<script id="preact_info" type="application/json">{json.dumps(info)}</script>'
    calls = []
    anubis.solve("https://scpfoundation.net/scp-173", body, {}, lambda u, h: calls.append(u))
    result = hashlib.sha256(b"c0ffee").hexdigest()
    assert calls == ["https://scpfoundation.net/.within.website/x/cmd/anubis/api/pass-challenge"
                     f"?id=01ab&redir=%2Fscp-173&result={result}"]
