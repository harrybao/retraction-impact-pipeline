"""One row for every file in data/mhtml, so the 36 GB is navigable.

    python scripts/export_archive_index.py

Writes data/clean/archive_index.csv.

The archive is stored as `mhtml/<doi-prefix>/<xx>/<sha1>.mhtml.gz`. That is the
right layout for a crawler - it never collides and it never needs renaming when
a DOI is corrected - and it is unreadable for a person. Handed the folder on
its own, a teammate can tell you how many files there are and nothing else.

`documents.mhtml_path` maps 89,023 of them. The other 10,386 have no database
row at all: they are captures the verifier rejected, whose path was cleared
when they were marked unusable. They are still on disk, deliberately - they are
the evidence that the refusals were real rather than a bug in our client - so
an index that skipped them would misdescribe the folder it claims to index.

Those are recovered here from the file itself. MHTML stores the URL it was
saved from in a `Snapshot-Content-Location` header in the first few hundred
bytes, so identifying them costs one small read each rather than a full
decompression.
"""
from __future__ import annotations

import csv
import gzip
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console

from rwpipe import config

console = Console()

OUT = config.DATA / "clean" / "archive_index.csv"
LOCATION = re.compile(rb"Snapshot-Content-Location:\s*(\S+)", re.I)
# A DOI inside a publisher URL: /doi/10.1016/j.foo, /retrieve/pii/..., etc.
DOI_IN_URL = re.compile(r"(10\.\d{4,9}/[^\s?&#]+)", re.I)

COLUMNS = ["path", "sha1", "doi_prefix", "doi", "doc_role", "publisher",
           "journal", "host", "capture_format", "status", "usable", "verdict",
           "title_similarity", "gz_bytes", "in_database", "note"]


def source_url(path: Path) -> str:
    """The URL a capture was saved from, from its MHTML header. Read short:
    the header sits at the top, and these files run to 875 KB."""
    try:
        with gzip.open(path, "rb") as fh:
            head = fh.read(2048)
    except Exception:                                      # noqa: BLE001
        return ""
    m = LOCATION.search(head)
    return m.group(1).decode("utf-8", "ignore") if m else ""


def main() -> int:
    conn = sqlite3.connect(f"file:{config.DATA / 'interim' / 'pipeline.sqlite'}?mode=ro",
                           uri=True)
    conn.execute("PRAGMA busy_timeout=60000")

    console.print("[dim]reading the database...[/dim]")
    known: dict[str, tuple] = {}
    for row in conn.execute("""
            SELECT d.mhtml_path, d.doi, d.doi_prefix, d.final_host,
                   d.capture_format, d.status, d.usable, d.gz_bytes,
                   a.verdict, a.title_similarity, a.doc_role,
                   COALESCE(r1.publisher, r2.publisher) AS publisher,
                   COALESCE(r1.journal,   r2.journal)   AS journal
            FROM documents d
            LEFT JOIN archive_checks a ON a.doi = d.doi
            LEFT JOIN records r1 ON r1.original_doi   = d.doi
            LEFT JOIN records r2 ON r2.retraction_doi = d.doi
            WHERE d.mhtml_path IS NOT NULL"""):
        known[row[0].replace("\\", "/")] = row
    console.print(f"  {len(known):,} files named by documents.mhtml_path")

    root = config.DATA / "mhtml"
    on_disk = sorted(p for p in root.rglob("*.gz"))
    console.print(f"  {len(on_disk):,} files on disk")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    n_known = n_orphan = n_named = 0
    with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(COLUMNS)
        for p in on_disk:
            rel = p.relative_to(config.DATA).as_posix()
            sha1 = p.name.split(".")[0]
            prefix = rel.split("/")[1]
            row = known.get(rel)
            if row:
                n_known += 1
                (_, doi, dprefix, host, fmt, status, usable, gz,
                 verdict, sim, role, publisher, journal) = row
                w.writerow([rel, sha1, dprefix or prefix, doi, role or "",
                            publisher or "", journal or "", host or "", fmt,
                            status, 1 if usable == 1 else 0, verdict or "",
                            "" if sim is None else sim,
                            gz if gz is not None else p.stat().st_size, 1, ""])
            else:
                # No database row. Recover what the file says about itself so
                # the row is still identifiable rather than just a hash.
                n_orphan += 1
                url = source_url(p)
                m = DOI_IN_URL.search(url)
                doi = m.group(1).lower() if m else ""
                if doi:
                    n_named += 1
                w.writerow([rel, sha1, prefix, doi, "", "", "", "", "mhtml",
                            "rejected", 0, "not-in-database", "",
                            p.stat().st_size, 0,
                            f"capture rejected at verification; saved from {url}"
                            if url else "capture rejected at verification"])

    console.print(f"\n[bold]{OUT}[/bold]")
    console.print(f"  {n_known + n_orphan:,} rows")
    console.print(f"    {n_known:,} in the database")
    console.print(f"    {n_orphan:,} rejected captures, "
                  f"{n_named:,} of which still name a DOI in their saved URL")
    console.print(f"  {OUT.stat().st_size/1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
