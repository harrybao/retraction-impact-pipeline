"""Stage 0 -> build the crawl frontier from the Retraction Watch CSV.

    python scripts/build_queue.py

Idempotent: re-running refreshes record metadata and inserts any new pages
without touching the status of pages already fetched.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console

from rwpipe import config, db
from rwpipe.csvload import build_tasks, load_records

console = Console()


def main() -> int:
    console.print(f"[bold]reading[/bold] {config.RAW_CSV}")
    df = load_records()
    console.print(f"  {len(df):,} records parsed")

    tasks = build_tasks(df)
    n_skipped = len(df) * 2 - len(tasks)
    console.print(f"  {len(tasks):,} pages queued, {n_skipped:,} DOI slots unusable")

    conn = db.connect()
    db.init(conn)

    # What the incoming file says about records we already hold. Read before
    # writing, so a DOI that changed upstream can be reported rather than
    # quietly rewritten underneath an archived capture.
    incoming = {
        int(r["Record ID"]): (r["original_doi"], r["retraction_doi"])
        for _, r in df.iterrows()
    }
    existing = {
        r["record_id"]: (r["original_doi"], r["retraction_doi"])
        for r in conn.execute(
            "SELECT record_id, original_doi, retraction_doi FROM records")
    }
    doi_changed = [(rid, existing[rid], incoming[rid])
                   for rid in incoming.keys() & existing.keys()
                   if existing[rid] != incoming[rid]]
    new_records = incoming.keys() - existing.keys()
    gone = existing.keys() - incoming.keys()

    # Every non-key column, not the five that happened to be listed. A record
    # corrected upstream - a fixed DOI, a revised retraction date, a
    # reclassified subject - could not previously reach the database at all.
    conn.executemany(
        """INSERT INTO records (record_id,title,subject,journal,publisher,country,
                article_type,retraction_nature,reason,paywalled,retraction_date,
                original_date,original_doi,retraction_doi)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(record_id) DO UPDATE SET
                title=excluded.title, subject=excluded.subject,
                journal=excluded.journal, publisher=excluded.publisher,
                country=excluded.country, article_type=excluded.article_type,
                retraction_nature=excluded.retraction_nature,
                reason=excluded.reason, paywalled=excluded.paywalled,
                retraction_date=excluded.retraction_date,
                original_date=excluded.original_date,
                original_doi=excluded.original_doi,
                retraction_doi=excluded.retraction_doi""",
        [
            (int(r["Record ID"]), r["Title"], r["Subject"], r["Journal"],
             r["Publisher"], r["Country"], r["ArticleType"], r["RetractionNature"],
             r["Reason"], r["Paywalled"], r["RetractionDate"], r["OriginalPaperDate"],
             r["original_doi"], r["retraction_doi"])
            for _, r in df.iterrows()
        ],
    )

    if new_records:
        console.print(f"  [green]{len(new_records):,} new records[/green]")
    if gone:
        # Kept, not deleted: a withdrawn retraction record is itself a finding,
        # and pages have already been fetched and analysed against it. Removing
        # it is the client's call.
        console.print(f"  [yellow]{len(gone):,} records no longer in the source "
                      f"- kept, not deleted[/yellow]")
    if doi_changed:
        console.print(f"  [red]{len(doi_changed):,} records changed a DOI[/red]")
        console.print("  [red]  any page already fetched under the old DOI is now "
                      "filed under a paper it is not[/red]")
        for rid, was, now in doi_changed[:10]:
            console.print(f"      {rid}: {was[0] or '-'} / {was[1] or '-'}")
            console.print(f"        ->  {now[0] or '-'} / {now[1] or '-'}")
    # OR IGNORE keeps already-fetched pages untouched on re-run
    conn.executemany(
        """INSERT OR IGNORE INTO pages (record_id,kind,doi,doi_prefix)
           VALUES (?,?,?,?)""",
        [(t.record_id, t.kind, t.doi, t.prefix) for t in tasks],
    )
    conn.commit()

    # Collapse to distinct DOIs. Many records legitimately share one page:
    # a single mass-retraction notice can cover hundreds of papers, and some
    # records carry the same DOI in both columns.
    n_docs = db.sync_documents(conn)
    console.print(
        f"  {n_docs:,} distinct DOIs to fetch "
        f"({len(tasks) - n_docs:,} duplicate fetches avoided)")

    console.print("\n[bold]frontier status[/bold]")
    for status, n in sorted(db.counts_by_status(conn).items()):
        console.print(f"  {status:<8} {n:>8,}")

    top = conn.execute(
        """SELECT doi_prefix, COUNT(*) n FROM documents
           GROUP BY doi_prefix ORDER BY n DESC LIMIT 10"""
    ).fetchall()
    console.print("\n[bold]top DOI prefixes[/bold]")
    for r in top:
        console.print(f"  {r['doi_prefix']:<10} {r['n']:>8,}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
