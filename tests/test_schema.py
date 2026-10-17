"""A fresh database has every table the pipeline writes to.

`altmetric_detail` and `altmetric_posts` were created by hand on the working
database while their collector was being written and never added to SCHEMA. On
this machine everything worked; on a clean rebuild
`collect_altmetric_detail.py` failed at its first INSERT, and the reason was
nowhere near the symptom.

So rather than list the tables by hand, this reads every INSERT and UPDATE in
the scripts and the package, and checks a freshly initialised database has
somewhere to put each one. A new collector that invents a table is caught the
first time the suite runs.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rwpipe import db

# An UPDATE only counts when SET follows the name, which keeps prose out:
# "the update lifecycle this project studies" and "this update should come
# from here" both parse as writes otherwise. `DO UPDATE SET` is excluded for
# the same reason - it is an upsert clause, and the table was already named by
# the INSERT it belongs to.
WRITE_RE = re.compile(
    r"""(?:INSERT\s+(?:OR\s+\w+\s+)?INTO\s+["'`\[]?(?P<insert>\w+)"""
    r"""|(?<!DO\s)UPDATE\s+["'`\[]?(?P<update>\w+)["'`\]]?\s+SET\b)""",
    re.IGNORECASE,
)
TEMP_RE = re.compile(r"CREATE\s+(?:TEMP|TEMPORARY)\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)",
                     re.IGNORECASE)


def source_files() -> list[Path]:
    return (sorted((ROOT / "scripts").glob("*.py"))
            + sorted((ROOT / "src" / "rwpipe").glob("*.py")))


def written_tables() -> dict[str, set[str]]:
    """Table -> the files that write to it, excluding scratch tables.

    A script may build a temp table and fill it in the same breath - crawl.py
    does, to rank hosts once instead of per page. Those are found by reading
    the CREATE TEMP TABLE next to them rather than by keeping a list here,
    which would go stale the same way the schema did.
    """
    temps: set[str] = set()
    raw: dict[str, set[str]] = {}
    for f in source_files():
        text = f.read_text(encoding="utf-8", errors="replace")
        temps |= {m.group(1).lower() for m in TEMP_RE.finditer(text)}
        for m in WRITE_RE.finditer(text):
            t = (m.group("insert") or m.group("update")).lower()
            raw.setdefault(t, set()).add(f.name)
    return {t: fs for t, fs in raw.items() if t not in temps}


@pytest.fixture(scope="module")
def fresh_tables(tmp_path_factory) -> set[str]:
    path = tmp_path_factory.mktemp("freshdb") / "pipeline.sqlite"
    conn = sqlite3.connect(path)
    db.init(conn)
    db.migrate(conn)
    names = {r[0].lower() for r in
             conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    conn.close()
    return names


def test_a_fresh_database_has_tables(fresh_tables):
    assert len(fresh_tables) > 10


@pytest.mark.parametrize("table", sorted(written_tables()))
def test_every_written_table_exists_in_a_fresh_database(table, fresh_tables):
    writers = ", ".join(sorted(written_tables()[table]))
    assert table in fresh_tables, (
        f"{writers} writes to {table!r}, which a fresh database does not have. "
        f"Add it to SCHEMA in src/rwpipe/db.py, or to NOT_SCHEMA here if it is "
        f"a scratch table."
    )


@pytest.mark.parametrize("table", ["altmetric_detail", "altmetric_posts"])
def test_the_two_that_were_missing(table, fresh_tables):
    assert table in fresh_tables


def test_altmetric_posts_can_take_a_row(tmp_path):
    """The columns, not just the name - a dated mention is the media analysis."""
    conn = sqlite3.connect(tmp_path / "p.sqlite")
    db.init(conn)
    conn.execute(
        """INSERT INTO altmetric_posts
           (doi, kind, post_id, title, summary, url, author, author_url,
            posted_on, country, license, raw)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("10.1/x", "news", "p1", "T", "S", "u", "a", "au",
         "2024-01-02T03:04:05Z", "GB", "lic", "{}"),
    )
    conn.execute(
        "INSERT INTO altmetric_detail (doi, posts_total, kinds, bytes, error, fetched_at)"
        " VALUES (?,?,?,?,?,?)",
        ("10.1/x", 1, '{"news": 1}', 42, None, "2026-09-24"),
    )
    conn.commit()
    assert conn.execute("SELECT posted_on FROM altmetric_posts").fetchone()[0].startswith("2024")
    # The primary key is (doi, kind, post_id): the same mention must not double.
    conn.execute(
        "INSERT OR REPLACE INTO altmetric_posts (doi, kind, post_id) VALUES (?,?,?)",
        ("10.1/x", "news", "p1"),
    )
    conn.commit()
    assert conn.execute("SELECT count(*) FROM altmetric_posts").fetchone()[0] == 1
    conn.close()
