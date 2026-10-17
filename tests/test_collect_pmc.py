"""The PubMed Central collector's handling of NCBI refusals, without the network.

NCBI refuses in spells (HTTP 429 for minutes, measured on 6 October 2026). The
converter request waits them out and then gives up with a message R looks for;
the full-text fetch reads the status before the body, so a refusal is not
recorded as a wrong record.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rwpipe import db  # noqa: E402

_spec = importlib.util.spec_from_file_location("collect_pmc",
                                               ROOT / "scripts" / "collect_pmc.py")
pmc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pmc)


def client(*responses):
    """A client whose transport answers with `responses` in turn (the last
    one repeated), recording every request."""
    seen = []

    def handler(request):
        seen.append(request)
        status, body, headers = responses[min(len(seen), len(responses)) - 1]
        return httpx.Response(status, text=body, headers=headers or {})

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def no_sleep(monkeypatch):
    waits = []
    monkeypatch.setattr(pmc.time, "sleep", waits.append)
    return waits


def test_ask_waits_out_a_spell_of_429s(monkeypatch):
    waits = no_sleep(monkeypatch)
    c, seen = client((429, "", None), (429, "", None),
                     (200, '{"records": [{"doi": "10.1/x", "pmcid": "PMC1"}]}', None))
    assert pmc.ask(c, ["10.1/x"])["records"][0]["pmcid"] == "PMC1"
    assert waits == [15, 30] and len(seen) == 3
    assert seen[0].url.host == "pmc.ncbi.nlm.nih.gov"        # the documented address


def test_ask_honours_retry_after_and_ignores_a_date(monkeypatch):
    waits = no_sleep(monkeypatch)
    c, _ = client((429, "", {"Retry-After": "7"}),
                  (429, "", {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
                  (200, '{"records": []}', None))
    assert pmc.ask(c, ["10.1/x"]) == {"records": []}
    assert waits == [7.0, 30]


def test_ask_gives_up_with_the_message_r_looks_for(monkeypatch, capsys):
    waits = no_sleep(monkeypatch)
    c, seen = client((429, "", None))
    assert pmc.ask(c, ["10.1/x"]) is None
    assert len(seen) == len(pmc.RETRY_WAITS) + 1
    assert waits == list(pmc.RETRY_WAITS)                     # no wait after the last
    assert "gave up after" in capsys.readouterr().out


def fetch_db(tmp_path, n=2):
    conn = db.connect(tmp_path / "pipeline.sqlite")
    db.init(conn)
    for i in range(n):
        conn.execute("INSERT INTO documents (doi, doi_prefix, status, usable) "
                     "VALUES (?, '10.1', 'blocked', 0)", (f"10.1/p{i}",))
        conn.execute("INSERT INTO pmc_ids (doi, pmcid, pmid, status, checked_at) "
                     "VALUES (?, ?, NULL, 'found', datetime('now'))", (f"10.1/p{i}", f"PMC{i}"))
    conn.commit()
    return conn


def test_a_refused_fetch_is_an_error_not_a_wrong_record(monkeypatch, tmp_path):
    no_sleep(monkeypatch)
    conn = fetch_db(tmp_path, 1)
    c, _ = client((503, "<html>Service unavailable</html>", None))
    pmc.fetch_all(conn, c, None)
    status, error = conn.execute("SELECT status, error FROM documents").fetchone()
    assert status == "blocked" and error == "pmc: http 503"


def test_a_long_spell_stops_the_fetch_and_leaves_the_rest(monkeypatch, tmp_path, capsys):
    no_sleep(monkeypatch)
    conn = fetch_db(tmp_path, 2)
    c, seen = client((429, "", None))
    pmc.fetch_all(conn, c, None)
    assert len(seen) == len(pmc.RETRY_WAITS) + 1              # the second record is not tried
    assert "gave up after" in capsys.readouterr().out
    assert [r[0] for r in conn.execute("SELECT error FROM documents")] == [None, None]


def test_a_blank_error_body_does_not_crash_the_converter_request(monkeypatch):
    no_sleep(monkeypatch)
    c, _ = client((502, " \n", None))
    assert pmc.ask(c, ["10.1/x"]) is None


def test_an_error_inside_a_200_answer_is_not_read_as_not_found(monkeypatch):
    no_sleep(monkeypatch)
    c, _ = client((200, '{"status": "error", "errmsg": "bad request"}', None))
    assert pmc.ask(c, ["10.1/x"]) is None


def test_retry_after_is_capped_and_nan_is_ignored():
    def resp(v):
        return httpx.Response(429, headers={"Retry-After": v})
    assert pmc.retry_after(resp("86400")) == float(pmc.RETRY_WAITS[-1])
    assert pmc.retry_after(resp("nan")) == 0.0
    assert pmc.retry_after(resp("-5")) == 0.0


def test_the_id_lookup_stops_after_a_refusal_spell(monkeypatch, tmp_path, capsys):
    no_sleep(monkeypatch)
    monkeypatch.setattr(pmc, "BATCH", 1)                       # one DOI per request
    conn = db.connect(tmp_path / "pipeline.sqlite")
    db.init(conn)
    for i in range(3):
        conn.execute("INSERT INTO documents (doi, doi_prefix, status, usable) "
                     "VALUES (?, '10.1', 'blocked', 0)", (f"10.1/q{i}",))
    conn.commit()
    c, seen = client((429, "", None))
    pmc.resolve(conn, c, None)
    assert len(seen) == len(pmc.RETRY_WAITS) + 1              # the 2nd and 3rd not asked
    out = capsys.readouterr().out
    assert "gave up after" in out and "3 not asked" in out
    assert conn.execute("SELECT COUNT(*) FROM pmc_ids").fetchone()[0] == 0
