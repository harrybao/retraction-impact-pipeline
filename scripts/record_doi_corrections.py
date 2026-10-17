"""Record a DOI a publisher says is wrong, without editing the source dataset.

    python scripts/record_doi_corrections.py --list
    python scripts/record_doi_corrections.py

Retraction Watch occasionally carries a DOI the publisher does not recognise.
IOP found two of them in the list we sent for text-and-data-mining approval:

    10.1088/1742-6596/1382/1/012204  ->  10.1088/1742-6596/JPCS/1/012204
    10.1088/1751-8113/40/14/C01      ->  10.1088/1751-8121/40/14/C01

Both are `retraction_doi` values in `records` (ids 37119 and 36643), which is
to say they are the client's source data. Overwriting them in `documents` would
make our dataset stop matching the file it was built from, and every count in
the report is defended by that correspondence - so the correction goes in a
table beside the data rather than into it, the same way `pmc_ids`,
`oa_all_locations` and `wayback_captures` do.

The rule for anything that consumes this: **ask at `corrected_doi`, file the
result under `doi`.** The Retraction Watch identifier stays the key that joins
the dataset together; the correction is only an address to fetch from.

Idempotent. Add rows here as publishers report them.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console

from rwpipe import db

console = Console()

# (doi as Retraction Watch has it, what the publisher says it should be,
#  who told us, why)
CORRECTIONS = [
    ("10.1088/1742-6596/1382/1/012204",
     "10.1088/1742-6596/JPCS/1/012204",
     "IOP Publishing, 2026-09-09",
     "publisher corrected the DOI while reviewing our TDM request"),
    ("10.1088/1751-8113/40/14/C01",
     "10.1088/1751-8121/40/14/C01",
     "IOP Publishing, 2026-09-09",
     "journal moved from J. Phys. A 1751-8113 to 1751-8121; "
     "publisher corrected the DOI while reviewing our TDM request"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="show what is recorded")
    args = ap.parse_args()

    conn = db.connect()
    conn.execute("PRAGMA busy_timeout = 15000")
    conn.execute("""CREATE TABLE IF NOT EXISTS doi_corrections (
                        doi TEXT PRIMARY KEY,
                        corrected_doi TEXT NOT NULL,
                        source TEXT NOT NULL,
                        reason TEXT,
                        recorded_at TEXT)""")
    conn.commit()

    if args.list:
        rows = conn.execute("SELECT doi, corrected_doi, source, reason "
                            "FROM doi_corrections ORDER BY doi").fetchall()
        console.print(f"[bold]{len(rows)} recorded[/bold]")
        for d, cd, src, why in rows:
            console.print(f"\n  {d}\n    -> {cd}\n    [dim]{src} - {why}[/dim]")
        return 0

    for doi, corrected, source, reason in CORRECTIONS:
        # The point of the table is that the original stays reachable, so a
        # correction for a DOI we do not hold is a mistake worth stopping on.
        row = conn.execute("SELECT usable FROM documents WHERE lower(doi) = ?",
                           (doi.lower(),)).fetchone()
        if not row:
            console.print(f"[red]{doi} is not in documents - not recording[/red]")
            continue
        conn.execute(
            """INSERT OR REPLACE INTO doi_corrections
               (doi, corrected_doi, source, reason, recorded_at)
               VALUES (?,?,?,?, datetime('now'))""",
            (doi, corrected, source, reason))
        console.print(f"  {doi}\n    -> {corrected}   [dim]({source})[/dim]")
    conn.commit()

    n = conn.execute("SELECT COUNT(*) FROM doi_corrections").fetchone()[0]
    console.print(f"\n[bold]{n} corrections recorded[/bold]")
    console.print("[dim]documents and records are unchanged - fetch at "
                  "corrected_doi, file under doi[/dim]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
