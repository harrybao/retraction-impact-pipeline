"""Ask Altmetric about the RETRACTION NOTICES, not just the papers.

    python scripts/collect_altmetric_notices.py --limit 300   # sample first
    python scripts/collect_altmetric_notices.py               # all 47,021

The client's doubt that only 2,513 papers have news was half right. The
2,513 is not a collection failure - the summary and detail endpoints agree, no
request is unresolved, and 4.00% is the rate the literature reports for indexed
papers generally (Fang et al. 2020, Table 3). But it is not the whole scope
either, and the part that is missing is the part the client was pointing at.

A newsroom covering a retraction links the NOTICE. Zheng et al. 2025 found 79%
of post-retraction news mentions of highly-mentioned articles were mentions of
the notice rather than the paper, and they queried both identifiers. This
project only ever queried original DOIs: of 47,074 distinct notice DOIs, 53 had
been asked about and 47,021 had not. That is an identifier-scope gap, not a
parsing bug, and it cannot be argued away - it has to be collected.

Two rules this encodes:

**Notices go in their own table.** `altmetric` is keyed on original DOIs and
every figure taken from it is per paper. Writing notices into it would change
what that denominator means without anyone noticing.

**A notice is not its papers.** Ten notices retract more than a hundred papers
each, the largest 659. One article about a mass retraction is one article;
copying it onto 659 papers would manufacture coverage. `n_papers` is stored so
the merge back to paper level can say so, and so a mass notice can be reported
separately rather than averaged in.

Like the paper collector: the client's own API key, a deliberately gentle rate,
a hard stop the moment the service starts refusing, and resumable - a notice
already recorded without an error is skipped.
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

from rwpipe import altmetric, db

console = Console()


def targets(conn, limit: int | None, order: str = "papers") -> list[tuple[str, int]]:
    """Notice DOIs still to ask about, with how many papers each retracts.

    A notice whose DOI is the paper's own DOI is skipped: there is nothing
    separate to ask about, and `altmetric` already holds the answer. A row that
    errored is retried, for the same reason the paper collector retries them -
    recording a failure must not make it permanent.
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
        LEFT JOIN altmetric_notice a ON a.retraction_doi = n.doi
        WHERE a.retraction_doi IS NULL
           OR (a.error IS NOT NULL AND a.error <> '')
    """
    # Biggest notices first: they cover the most papers, so if a run is cut
    # short the part that was collected is the part that matters most.
    sql += " ORDER BY n.n_papers DESC, n.doi" if order == "papers" else " ORDER BY n.doi"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [(r["doi"], r["n_papers"]) for r in conn.execute(sql)]


async def run(conn, items: list[tuple[str, int]], key: str, rate: float) -> Counter:
    stats: Counter = Counter()
    started = time.time()

    async with httpx.AsyncClient(follow_redirects=True) as client:
        for doi, n_papers in items:
            try:
                a = await altmetric.fetch(client, doi, key)
            except altmetric.QuotaExhausted as exc:
                console.print(f"\n[red]service is refusing requests: {exc}[/red]")
                console.print("[yellow]stopping to protect the client's key. "
                              "Re-run later; progress is saved.[/yellow]")
                break

            conn.execute(
                """INSERT OR REPLACE INTO altmetric_notice
                   (retraction_doi, n_papers, found, altmetric_id, score,
                    news_count, social_count, counts_json, details_url,
                    added_on, error, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?, datetime('now'))""",
                (doi, n_papers, int(a.found), a.altmetric_id, a.score, a.news,
                 a.social, json.dumps(a.counts) if a.counts else None,
                 a.details_url, a.added_on, a.error))
            conn.commit()

            stats["total"] += 1
            if a.error:
                stats["error"] += 1
            elif not a.found:
                stats["no_attention"] += 1
            else:
                stats["with_attention"] += 1
                if a.news:
                    stats["with_news"] += 1
                    stats["papers_behind_news"] += n_papers
                if a.social:
                    stats["with_social"] += 1

            n = stats["total"]
            if n % 250 == 0:
                el = max(time.time() - started, 1)
                left = (len(items) - n) * rate / 3600
                console.print(
                    f"[dim]{n:>7,}/{len(items):,}[/dim] "
                    f"attention {stats['with_attention']:,}  "
                    f"news {stats['with_news']:,}  "
                    f"social {stats['with_social']:,}  "
                    f"no record {stats['no_attention']:,}  "
                    f"| {n/el*3600:,.0f}/h, {left:.1f}h left")
            await asyncio.sleep(rate)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--rate", type=float, default=1.0,
                    help="seconds between requests; this is the client's key, "
                         "so the default is deliberately gentle")
    ap.add_argument("--order", choices=("papers", "doi"), default="papers",
                    help="papers = biggest notices first, so a short run still "
                         "covers the most papers")
    args = ap.parse_args()

    key = altmetric.load_key()
    conn = db.connect()
    db.init(conn)

    items = targets(conn, args.limit, args.order)
    if not items:
        console.print("[green]nothing left to collect[/green]")
        return 0

    papers = sum(n for _, n in items)
    hours = len(items) * args.rate / 3600
    console.print(f"[bold]Altmetric, retraction notices: {len(items):,} DOIs[/bold] "
                  f"covering {papers:,} papers, at {args.rate}s apart "
                  f"- about {hours:.1f} hours\n")

    stats = asyncio.run(run(conn, items, key, args.rate))

    n = stats["total"] or 1
    console.print(f"\n[bold]processed {stats['total']:,} notices[/bold]")
    console.print(f"  with any attention : {stats['with_attention']:,} "
                  f"({stats['with_attention']/n*100:.1f}%)")
    console.print(f"  [bold]covered by news    : {stats['with_news']:,} "
                  f"({stats['with_news']/n*100:.1f}%)[/bold]")
    console.print(f"  shared socially    : {stats['with_social']:,} "
                  f"({stats['with_social']/n*100:.1f}%)")
    console.print(f"  no record at all   : {stats['no_attention']:,} "
                  f"({stats['no_attention']/n*100:.1f}%)")
    console.print(f"  errors             : {stats['error']:,}")
    console.print(f"\n  papers behind a notice with news: "
                  f"{stats['papers_behind_news']:,}")
    console.print("[dim]  that is papers whose notice was covered, not pieces of "
                  "coverage: one article about a mass retraction is one "
                  "article.[/dim]")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
