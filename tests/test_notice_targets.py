"""The notice collector asks about the right DOIs, and only those.

The point of this collection is scope: 47,021 retraction-notice DOIs had never
been queried, which is where post-retraction news lives. Getting the target
list wrong is the one way to spend thirteen hours of someone else's API quota
and still not have asked the question.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from rwpipe import db

import importlib.util

spec = importlib.util.spec_from_file_location(
    "collect_altmetric_notices", ROOT / "scripts" / "collect_altmetric_notices.py")
notices = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notices)


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(tmp_path / "p.sqlite")
    c.row_factory = sqlite3.Row
    db.init(c)
    return c


def add_record(conn, record_id, original, retraction):
    conn.execute(
        "INSERT INTO records (record_id, original_doi, retraction_doi) VALUES (?,?,?)",
        (record_id, original, retraction))
    conn.commit()


def test_a_notice_with_its_own_doi_is_a_target(conn):
    add_record(conn, 1, "10.1/paper", "10.1/notice")
    assert notices.targets(conn, None) == [("10.1/notice", 1)]


def test_a_notice_sharing_the_paper_doi_is_not(conn):
    # Nothing separate to ask about, and `altmetric` already holds the answer.
    add_record(conn, 1, "10.1/same", "10.1/same")
    assert notices.targets(conn, None) == []


def test_case_and_whitespace_do_not_make_a_second_target(conn):
    add_record(conn, 1, "10.1/paper", "10.1/Notice")
    add_record(conn, 2, "10.1/other", " 10.1/NOTICE ")
    got = notices.targets(conn, None)
    assert got == [("10.1/notice", 2)], got


def test_n_papers_counts_the_papers_one_notice_retracts(conn):
    for i in range(5):
        add_record(conn, i, f"10.1/paper{i}", "10.1/mass")
    assert notices.targets(conn, None) == [("10.1/mass", 5)]


def test_the_same_paper_twice_is_still_one_paper(conn):
    add_record(conn, 1, "10.1/paper", "10.1/notice")
    add_record(conn, 2, "10.1/paper", "10.1/notice")
    assert notices.targets(conn, None) == [("10.1/notice", 1)]


def test_an_empty_retraction_doi_is_not_a_target(conn):
    add_record(conn, 1, "10.1/paper", "")
    add_record(conn, 2, "10.1/paper2", None)
    assert notices.targets(conn, None) == []


def test_a_notice_already_collected_is_skipped(conn):
    add_record(conn, 1, "10.1/paper", "10.1/notice")
    conn.execute("INSERT INTO altmetric_notice (retraction_doi, found) VALUES (?, 0)",
                 ("10.1/notice",))
    conn.commit()
    assert notices.targets(conn, None) == []


def test_a_notice_that_errored_is_retried(conn):
    # Recording a failure must not make it permanent.
    add_record(conn, 1, "10.1/paper", "10.1/notice")
    conn.execute(
        "INSERT INTO altmetric_notice (retraction_doi, found, error) VALUES (?,?,?)",
        ("10.1/notice", 0, "timeout"))
    conn.commit()
    assert notices.targets(conn, None) == [("10.1/notice", 1)]


def test_an_empty_error_string_counts_as_collected(conn):
    add_record(conn, 1, "10.1/paper", "10.1/notice")
    conn.execute(
        "INSERT INTO altmetric_notice (retraction_doi, found, error) VALUES (?,?,?)",
        ("10.1/notice", 1, ""))
    conn.commit()
    assert notices.targets(conn, None) == []


def test_biggest_notices_come_first(conn):
    add_record(conn, 1, "10.1/a", "10.1/small")
    for i in range(3):
        add_record(conn, 10 + i, f"10.1/b{i}", "10.1/big")
    got = notices.targets(conn, None)
    assert [d for d, _ in got] == ["10.1/big", "10.1/small"]
    assert got[0][1] == 3


def test_ordering_by_doi_is_available(conn):
    add_record(conn, 1, "10.1/a", "10.1/zzz")
    for i in range(3):
        add_record(conn, 10 + i, f"10.1/b{i}", "10.1/aaa")
    assert [d for d, _ in notices.targets(conn, None, order="doi")] == \
        ["10.1/aaa", "10.1/zzz"]


def test_limit_applies_after_ordering(conn):
    add_record(conn, 1, "10.1/a", "10.1/small")
    for i in range(3):
        add_record(conn, 10 + i, f"10.1/b{i}", "10.1/big")
    assert notices.targets(conn, 1) == [("10.1/big", 3)]


def test_notices_are_written_to_their_own_table(conn):
    """The paper table must not gain rows keyed on a notice."""
    add_record(conn, 1, "10.1/paper", "10.1/notice")
    conn.execute(
        """INSERT INTO altmetric_notice
           (retraction_doi, n_papers, found, news_count) VALUES (?,?,?,?)""",
        ("10.1/notice", 1, 1, 3))
    conn.commit()
    assert conn.execute("SELECT count(*) FROM altmetric").fetchone()[0] == 0
    row = conn.execute("SELECT n_papers, news_count FROM altmetric_notice").fetchone()
    assert (row[0], row[1]) == (1, 3)
