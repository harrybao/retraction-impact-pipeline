"""Stage 1b - collect downstream engagement for every retracted paper.

    python scripts/collect_engagement.py                 # citations, all records
    python scripts/collect_engagement.py --limit 500     # a sample first
    python scripts/collect_engagement.py --wikipedia     # add the Wikipedia pass

Two passes, because they cost very different amounts:

  citations   OpenAlex, 50 DOIs per request - the whole corpus in ~1,400 calls
  wikipedia   one search per DOI, so it is opt-in and runs second

Keyed on the *original* paper's DOI. The question is whether a retracted work
keeps being cited, so engagement belongs to the paper, not to its notice.

Touches only OpenAlex and Wikipedia, so it runs safely alongside the crawler.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx
from rich.console import Console

from rwpipe import db, engagement
from rwpipe.clean import parse_date

console = Console()


def targets(conn, limit: int | None, redo: bool) -> list[tuple[str, int, int | None]]:
    """(original_doi, record_id, retraction_year) for records not yet collected."""
    sql = """
      SELECT r.original_doi AS doi, r.record_id,
             CAST(substr(r.retraction_date, -4) AS INTEGER) AS ryear
      FROM records r
      WHERE r.original_doi IS NOT NULL
    """
    if not redo:
        # a throttled row is unfinished work, not a result - pick it back up
        sql += (" AND r.original_doi NOT IN ("
                "SELECT doi FROM engagement WHERE error IS NULL"
                " OR error = 'not in openalex')")
    sql += " GROUP BY r.original_doi ORDER BY r.record_id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [(r["doi"], r["record_id"], r["ryear"]) for r in conn.execute(sql)]


def retraction_years(conn) -> dict[str, int]:
    """Earliest retraction year per original DOI, built once up front.

    Parsing happens in Python, not SQL. The records table holds the raw CSV
    dates ("3/15/2012 0:00"), and SQLite's strftime only understands ISO - it
    returns NULL for every row without raising, which silently zeroed the
    entire before/after split on the first run. Reusing clean.parse_date also
    keeps the two 18th-century records working.

    A paper can carry several notices: an expression of concern later upgraded
    to a retraction. The earliest is when readers were first warned, so that is
    the boundary the split should use.
    """
    out: dict[str, int] = {}
    for r in conn.execute(
            "SELECT original_doi doi, retraction_date d FROM records "
            "WHERE original_doi IS NOT NULL AND retraction_date IS NOT NULL "
            "AND retraction_date <> ''"):
        ts = parse_date(r["d"])
        if ts is None:
            continue
        y = int(ts.year)
        if r["doi"] not in out or y < out[r["doi"]]:
            out[r["doi"]] = y
    return out


async def collect_citations(conn, rows, concurrency: int) -> Counter:
    stats: Counter = Counter()
    ryears = retraction_years(conn)
    console.print(f"[dim]retraction years resolved for {len(ryears):,} papers[/dim]")
    batches = [rows[i:i + engagement.BATCH] for i in range(0, len(rows), engagement.BATCH)]
    sem = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()
    started = time.time()

    async with httpx.AsyncClient(follow_redirects=True) as client:
        async def do(batch) -> None:
            dois = [d for d, _, _ in batch]
            async with sem:
                got = await engagement.openalex_batch(client, dois)
            async with lock:
                for doi, rec_id, _ in batch:
                    e = got.get(doi) or engagement.Engagement(doi=doi, error="missing")
                    ry = ryears.get(doi)
                    before, during, after = e.split_by_year(ry)
                    # `window_covers_retraction` is written here, with the row
                    # it describes. It was computed by `engagement.py` and left
                    # out of this INSERT, and because the statement is
                    # INSERT OR REPLACE, re-running this collector would have
                    # reset all 62,867 existing flags to NULL - silently
                    # destroying the only record of which papers can support a
                    # before/after comparison at all. That distinction was the
                    # subject of a published correction; losing it costs more
                    # than the citation counts themselves.
                    # `wikipedia_count` and `wikipedia_pages` are filled by a
                    # later pass in this same script, and by collect_wikipedia.py
                    # across eleven language editions. INSERT OR REPLACE writes
                    # a whole row, so re-running this collector would have reset
                    # both to NULL for all 3,174 papers that have them - work
                    # done by a different collector, destroyed by this one.
                    # They are carried forward explicitly rather than left to
                    # the row-replacement to decide.
                    conn.execute(
                        """INSERT OR REPLACE INTO engagement
                           (doi, record_id, openalex_id, work_type, cited_by_count,
                            counts_by_year, citations_before, citations_during,
                            citations_after, referenced_works_count, is_oa,
                            retraction_year, window_covers_retraction,
                            wikipedia_count, wikipedia_pages,
                            error, fetched_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,
                                   (SELECT wikipedia_count FROM engagement WHERE doi = ?),
                                   (SELECT wikipedia_pages FROM engagement WHERE doi = ?),
                                   ?, datetime('now'))""",
                        (doi, rec_id, e.openalex_id, e.work_type, e.cited_by_count,
                         json.dumps(e.counts_by_year) if e.counts_by_year else None,
                         before, during, after, e.referenced_works_count,
                         None if e.is_oa is None else int(e.is_oa), ry,
                         1 if e.window_covers else 0,
                         doi, doi,          # for the two carry-forward subqueries
                         e.error))
                    stats["total"] += 1
                    if e.error == "not in openalex":
                        stats["missing"] += 1
                    elif e.error:
                        stats["failed"] += 1
                    if (e.cited_by_count or 0) > 0:
                        stats["cited"] += 1
                    if after > 0:
                        stats["cited_after_retraction"] += 1
                    if ry is None:
                        stats["no_retraction_year"] += 1
                conn.commit()
                n = stats["total"]
                if n % 2000 < engagement.BATCH:
                    rate = n / max(time.time() - started, 1) * 3600
                    console.print(
                        f"[dim]{n:>7,}/{len(rows):,}[/dim] "
                        f"cited {stats['cited']:,}  "
                        f"cited after retraction {stats['cited_after_retraction']:,}  "
                        f"| {rate:,.0f}/h")

        await asyncio.gather(*(do(b) for b in batches))
    return stats


async def collect_wikipedia(conn, dois: list[str], concurrency: int) -> Counter:
    stats: Counter = Counter()
    sem = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()
    async with httpx.AsyncClient(follow_redirects=True) as client:
        async def do(doi: str) -> None:
            async with sem:
                res = await engagement.wikipedia_citations(client, doi)
            async with lock:
                if res is None:
                    # The search never succeeded. Writing 0 here would record
                    # "not on Wikipedia" as a finding about the paper; leaving
                    # the column NULL keeps it in the queue for the next run.
                    stats["failed"] += 1
                    return
                n, pages = res
                conn.execute(
                    "UPDATE engagement SET wikipedia_count=?, wikipedia_pages=? WHERE doi=?",
                    (n, pages, doi))
                conn.commit()
                stats["total"] += 1
                if n:
                    stats["on_wikipedia"] += 1
                if stats["total"] % 500 == 0:
                    console.print(f"[dim]{stats['total']:,}/{len(dois):,}[/dim] "
                                  f"cited on Wikipedia {stats['on_wikipedia']:,}")
        await asyncio.gather(*(do(d) for d in dois))
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=2,
                    help="OpenAlex allows ~10 req/s; 4 was enough to get throttled")
    ap.add_argument("--wikipedia", action="store_true",
                    help="also search Wikipedia (one request per DOI, much slower)")
    ap.add_argument("--redo", action="store_true", help="re-collect already-stored DOIs")
    args = ap.parse_args()

    conn = db.connect()
    db.init(conn)

    if args.wikipedia:
        # Only ask Wikipedia about papers Altmetric already says are cited
        # there. Altmetric gives the count; this pass gives the article titles,
        # which is the part worth having - "the Wikipedia page on X still cites
        # a retracted paper" says something a number cannot. Restricting to
        # known-positive DOIs turns 62,867 searches into a few thousand.
        dois = [r["doi"] for r in conn.execute(
            """SELECT e.doi FROM engagement e
               JOIN altmetric a ON a.doi = e.doi
               WHERE e.wikipedia_count IS NULL
                 AND a.counts_json IS NOT NULL
                 AND json_extract(a.counts_json, '$.cited_by_wikipedia_count') > 0
               ORDER BY json_extract(a.counts_json, '$.cited_by_wikipedia_count') DESC"""
            + (f" LIMIT {int(args.limit)}" if args.limit else ""))]
        if not dois:
            console.print("[green]Wikipedia pass already complete[/green]")
            return 0
        console.print(f"[bold]Wikipedia: {len(dois):,} DOIs[/bold]\n")
        st = asyncio.run(collect_wikipedia(conn, dois, args.concurrency))
        console.print(f"\n  queried {st['total']:,}, of which {st['on_wikipedia']:,} are still cited on Wikipedia")
        conn.close()
        return 0

    rows = targets(conn, args.limit, args.redo)
    if not rows:
        console.print("[green]nothing left to collect[/green]")
        return 0
    console.print(f"[bold]OpenAlex: {len(rows):,} papers[/bold] "
                  f"in {(len(rows) + engagement.BATCH - 1)//engagement.BATCH:,} batched requests\n")

    started = time.time()
    st = asyncio.run(collect_citations(conn, rows, args.concurrency))
    el = time.time() - started
    n = st["total"] or 1

    console.print(f"\n[bold]done in {el/60:.1f} min[/bold]")
    console.print(f"  papers processed        : {st['total']:,}")
    console.print(f"  with at least 1 citation: {st['cited']:,} ({st['cited']/n*100:.0f}%)")
    console.print(f"  [bold]cited AFTER retraction  : {st['cited_after_retraction']:,} "
                  f"({st['cited_after_retraction']/n*100:.0f}%)[/bold]")
    console.print(f"  genuinely not in OpenAlex: {st['missing']:,}")
    console.print(f"  request failed (retry)   : {st['failed']:,}")
    console.print(f"  no usable retraction date: {st['no_retraction_year']:,}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
