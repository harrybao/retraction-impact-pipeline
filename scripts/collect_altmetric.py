"""Stage 1b - news and social media attention, via Altmetric.

    python scripts/collect_altmetric.py --limit 200      # sample first
    python scripts/collect_altmetric.py                  # the full corpus

Uses the API key the client supplied, so it runs deliberately slowly and stops
outright the moment the service starts refusing us. Exhausting someone else's
quota is not a recoverable mistake, and a stalled collection is easier to fix
than a revoked key.

Resumable: a DOI already in the altmetric table is skipped, so an interrupted
run continues where it stopped.
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


def targets(conn, limit: int | None) -> list[str]:
    # A row that errored is not a row that was answered. The original query
    # asked only for DOIs absent from the table, so anything written with an
    # error - a timeout, a bad response - was skipped for good: the failure
    # became permanent by being recorded. Retry those too.
    sql = """SELECT e.doi FROM engagement e
             LEFT JOIN altmetric a ON a.doi = e.doi
             WHERE a.doi IS NULL OR (a.error IS NOT NULL AND a.error <> '')
             ORDER BY e.cited_by_count DESC"""
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [r["doi"] for r in conn.execute(sql)]


async def run(conn, dois: list[str], key: str, rate: float) -> Counter:
    stats: Counter = Counter()
    started = time.time()
    stopped = False

    async with httpx.AsyncClient(follow_redirects=True) as client:
        for doi in dois:
            if stopped:
                break
            try:
                a = await altmetric.fetch(client, doi, key)
            except altmetric.QuotaExhausted as exc:
                console.print(f"\n[red]service is refusing requests: {exc}[/red]")
                console.print("[yellow]stopping to protect the client's key. "
                              "Re-run later; progress is saved.[/yellow]")
                stopped = True
                break

            conn.execute(
                """INSERT OR REPLACE INTO altmetric
                   (doi, found, altmetric_id, score, news_count, social_count,
                    counts_json, details_url, added_on, error, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?, datetime('now'))""",
                (doi, int(a.found), a.altmetric_id, a.score, a.news, a.social,
                 json.dumps(a.counts) if a.counts else None,
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
                if a.social:
                    stats["with_social"] += 1

            n = stats["total"]
            if n % 250 == 0:
                el = max(time.time() - started, 1)
                console.print(
                    f"[dim]{n:>7,}/{len(dois):,}[/dim] "
                    f"with attention {stats['with_attention']:,}  "
                    f"news {stats['with_news']:,}  "
                    f"social {stats['with_social']:,}  "
                    f"no record {stats['no_attention']:,}  "
                    f"| {n/el*3600:,.0f}/h")
            await asyncio.sleep(rate)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--rate", type=float, default=1.0,
                    help="seconds between requests; this is the client's key, "
                         "so the default is deliberately gentle")
    args = ap.parse_args()

    key = altmetric.load_key()
    conn = db.connect()
    db.init(conn)

    dois = targets(conn, args.limit)
    if not dois:
        console.print("[green]nothing left to collect[/green]")
        return 0

    hours = len(dois) * args.rate / 3600
    console.print(f"[bold]Altmetric: {len(dois):,} DOIs[/bold] at "
                  f"{args.rate}s apart — about {hours:.1f} hours\n")

    stats = asyncio.run(run(conn, dois, key, args.rate))

    n = stats["total"] or 1
    console.print(f"\n[bold]processed {stats['total']:,}[/bold]")
    console.print(f"  with any attention : {stats['with_attention']:,} "
                  f"({stats['with_attention']/n*100:.0f}%)")
    console.print(f"  [bold]covered by news    : {stats['with_news']:,} "
                  f"({stats['with_news']/n*100:.0f}%)[/bold]")
    console.print(f"  shared socially    : {stats['with_social']:,} "
                  f"({stats['with_social']/n*100:.0f}%)")
    console.print(f"  no record at all   : {stats['no_attention']:,} "
                  f"({stats['no_attention']/n*100:.0f}%)")
    console.print(f"  errors             : {stats['error']:,}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
