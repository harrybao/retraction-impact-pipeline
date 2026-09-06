"""Columns that are computed and then not written.

Every check here exists because a value was derived correctly, stored nowhere,
and would have been destroyed by the next ordinary run. That failure leaves no
trace: the code that computes it is right, the tests for that code pass, and
the loss only appears later as a column full of NULLs that nobody can explain.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

ROOT = Path(__file__).resolve().parents[1]


def _insert_columns(source: str, table: str) -> set[str]:
    """The column list of an INSERT into `table`, as written."""
    m = re.search(r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+%s\s*\(([^)]*)\)" % table,
                  source, re.S | re.I)
    assert m, "no INSERT INTO %s found" % table
    return {c.strip() for c in m.group(1).replace("\n", " ").split(",") if c.strip()}


def test_engagement_writes_the_window_flag():
    """`window_covers_retraction` decides whether a paper can support a
    before/after comparison at all. It was computed by engagement.py, omitted
    from this INSERT, and the statement is INSERT OR REPLACE - so re-running
    the collector would have reset all 62,867 flags to NULL and turned
    "cannot be measured" back into "measured as zero". That distinction was
    the subject of a published correction."""
    src = (ROOT / "scripts" / "collect_engagement.py").read_text(encoding="utf-8")
    cols = _insert_columns(src, "engagement")
    assert "window_covers_retraction" in cols, \
        "computed but not persisted; a re-run would null it"


def test_engagement_persists_every_column_it_computes():
    """A weaker guard that survives renames: anything the Engagement dataclass
    exposes and the schema has a column for should be written."""
    from rwpipe import engagement  # noqa: PLC0415

    src = (ROOT / "scripts" / "collect_engagement.py").read_text(encoding="utf-8")
    cols = _insert_columns(src, "engagement")
    schema = (ROOT / "src" / "rwpipe" / "db.py").read_text(encoding="utf-8")
    m = re.search(r"CREATE TABLE IF NOT EXISTS engagement\s*\((.*?)\n\);", schema, re.S)
    assert m
    declared = {ln.strip().split()[0] for ln in m.group(1).splitlines()
                if ln.strip() and not ln.strip().startswith(("--", "PRIMARY", "FOREIGN"))}
    # Columns the schema declares that the writer never mentions at all.
    never_written = declared - cols - {"id"}
    assert not never_written, \
        "declared but never written by the collector: %s" % sorted(never_written)
