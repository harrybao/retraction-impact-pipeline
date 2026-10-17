"""Identity exclusions must not survive through old Stage 2 export rows."""
import csv
import gzip
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "export_text_selection", Path(__file__).resolve().parents[1] / "scripts/export_text.py")
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)


def database():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE documents (doi TEXT PRIMARY KEY, usable INTEGER, source_kind TEXT);
        CREATE TABLE shared_bodies (doi TEXT PRIMARY KEY, kind TEXT);
        CREATE TABLE document_text (
          doi TEXT PRIMARY KEY, doc_role TEXT, host TEXT, capture_format TEXT, quality TEXT,
          extractor_version INTEGER, title TEXT, abstract TEXT, body TEXT, notice TEXT,
          notice_method TEXT, references_n INTEGER, method TEXT, source_path TEXT,
          extracted_at TEXT, error TEXT);
    """)
    return conn


def add(conn, doi, usable=1, version=15, document=True, text=True):
    if document:
        conn.execute("INSERT INTO documents VALUES (?,?,?)", (doi, usable, "fixture"))
    if text:
        conn.execute("INSERT INTO document_text VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (doi, "retraction", "example.org", "html", "notice-only", version,
                      "Notice", "", "", "This article has been retracted because data were fabricated.",
                      "cue", 0, "soup", "fixture.html", "2026-10-16", None))


@pytest.mark.parametrize("version", [14, 15])
def test_all_exports_use_same_current_selection(monkeypatch, tmp_path, version):
    conn = database()
    add(conn, "10.1234/selected", version=version)
    add(conn, "10.1234/excluded", usable=0, version=13)
    add(conn, "10.1234/unknown", usable=None, version=13)
    add(conn, "10.1234/orphan", document=False, version=13)
    monkeypatch.setattr(exporter.db, "connect", lambda: conn)
    monkeypatch.setattr(exporter, "OUT", tmp_path)
    assert exporter.main() == 0
    with (tmp_path / "stage2_index.csv").open(encoding="utf-8", newline="") as stream:
        assert [r["doi"] for r in csv.DictReader(stream)] == ["10.1234/selected"]
    with gzip.open(tmp_path / "stage2_notices.csv.gz", "rt", encoding="utf-8", newline="") as stream:
        assert [r["doi"] for r in csv.DictReader(stream)] == ["10.1234/selected"]
    with gzip.open(tmp_path / "stage2_fulltext.jsonl.gz", "rt", encoding="utf-8") as stream:
        assert [json.loads(line)["doi"] for line in stream] == ["10.1234/selected"]
    assert conn.execute("SELECT count(*) FROM document_text").fetchone()[0] == 4


@pytest.mark.parametrize("problem,match", [("missing", "no extracted row"), ("mixed", "mixed extractor versions")])
def test_partial_rebuild_fails_before_opening_any_export(monkeypatch, tmp_path, problem, match):
    conn = database()
    add(conn, "10.1234/first")
    add(conn, "10.1234/second", text=problem != "missing", version=14)
    monkeypatch.setattr(exporter.db, "connect", lambda: conn)
    monkeypatch.setattr(exporter, "OUT", tmp_path / "new-output")
    with pytest.raises(RuntimeError, match=match):
        exporter.main()
    assert not (tmp_path / "new-output").exists()


def test_empty_selection_is_valid_and_does_not_export_old_rows():
    conn = database()
    add(conn, "10.1234/excluded", usable=0)
    assert list(exporter.text_rows(conn)) == []


def test_readiness_checks_and_rows_share_one_snapshot():
    conn = database()
    assert not conn.in_transaction
    assert list(exporter.text_rows(conn)) == []
    assert conn.in_transaction
