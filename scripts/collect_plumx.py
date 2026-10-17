"""Stage 1b - PlumX metrics, the second source the client named.

    python scripts/collect_plumx.py --probe          # check the key first
    python scripts/collect_plumx.py --limit 200      # sample
    python scripts/collect_plumx.py                  # the full corpus

Needs an Elsevier API key in .env as ELSEVIER_API_KEY (https://dev.elsevier.com/).

Always --probe first. A key can authenticate for one Elsevier API and still be
refused on this one, and finding that out on request 1 rather than request
40,000 is the difference between a minute and a wasted evening.

Resumable: a DOI already in the plumx table is skipped.
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

from rwpipe import db, plumx

console = Console()


def targets(conn, limit: int | None) -> list[str]:
    """Most-cited first, so an interrupted run still covers the papers that
    matter most to the analysis."""
    # Errored rows are retried: recording a failure must not make it final.
    # But a failure that is not going to change must not be retried forever
    # either, or the run can never say it is finished. `10.1016/j.rdf.2012.
    # 12.001` returns HTTP 406 from this endpoint on every attempt, with any
    # Accept header, correctly encoded - the service chokes on the ".rdf."
    # segment of the path. It was re-queued by every run and kept the
    # "nothing left to collect" branch unreachable. A row is parked only after
    # failing the *same way twice* (see run()), so a transient error still
    # gets its retry and a code fix that changes the outcome clears the park.
    sql = """SELECT e.doi FROM engagement e
             LEFT JOIN plumx p ON p.doi = e.doi
             WHERE p.doi IS NULL
                OR (p.error IS NOT NULL AND p.error <> ''
                    AND p.error NOT LIKE 'permanent:%')
             ORDER BY e.cited_by_count DESC"""
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [r["doi"] for r in conn.execute(sql)]


def notice_targets(conn, limit: int | None) -> list[tuple[str, int]]:
    """Retraction-notice DOIs still to ask PlumX about, with how many papers
    each retracts.

    The same notice set, cleaned the same way, as
    scripts/collect_altmetric_notices.py - so the two sources the client named
    are asked about exactly the same DOIs and their answers can be combined
    paper by paper. A notice whose DOI is the paper's own is skipped: `plumx`
    already holds that answer. Errors are retried, except one that has failed
    the same way twice.
    """
    sql = """
        WITH notice AS (
            SELECT lower(trim(r.retraction_doi)) AS doi,
                   COUNT(DISTINCT lower(trim(r.original_doi))) AS n_papers
            FROM records r
            WHERE r.retraction_doi IS NOT NULL
              AND trim(r.retraction_doi) <> ''
              AND lower(trim(r.retraction_doi))
                  <> lower(trim(COALESCE(r.original_doi, '')))
            GROUP BY 1
        )
        SELECT n.doi, n.n_papers
        FROM notice n
        LEFT JOIN plumx_notice p ON p.retraction_doi = n.doi
        WHERE p.retraction_doi IS NULL
           OR (p.error IS NOT NULL AND p.error <> ''
               AND p.error NOT LIKE 'permanent:%')
        ORDER BY n.n_papers DESC, n.doi
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [(r["doi"], r["n_papers"]) for r in conn.execute(sql)]


def save(conn, table: str, doi: str, n_papers: int | None, m, err) -> None:
    """One answer, into `plumx` (a paper) or `plumx_notice` (a notice)."""
    vals = (int(m.found), m.plumx_id, m.usage, m.captures, m.mentions, m.social,
            m.totals.get("citation", 0),
            json.dumps(m.totals) if m.totals else None,
            json.dumps(m.detail) if m.detail else None, err)
    if table == "plumx_notice":
        conn.execute(
            """INSERT OR REPLACE INTO plumx_notice
               (retraction_doi, n_papers, found, plumx_id, usage_count,
                capture_count, mention_count, social_count, citation_count,
                totals_json, detail_json, error, fetched_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'))""",
            (doi, n_papers) + vals)
    else:
        conn.execute(
            """INSERT OR REPLACE INTO plumx
               (doi, found, plumx_id, usage_count, capture_count,
                mention_count, social_count, citation_count,
                totals_json, detail_json, error, fetched_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?, datetime('now'))""",
            (doi,) + vals)


async def run(conn, dois: list[str], key: str, rate: float,
              table: str = "plumx", n_papers: dict[str, int] | None = None) -> Counter:
    stats: Counter = Counter()
    started = time.time()
    keycol = "retraction_doi" if table == "plumx_notice" else "doi"
    n_papers = n_papers or {}

    async with httpx.AsyncClient(follow_redirects=True) as client:
        for doi in dois:
            try:
                m = await plumx.fetch(client, doi, key)
            except (plumx.QuotaExhausted, plumx.NotEntitled) as exc:
                console.print(f"\n[red]stopping: {exc}[/red]")
                console.print("[yellow]progress is saved; re-run when resolved."
                              "[/yellow]")
                break

            # Second identical failure means the answer is not going to change.
            err = m.error
            if err:
                prev = conn.execute(f"SELECT error FROM {table} WHERE {keycol} = ?",
                                    (doi,)).fetchone()
                if prev and prev[0] == err:
                    err = f"permanent: {err}"

            save(conn, table, doi, n_papers.get(doi), m, err)
            conn.commit()

            stats["total"] += 1
            if m.error:
                stats["error"] += 1
            elif not m.found:
                stats["no_record"] += 1
            else:
                stats["found"] += 1
                if m.usage:
                    stats["with_usage"] += 1
                if m.captures:
                    stats["with_capture"] += 1
                if m.mentions:
                    stats["with_mention"] += 1

            n = stats["total"]
            if n % 250 == 0:
                el = max(time.time() - started, 1)
                console.print(
                    f"[dim]{n:>7,}/{len(dois):,}[/dim] "
                    f"with a record {stats['found']:,}  "
                    f"with downloads/views {stats['with_usage']:,}  "
                    f"with captures {stats['with_capture']:,}  "
                    f"no record {stats['no_record']:,}  "
                    f"| {n/el*3600:,.0f}/h")
            await asyncio.sleep(rate)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--rate", type=float, default=0.5,
                    help="seconds between requests")
    ap.add_argument("--probe", action="store_true",
                    help="check the key against one DOI and exit")
    ap.add_argument("--notices", action="store_true",
                    help="ask about retraction-notice DOIs (into plumx_notice) "
                         "instead of article DOIs")
    args = ap.parse_args()

    key = plumx.load_key()

    if args.probe:
        ok, msg = plumx.probe(key)
        console.print(("[green]OK[/green] " if ok else "[red]BLOCKED[/red] ") + msg)
        return 0 if ok else 1

    conn = db.connect()
    db.init(conn)

    if args.notices:
        items = notice_targets(conn, args.limit)
        dois = [d for d, _ in items]
        n_papers = dict(items)
        table, what = "plumx_notice", "retraction-notice DOIs"
    else:
        dois = targets(conn, args.limit)
        n_papers = None
        table, what = "plumx", "DOIs"
    if not dois:
        console.print("[green]nothing left to collect[/green]")
        return 0

    hours = len(dois) * args.rate / 3600
    console.print(f"[bold]PlumX: {len(dois):,} {what}[/bold] at {args.rate}s "
                  f"apart — about {hours:.1f} hours\n")

    stats = asyncio.run(run(conn, dois, key, args.rate, table=table, n_papers=n_papers))

    n = stats["total"] or 1
    console.print(f"\n[bold]processed {stats['total']:,}[/bold]")
    console.print(f"  in PlumX             : {stats['found']:,} ({stats['found']/n*100:.0f}%)")
    console.print(f"  [bold]with usage (views/downloads): {stats['with_usage']:,} "
                  f"({stats['with_usage']/n*100:.0f}%)[/bold]")
    console.print(f"  saved to a library   : {stats['with_capture']:,} "
                  f"({stats['with_capture']/n*100:.0f}%)")
    console.print(f"  news/blog mentions   : {stats['with_mention']:,} "
                  f"({stats['with_mention']/n*100:.0f}%)")
    console.print(f"  no record            : {stats['no_record']:,} "
                  f"({stats['no_record']/n*100:.0f}%)")
    console.print(f"  errors               : {stats['error']:,}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
