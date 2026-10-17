"""A notice dry run re-extracts stored pages without changing SQLite bytes."""
import csv
import gzip
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
import json
from concurrent.futures import Future

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from rwpipe import db
import notice_cue_impact as impact


def test_full_census_measures_a_false_notice_and_preserves_the_database(tmp_path):
    data = tmp_path / "data"
    (data / "interim").mkdir(parents=True)
    (data / "mhtml").mkdir()
    path = data / "interim/pipeline.sqlite"
    conn = sqlite3.connect(path)
    db.init(conn)
    old = "The samples were withdrawn at 10 minute intervals for chemical analysis."
    rel = "mhtml/sample.html.gz"
    (data / rel).write_bytes(gzip.compress(
        f"<html><head><title>A sampling experiment</title></head><body><p>{old}</p></body></html>".encode()))
    conn.execute("INSERT INTO records(record_id,title,original_doi) VALUES (1,?,?)",
                 ("A sampling experiment", "10.1234/sample"))
    conn.execute("INSERT INTO documents(doi,doi_prefix,shard,status,usable,mhtml_path) VALUES (?,?,0,'done',1,?)",
                 ("10.1234/sample", "10.1234", rel))
    conn.execute("INSERT INTO document_text(doi,notice,source_path,capture_format,quality,extractor_version) VALUES (?,?,?,'html','full',14)",
                 ("10.1234/sample", old, rel))
    conn.commit()
    conn.close()
    before = path.read_bytes()
    out = tmp_path / "impact.csv"
    env = dict(os.environ, RWPIPE_DATA=str(data), PYTHONIOENCODING="utf-8")
    result = subprocess.run([sys.executable, str(ROOT / "scripts/notice_cue_impact.py"),
                             "--all", "--workers", "1", "--base-rev", "none",
                             "--out", str(out)], env=env, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert len(rows) == 1 and rows[0]["change"] == "lost"
    assert rows[0]["selected_by"] == "full-census"
    assert path.read_bytes() == before
    assert not Path(str(path) + "-wal").exists()
    assert not Path(str(path) + "-shm").exists()


def test_full_census_does_not_skip_a_usable_capture_without_stored_text(tmp_path):
    data = tmp_path / "data"
    (data / "interim").mkdir(parents=True)
    (data / "mhtml").mkdir()
    path = data / "interim" / "pipeline.sqlite"
    conn = sqlite3.connect(path)
    db.init(conn)
    doi = "10.1234/new-notice"
    rel = "mhtml/notice.html.gz"
    (data / rel).write_bytes(gzip.compress(
        b"<html><body><p>This article has been withdrawn at the request of the editor.</p></body></html>"))
    conn.execute("INSERT INTO records(record_id,title,retraction_doi) VALUES (1,?,?)", ("Notice", doi))
    conn.execute("INSERT INTO documents(doi,doi_prefix,shard,status,usable,mhtml_path) VALUES (?,?,0,'done',1,?)",
                 (doi, "10.1234", rel))
    conn.commit()
    conn.close()
    before = path.read_bytes()
    out = tmp_path / "impact.csv"
    result = subprocess.run([sys.executable, str(ROOT / "scripts/notice_cue_impact.py"),
                             "--all", "--workers", "1", "--base-rev", "none", "--out", str(out)],
                            env=dict(os.environ, RWPIPE_DATA=str(data)), capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    with out.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1 and rows[0]["doi"] == doi
    assert rows[0]["change"] == "gained" and rows[0]["old_quality"] == "not-stored"
    assert path.read_bytes() == before


def test_baseline_is_rerun_even_when_new_and_stored_notices_match(monkeypatch):
    from rwpipe import extract, mhtml
    new = extract.Extracted(notice="Stored notice", body="body", notice_method="cue")
    old = extract.Extracted(notice="Different v14 notice", body="body", notice_method="cue")
    monkeypatch.setattr(impact, "_BASE", object())
    monkeypatch.setattr(impact.mhtml, "read", lambda p: mhtml.Document(html="<p>page</p>"))
    monkeypatch.setattr(impact, "_run", lambda mod, *args: new if mod is extract else old)
    r = impact._measure(dict(doi="10.1234/n", mhtml_path="n", role="retraction",
                             selected_by="full-census", old_notice=new.notice, old_body=""))
    assert r["base_notice"] == old.notice


def test_unreadable_capture_does_not_claim_a_successful_baseline_rerun(monkeypatch):
    from rwpipe import mhtml
    monkeypatch.setattr(impact.mhtml, "read", lambda p: mhtml.Document(error="bad MIME"))
    r = impact._measure(dict(doi="10.1234/n", mhtml_path="n", role="retraction",
                             selected_by="full-census", old_notice="", old_body=""))
    assert r["base_notice"] is None
    assert r["baseline_error"] == "bad MIME" and r["new_quality"] == "failed"


class InlineExecutor:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def submit(self, fn, task):
        future = Future()
        future.set_result(fn(task))
        return future


def test_checkpoint_reuses_only_completed_chunks_in_original_task_order(tmp_path, monkeypatch):
    monkeypatch.setattr(impact, "_checkpoint_signature", lambda *args: "stable")
    monkeypatch.setattr(impact, "ProcessPoolExecutor", InlineExecutor)
    calls = []
    def measure(t):
        calls.append(t["doi"])
        return {"doi": t["doi"], "new_notice": t["doi"]}
    monkeypatch.setattr(impact, "_measure", measure)
    tasks = [{"doi": "b"}, {"doi": "a"}]
    first = impact.measure_tasks(tasks, 1, None, tmp_path)
    (tmp_path / "incomplete-interrupted.json").write_text('{"torn', encoding="utf-8")
    assert impact.measure_tasks(tasks, 1, None, tmp_path) == first
    assert calls == ["b", "a"]


def test_checkpoint_refuses_changed_source_or_inputs(tmp_path, monkeypatch):
    (tmp_path / "manifest.json").write_text(json.dumps({"signature": "old"}), encoding="utf-8")
    monkeypatch.setattr(impact, "_checkpoint_signature", lambda *args: "new")
    with pytest.raises(RuntimeError, match="source or inputs changed"):
        impact.measure_tasks([], 1, None, tmp_path)


def test_checkpoint_refuses_duplicate_completed_results(tmp_path, monkeypatch):
    (tmp_path / "manifest.json").write_text(json.dumps({"signature": "stable"}), encoding="utf-8")
    (tmp_path / "chunk-duplicate.json").write_text(json.dumps([{"doi": "a"}, {"doi": "a"}]), encoding="utf-8")
    monkeypatch.setattr(impact, "_checkpoint_signature", lambda *args: "stable")
    with pytest.raises(RuntimeError, match="duplicate DOI"):
        impact.measure_tasks([{"doi": "a"}], 1, None, tmp_path)
