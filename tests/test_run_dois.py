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
