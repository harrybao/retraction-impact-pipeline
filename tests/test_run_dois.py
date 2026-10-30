"""The handover test entry point: which pages a list of items queues.

The client will test the pipeline with a few extreme examples from the CSV.
Each case here is one of those extremes: a notice shared by many records, a
record whose two DOIs are the same, a record with no usable DOI, a DOI that
is not in the CSV.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rwpipe import db  # noqa: E402
from rwpipe.csvload import clean_doi  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "run_dois", Path(__file__).resolve().parents[1] / "scripts" / "run_dois.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

COLS = ["Title", "Subject", "Journal", "Publisher", "Country", "ArticleType",
        "RetractionNature", "Reason", "Paywalled", "RetractionDate", "OriginalPaperDate"]


def frame(rows):
    """rows: (record id, original DOI cell, retraction DOI cell)."""
    df = pd.DataFrame([{**{c: "x" for c in COLS}, "Record ID": rid,
                        "OriginalPaperDOI": o, "RetractionDOI": r} for rid, o, r in rows])
    df["original_doi"] = df["OriginalPaperDOI"].map(clean_doi)
    df["retraction_doi"] = df["RetractionDOI"].map(clean_doi)
    return df


DF = frame([
    (1, "10.1000/a", "10.1000/a-notice"),          # an ordinary pair
    (2, "10.1000/b", "10.1000/mass"),              # three papers, one notice
    (3, "10.1000/c", "10.1000/mass"),
    (4, "10.1000/d", "10.1000/mass"),
    (5, "10.1000/same", "10.1000/same"),           # one DOI in both columns
    (6, "unavailable", "10.1000/f-notice"),     # no original DOI
    (7, "", "unavailable"),                  # no DOI at all
])


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "scratch.sqlite")
    db.init(c)
    return c


def pages(conn):
    return sorted(r[0] for r in conn.execute("SELECT doi FROM documents"))


def test_a_doi_queues_only_that_page(conn):
    _mod.queue(conn, DF, _mod.resolve(DF, ["https://doi.org/10.1000/A"]))
    assert pages(conn) == ["10.1000/a"]


def test_a_shared_notice_queues_one_page_but_keeps_every_record(conn):
    res = _mod.resolve(DF, ["10.1000/mass"])
    assert res[0]["record_ids"] == [2, 3, 4]
    _mod.queue(conn, DF, res)
    assert pages(conn) == ["10.1000/mass"]
    assert conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 3


def test_a_record_queues_both_of_its_pages(conn):
    _mod.queue(conn, DF, _mod.resolve(DF, ["record:1"]))
    assert pages(conn) == ["10.1000/a", "10.1000/a-notice"]


def test_a_record_with_the_same_doi_twice_is_one_document(conn):
    _mod.queue(conn, DF, _mod.resolve(DF, ["record:5"]))
    assert pages(conn) == ["10.1000/same"]


def test_a_record_without_an_original_doi_fetches_its_notice_and_says_so(conn):
    res = _mod.resolve(DF, ["record:6"])
    assert res[0]["pages"] == ["10.1000/f-notice"]
    assert "no usable original DOI" in res[0]["note"]


def test_items_with_nothing_to_fetch_are_reported_not_dropped(conn):
    res = _mod.resolve(DF, ["record:7", "record:99", "10.9999/elsewhere", "unavailable"])
    assert [r["pages"] for r in res] == [[], [], [], []]
    assert "no usable DOI" in res[0]["note"]
    assert "not in the Retraction Watch CSV" in res[1]["note"]
    assert "not in the Retraction Watch CSV" in res[2]["note"]
    assert res[3]["note"] == "not a DOI"
    assert _mod.queue(conn, DF, res) == 0


def test_the_collected_dataset_is_refused(conn, tmp_path):
    conn.executemany("INSERT INTO documents (doi, doi_prefix) VALUES (?, '10.1')",
                     [(f"10.1000/{i}",) for i in range(_mod.MAX_SCRATCH_DOCUMENTS + 1)])
    with pytest.raises(SystemExit):
        _mod.check_scratch(conn, tmp_path)


@pytest.mark.parametrize("status,error,usable,quality,expect", [
    ("done", None, 1, "full", "ok"),
    ("done", None, 0, None, "not this paper"),
    ("blocked", "http 403", None, None, "HTTP 403"),
    ("blocked", "challenge title: Just a moment...", None, None, "bot check"),
    ("blocked", "body too small (12 KB) - interstitial", None, None, "interstitial"),
    ("failed", "TimeoutError: page.goto", None, None, "network error"),
    ("pending", None, None, None, "not attempted"),
])
def test_every_outcome_is_explained(status, error, usable, quality, expect):
    assert expect in _mod.explain(status, error, usable, quality)


def test_only_bot_checks_are_retried(conn):
    conn.executemany(
        "INSERT INTO documents (doi, doi_prefix, status, error) VALUES (?, '10.1000', 'blocked', ?)",
        [("10.1000/challenge", "challenge title: Client Challenge"),
         ("10.1000/interstitial", "body too small (12 KB) - interstitial"),
         ("10.1000/forbidden", "http 403"),
         ("10.1000/wrong", "identity check failed: capture is 855 KB but does not contain it")])
    assert _mod.requeue_transient(conn) == 2
    still = sorted(r[0] for r in conn.execute("SELECT doi FROM documents WHERE status='blocked'"))
    assert still == ["10.1000/forbidden", "10.1000/wrong"]


# --- DOIs the CSV does not hold: run all the same, with Crossref's metadata ---
def date(y, m, d):
    return {"date-parts": [[y, m, d]]}


CROSSREF = {
    # a paper Crossref lists as retracted (and corrected: the correction is ignored),
    # titled as Springer titles them
    "10.2000/paper": {"title": ["RETRACTED ARTICLE: A study"], "container-title": ["J. Things"],
                      "publisher": "Pub", "type": "journal-article", "issued": date(2013, 6, 4),
                      "updated-by": [{"DOI": "10.2000/correction", "type": "correction", "label": "Correction",
                                      "updated": date(2014, 1, 1)},
                                     {"DOI": "10.2000/notice", "type": "retraction", "label": "Retraction",
                                      "updated": date(2020, 10, 8)}]},
    # a retraction notice, and the paper it retracts (listed twice, as Crossref does
    # when the publisher and Retraction Watch both report it)
    "10.2000/notice": {"title": ["Retracted: A study"], "container-title": ["J. Things"],
                       "publisher": "Pub", "type": "journal-article", "issued": date(2020, 10, 8),
                       "update-to": [{"DOI": "10.2000/paper", "type": "retraction", "label": "Retraction",
                                      "updated": date(2020, 10, 8)},
                                     {"DOI": "10.2000/PAPER", "type": "retraction", "label": "Retraction",
                                      "updated": date(2020, 10, 8), "source": "retraction-watch"}]},
    # a paper Crossref lists no retraction of
    "10.2000/plain": {"title": ["Fine work"], "container-title": ["J. Other"], "publisher": "Pub2",
                      "type": "journal-article", "issued": date(2019, 2, 3),
                      "updated-by": [{"DOI": "10.2000/fix", "type": "correction", "label": "Correction"}]},
    # one notice retracting three papers
    "10.2000/mass": {"title": ["Retraction note"], "container-title": ["J. Mass"], "publisher": "Pub",
                     "type": "journal-article",
                     "update-to": [{"DOI": f"10.2000/m{i}", "type": "retraction", "label": "Retraction",
                                    "updated": date(2026, 9, 1)} for i in (1, 2, 3)]},
    "10.2000/m1": {"title": ["First paper"]},
    # a Springer article carrying its own retraction
    "10.2000/self": {"title": ["RETRACTED ARTICLE: Self"], "type": "journal-article",
                     "update-to": [{"DOI": "10.2000/self", "type": "retraction", "label": "Retraction",
                                    "updated": date(2021, 3, 1)}]},
    # a title with JATS/MathML markup
    "10.2000/math": {"title": ['T-duality in AdS<mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML">'
                               '<mml:msub><mml:mi/><mml:mn>5</mml:mn></mml:msub></mml:math> &amp; <i>more</i>']},
}


def fake_crossref(doi):
    return CROSSREF.get(doi)


def down(doi):
    raise _mod.CrossrefUnavailable(doi)


def test_an_unlisted_retracted_paper_takes_its_retraction_from_crossref():
    (rec,), note = _mod.unlisted_record("10.2000/paper", fetch=fake_crossref)
    assert (rec["original_doi"], rec["retraction_doi"]) == ("10.2000/paper", "10.2000/notice")
    assert rec["RetractionDate"] == "10/8/2020 0:00" and rec["RetractionNature"] == "Retraction"
    assert (rec["Title"], rec["Journal"], rec["Publisher"]) == ("A study", "J. Things", "Pub")
    assert "not in the Retraction Watch CSV" in note and "retraction of it" in note
    assert "10.2000/notice, 2020-10-08" in note


def test_an_unlisted_notice_is_filed_as_the_csv_files_notices():
    (rec,), note = _mod.unlisted_record("10.2000/notice", fetch=fake_crossref)
    assert (rec["original_doi"], rec["retraction_doi"]) == ("10.2000/paper", "10.2000/notice")
    assert rec["Title"] == "A study"                 # the paper's title, as in the CSV
    assert rec["OriginalPaperDate"] == "6/4/2013 0:00"
    assert "retraction notice for 10.2000/paper" in note


def test_a_notice_retracting_several_papers_gets_a_record_per_paper():
    recs, note = _mod.unlisted_record("10.2000/mass", fetch=fake_crossref)
    assert [(r["original_doi"], r["retraction_doi"]) for r in recs] == [
        ("10.2000/m1", "10.2000/mass"), ("10.2000/m2", "10.2000/mass"), ("10.2000/m3", "10.2000/mass")]
    assert [r["Title"] for r in recs] == ["First paper", "", ""]
    assert len({r["Record ID"] for r in recs}) == 3
    assert "notice for 3 papers" in note


def test_an_article_carrying_its_own_retraction_is_held_in_both_columns():
    (rec,), note = _mod.unlisted_record("10.2000/self", fetch=fake_crossref)
    assert (rec["original_doi"], rec["retraction_doi"], rec["Title"]) == ("10.2000/self", "10.2000/self", "Self")
    assert "on its own page" in note


def test_an_unlisted_paper_without_a_retraction_runs_as_an_ordinary_paper():
    (rec,), note = _mod.unlisted_record("10.2000/plain", fetch=fake_crossref)
    assert (rec["original_doi"], rec["retraction_doi"], rec["RetractionDate"]) == ("10.2000/plain", None, "")
    assert "lists no retraction" in note


def test_a_doi_crossref_does_not_know_still_runs_by_its_doi():
    (rec,), note = _mod.unlisted_record("10.2000/unknown", fetch=fake_crossref)
    assert rec["original_doi"] == "10.2000/unknown" and rec["Title"] == ""
    assert "Crossref has no record" in note


def test_crossref_not_answering_is_not_reported_as_crossref_not_knowing():
    (rec,), note = _mod.unlisted_record("10.2000/paper", fetch=down)
    assert "did not answer" in note and "has no record" not in note
    assert rec["original_doi"] == "10.2000/paper"


def test_once_crossref_is_down_the_run_stops_asking_it():
    calls = []

    def flaky(doi):
        calls.append(doi)
        raise _mod.CrossrefUnavailable(doi)

    f = _mod.ask_once_down(flaky)
    for d in ("10.2000/a", "10.2000/b", "10.2000/c"):
        with pytest.raises(_mod.CrossrefUnavailable):
            f(d)
    assert calls == ["10.2000/a"]


def test_crossref_titles_lose_their_markup():
    (rec,), _ = _mod.unlisted_record("10.2000/math", fetch=fake_crossref)
    assert rec["Title"] == "T-duality in AdS 5 & more"


def test_a_percent_encoded_doi_is_asked_for_once_encoded(monkeypatch):
    seen = []

    class Answer:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"message": {"title": ["x"]}}'

    def urlopen(req, timeout):
        seen.append(req.full_url)
        return Answer()

    monkeypatch.setattr(_mod.urllib.request, "urlopen", urlopen)
    _mod.crossref_work("10.1002/(sici)1097-0215(19990517)81:4%3c669::aid-ijc27%3e3.0.co;2-c")
    assert "%3C669" in seen[0] and "%253c" not in seen[0].lower()


def test_unlisted_record_ids_are_negative_and_stable():
    (a,), _ = _mod.unlisted_record("10.2000/plain", fetch=fake_crossref)
    (b,), _ = _mod.unlisted_record("10.2000/plain", fetch=fake_crossref)
    (c,), _ = _mod.unlisted_record("10.2000/unknown", fetch=fake_crossref)
    assert a["Record ID"] == b["Record ID"] < 0 and a["Record ID"] != c["Record ID"]


def test_a_paper_and_its_notice_requested_together_share_one_record(conn):
    res = _mod.resolve(DF, ["10.2000/paper", "10.2000/notice"])
    df = _mod.add_unlisted(DF, res, lookup=lambda d: _mod.unlisted_record(d, fetch=fake_crossref))
    assert res[0]["record_ids"] == res[1]["record_ids"]
    assert _mod.queue(conn, df, res) == 2
    assert conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 1


def test_unlisted_dois_are_queued_with_records_of_their_own(conn):
    asked = []

    def lookup(doi):
        asked.append(doi)
        return _mod.unlisted_record(doi, fetch=fake_crossref)

    res = _mod.resolve(DF, ["10.2000/paper", "https://doi.org/10.2000/PAPER", "10.1000/a",
                            "unavailable", "record:99", "record:6"])
    df = _mod.add_unlisted(DF, res, lookup=lookup)
    assert asked == ["10.2000/paper"]                   # asked once for a repeated DOI
    assert [r["kind"] for r in res] == ["unlisted", "unlisted", "doi", "invalid", "record", "record"]
    assert res[0]["pages"] == ["10.2000/paper"] and res[0]["record_ids"][0] < 0
    assert res[3]["pages"] == [] and res[4]["pages"] == []   # not a DOI, unknown record: not run
    assert _mod.queue(conn, df, res) == 3
    assert pages(conn) == ["10.1000/a", "10.1000/f-notice", "10.2000/paper"]
    title, notice = conn.execute("SELECT title, retraction_doi FROM records WHERE original_doi = ?",
                                 ("10.2000/paper",)).fetchone()
    assert (title, notice) == ("A study", "10.2000/notice")
    # the CSV's own rows keep NULL for a missing DOI (an empty string would be
    # sent to the citation and attention services as a DOI)
    assert conn.execute("SELECT original_doi FROM records WHERE record_id = 6").fetchone()[0] is None
    assert len(DF) == 7                                  # the CSV's own frame is not changed


def test_an_unregistered_doi_is_explained_and_not_retried(conn):
    assert "not registered" in _mod.explain("blocked", "body too small (3 KB) - interstitial",
                                            None, None, 404, "doi.org")
    conn.execute("INSERT INTO documents (doi, doi_prefix, status, error, http_status, final_host) "
                 "VALUES ('10.9999/x', '10.9999', 'blocked', 'body too small (3 KB) - interstitial', "
                 "404, 'doi.org')")
    assert _mod.requeue_transient(conn) == 0


def test_the_dataset_column_does_not_blame_the_csv_snapshot_for_unlisted_dois():
    out = _mod.load_reference(None, {"10.2000/plain", "10.1000/a"}, frozenset({"10.2000/plain"}))
    assert out == {"10.2000/plain": "not in our dataset (not in the Retraction Watch CSV)"}
