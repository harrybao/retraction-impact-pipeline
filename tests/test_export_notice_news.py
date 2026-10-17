"""The notice exports the R analysis reads (2026-10-13).

Two things changed in scripts/export_notice_news.py, each with its reason:

* PlumX on the notice DOIs is exported (`plumx_notice.csv`). The news coverage
  over all four routes - Altmetric and PlumX, paper and notice DOIs - was
  counted from the database by hand on 27 September and had no producer in
  the repository, because these counts never reached data/clean.
* The export cannot write to the database. It opened it through db.connect()
  and ran the schema setup, which sets WAL mode and can add tables and
  columns; an export run against the collected dataset could change the file
  the handover package lists with its checksum.
"""
from __future__ import annotations

import csv
import hashlib
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rwpipe import db

SCRIPT = ROOT / "scripts" / "export_notice_news.py"


def make_data(tmp_path: Path, plumx_notice: bool = True) -> tuple[Path, Path]:
    """A scratch data directory whose database has one notice and its paper.

    Left in SQLite's default journal mode (the schema sets WAL), so that any
    write - even the switch to WAL that db.connect() makes - changes the file.
    """
    data = tmp_path / "data"
    (data / "interim").mkdir(parents=True)
    path = data / "interim" / "pipeline.sqlite"
    c = sqlite3.connect(path)
    db.init(c)
    db.migrate(c)
    c.execute("INSERT INTO records (record_id, original_doi, retraction_doi) "
              "VALUES (1, '10.1/paper', '10.1/notice')")
    c.execute("INSERT INTO altmetric_notice (retraction_doi, n_papers, found, news_count) "
              "VALUES ('10.1/notice', 1, 1, 2)")
    c.execute("INSERT INTO plumx_notice (retraction_doi, n_papers, found, detail_json) "
              "VALUES ('10.1/notice', 1, 1, '{\"mention.NEWS_COUNT\": 3}')")
    if not plumx_notice:
        c.execute("DROP TABLE plumx_notice")
    c.commit()
    c.execute("PRAGMA journal_mode=DELETE")
    c.close()
    return data, path


def run(data: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, RWPIPE_DATA=str(data), PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True,
                          text=True, encoding="utf-8")


def read(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_plumx_on_notice_dois_is_exported(tmp_path):
    data, _ = make_data(tmp_path)
    r = run(data)
    assert r.returncode == 0, r.stdout + r.stderr
    rows = read(data / "clean" / "plumx_notice.csv")
    assert len(rows) == 1
    assert rows[0]["retraction_doi"] == "10.1/notice"
    assert rows[0]["n_papers"] == "1"
    assert '"mention.NEWS_COUNT": 3' in rows[0]["detail_json"]


def test_the_export_leaves_the_database_unchanged(tmp_path):
    data, path = make_data(tmp_path)
    before = sha(path)
    r = run(data)
    assert r.returncode == 0, r.stdout + r.stderr
    assert sha(path) == before


def test_repeated_compressed_exports_are_byte_identical(tmp_path):
    data, _ = make_data(tmp_path)
    assert run(data).returncode == 0
    first = {p.name: p.read_bytes() for p in (data / "clean").glob("*.gz")}
    assert first
    assert run(data).returncode == 0
    assert {p.name: p.read_bytes() for p in (data / "clean").glob("*.gz")} == first
    assert all(content[4:8] == b"\x00\x00\x00\x00" for content in first.values())


def test_no_database_is_an_error_not_a_new_empty_one(tmp_path):
    # db.connect() creates the file it is pointed at, so the old export, run
    # where there was no database, made an empty one and exported empty tables
    # with exit status 0 - empty notice tables that the R analysis would have
    # read as "no coverage".
    data = tmp_path / "data"
    data.mkdir()
    r = run(data)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "no database" in r.stdout
    assert not (data / "interim" / "pipeline.sqlite").exists()
    assert not (data / "clean").exists()


def test_a_database_without_plumx_notices_refuses_a_partial_export(tmp_path):
    # A database collected before `collect_plumx.py --notices` existed.
    data, _ = make_data(tmp_path, plumx_notice=False)
    r = run(data)
    assert r.returncode == 1, r.stdout + r.stderr
    assert not (data / "clean").exists()
    assert "no plumx_notice table" in r.stdout


def test_incomplete_old_schema_does_not_mix_a_previous_successful_export(tmp_path):
    data, path = make_data(tmp_path)
    assert run(data).returncode == 0
    before = {p.name: p.read_bytes() for p in (data / "clean").iterdir()}
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE plumx_notice")
    conn.execute("UPDATE altmetric_notice SET news_count=99")
    conn.commit()
    conn.close()
    assert run(data).returncode == 1
    assert {p.name: p.read_bytes() for p in (data / "clean").iterdir()} == before
