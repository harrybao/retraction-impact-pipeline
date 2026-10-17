"""No production files are used: upgrading an existing corpus is explicit."""
import hashlib
import importlib.util
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_old_text_is_refused_before_schema_or_database_writes(tmp_path):
    data = tmp_path / "data"
    path = data / "interim/pipeline.sqlite"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE document_text(doi TEXT, extractor_version INTEGER)")
        c.execute("INSERT INTO document_text VALUES ('10.1234/example', 14)")
    before = hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns
    r = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/extract_text.py")],
                       env=dict(os.environ, RWPIPE_DATA=str(data)), capture_output=True,
                       text=True, encoding="utf-8")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "--upgrade explicitly" in " ".join(r.stdout.split())
    assert (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) == before
    assert not Path(str(path) + "-wal").exists()


def test_shared_target_is_extracted_only_in_its_reviewed_notice_role():
    from rwpipe import db
    spec = importlib.util.spec_from_file_location("role_upgrade_test", ROOT / "scripts/extract_text.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init(conn)
    conn.execute("INSERT INTO records(record_id,original_doi,retraction_doi) VALUES (1,'10.1234/both','10.1234/both')")
    conn.execute("INSERT INTO documents(doi,doi_prefix,usable,mhtml_path) VALUES ('10.1234/both','10.1234',1,'example')")
    assert module.targets(conn, None, True)[0]["doc_role"] == "both"
    conn.execute("INSERT INTO archive_checks(doi,matched_role) VALUES ('10.1234/both','retraction')")
    assert module.targets(conn, None, True)[0]["doc_role"] == "retraction"
    conn.close()
