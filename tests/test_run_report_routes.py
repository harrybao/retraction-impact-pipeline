"""The run report says what each recovery route answered for a page still not
held, so a refusal cannot hide behind the crawler's reason (7 October 2026)."""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
_spec = importlib.util.spec_from_file_location("run_dois", ROOT / "scripts" / "run_dois.py")
rd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rd)


def test_routes_tried_reports_pmc_and_the_archive():
    conn = sqlite3.connect(":memory:")
    assert rd.routes_tried(conn, "10.1/x") == []                  # routes not run
    conn.execute("CREATE TABLE pmc_ids (doi TEXT, pmcid TEXT)")
    conn.execute("CREATE TABLE wayback_captures (doi TEXT, status TEXT)")
    conn.execute("INSERT INTO pmc_ids VALUES ('10.1/x', NULL)")
    conn.execute("INSERT INTO wayback_captures VALUES ('10.1/x', 'other-doi')")
    assert rd.routes_tried(conn, "10.1/x") == [
        "PubMed Central: does not hold it",
        "Internet Archive: holds a copy that declares another DOI (kept for review)"]
    assert rd.routes_tried(conn, "10.1/unasked") == [
        "PubMed Central: not asked (NCBI did not answer)",
        "Internet Archive: not asked (the Archive did not answer)"]
