"""Stage 1a-fallback - recover what the official APIs can give for pages the
crawler cannot reach.

    python scripts/enrich.py                 # every blocked/failed page
    python scripts/enrich.py --limit 200     # a sample first
    python scripts/enrich.py --all           # every document, not just failures

Runs happily alongside the crawler: it touches Crossref, Europe PMC and
OpenAlex, none of which are publisher hosts, so it competes with nothing and
cannot make the crawler's rate limiting worse.

Resumable - a DOI already in api_records is skipped.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx
from rich.console import Console

from rwpipe import apis, db

console = Console()

COLUMNS = ("doi", "sources", "title", "publisher", "journal", "issued",
           "work_type", "abstract", "abstract_source", "reference_count",
           "update_types", "related_dois", "is_oa", "oa_url", "pmid", "pmcid",
           "tdm_links", "error")


def to_row(e: apis.Enrichment) -> tuple:
    return (e.doi, ";".join(e.sources) or None, e.title, e.publisher, e.journal,
            e.issued, e.work_type, e.abstract, e.abstract_source,
            e.reference_count, e.update_types, e.related_dois,
            None if e.is_oa is None else int(e.is_oa), e.oa_url, e.pmid,
            e.pmcid, e.tdm_links, e.error)


def pending_dois(conn, only_failures: bool, limit: int | None) -> list[str]:
    where = ("d.status IN ('blocked','failed')" if only_failures else "1=1")
    # `a.doi IS NULL` alone permanently skips any DOI whose first attempt
    # errored - the row exists, so it never comes back, and a transient
    # network failure is preserved as a final answer. 376 DOIs were in that
    # state. Rows carrying an error are retried; rows carrying a real result
    # (including "not found anywhere") are not.
    sql = f"""SELECT d.doi FROM documents d
              LEFT JOIN api_records a ON a.doi = d.doi
              WHERE {where}
                AND (a.doi IS NULL OR (a.error IS NOT NULL AND a.error <> ''))
              ORDER BY d.shard"""
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [r["doi"] for r in conn.execute(sql)]


async def run(dois: list[str], concurrency: int, conn) -> Counter:
    stats: Counter = Counter()
    sem = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()
    started = time.time()

    async with httpx.AsyncClient(http2=False, follow_redirects=True) as client:
        async def one(doi: str) -> None:
            async with sem:
                e = await apis.enrich(client, doi)
            async with lock:
                conn.execute(
                    f"INSERT OR REPLACE INTO api_records ({','.join(COLUMNS)},fetched_at) "
                    f"VALUES ({','.join('?' * len(COLUMNS))}, datetime('now'))",
                    to_row(e))
                conn.commit()
                stats["total"] += 1
                if e.error:
                    stats["no_record"] += 1
                if e.abstract:
                    stats["with_abstract"] += 1
                if e.update_types:
                    stats["with_retraction_link"] += 1
                if e.tdm_links:
                    stats["with_tdm_link"] += 1
                if e.is_oa:
                    stats["open_access"] += 1
                n = stats["total"]
                if n % 100 == 0:
                    rate = n / max(time.time() - started, 1) * 3600
                    console.print(
                        f"[dim]{n:>6,}/{len(dois):,}[/dim] "
                        f"摘要 {stats['with_abstract']:,}  "
                        f"撤稿关系 {stats['with_retraction_link']:,}  "
                        f"无记录 {stats['no_record']:,}  "
                        f"| {rate:,.0f}/h")

        # a modest fixed pool - these are public services doing us a favour
        await asyncio.gather(*(one(d) for d in dois))
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--all", action="store_true",
                    help="enrich every document, not only the unreachable ones")
    ap.add_argument("--concurrency", type=int, default=6)
    args = ap.parse_args()

    conn = db.connect()
    db.init(conn)
    dois = pending_dois(conn, only_failures=not args.all, limit=args.limit)
    if not dois:
        console.print("[green]nothing left to enrich[/green]")
        return 0

    console.print(f"[bold]enriching {len(dois):,} DOIs[/bold] "
                  f"via Crossref + Europe PMC + OpenAlex "
                  f"(concurrency {args.concurrency})\n")
    started = time.time()
    stats = asyncio.run(run(dois, args.concurrency, conn))
    el = time.time() - started

    n = stats["total"] or 1
    console.print(f"\n[bold]done in {el/60:.1f} min[/bold]")
    console.print(f"  DOIs processed        : {stats['total']:,}")
    console.print(f"  with an abstract      : {stats['with_abstract']:,} "
                  f"({stats['with_abstract']/n*100:.0f}%)")
    console.print(f"  with retraction link  : {stats['with_retraction_link']:,} "
                  f"({stats['with_retraction_link']/n*100:.0f}%)")
    console.print(f"  open access           : {stats['open_access']:,}")
    console.print(f"  publisher TDM link    : {stats['with_tdm_link']:,}")
    console.print(f"  no record anywhere    : {stats['no_record']:,}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
