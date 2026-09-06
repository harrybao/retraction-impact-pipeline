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
    sql = """SELECT e.doi FROM engagement e
             LEFT JOIN plumx p ON p.doi = e.doi
             WHERE p.doi IS NULL OR (p.error IS NOT NULL AND p.error <> '')
             ORDER BY e.cited_by_count DESC"""
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [r["doi"] for r in conn.execute(sql)]


async def run(conn, dois: list[str], key: str, rate: float) -> Counter:
    stats: Counter = Counter()
    started = time.time()

    async with httpx.AsyncClient(follow_redirects=True) as client:
        for doi in dois:
            try:
                m = await plumx.fetch(client, doi, key)
            except (plumx.QuotaExhausted, plumx.NotEntitled) as exc:
                console.print(f"\n[red]stopping: {exc}[/red]")
                console.print("[yellow]progress is saved; re-run when resolved."
                              "[/yellow]")
                break

            conn.execute(
                """INSERT OR REPLACE INTO plumx
                   (doi, found, plumx_id, usage_count, capture_count,
                    mention_count, social_count, citation_count,
                    totals_json, detail_json, error, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?, datetime('now'))""",
                (doi, int(m.found), m.plumx_id, m.usage, m.captures,
                 m.mentions, m.social, m.totals.get("citation", 0),
                 json.dumps(m.totals) if m.totals else None,
                 json.dumps(m.detail) if m.detail else None, m.error))
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
                    f"有记录 {stats['found']:,}  "
                    f"有下载/浏览 {stats['with_usage']:,}  "
                    f"被收藏 {stats['with_capture']:,}  "
                    f"无记录 {stats['no_record']:,}  "
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
    args = ap.parse_args()

    key = plumx.load_key()

    if args.probe:
        ok, msg = plumx.probe(key)
        console.print(("[green]OK[/green] " if ok else "[red]BLOCKED[/red] ") + msg)
        return 0 if ok else 1

    conn = db.connect()
    db.init(conn)

    dois = targets(conn, args.limit)
    if not dois:
        console.print("[green]nothing left to collect[/green]")
        return 0

    hours = len(dois) * args.rate / 3600
    console.print(f"[bold]PlumX: {len(dois):,} DOIs[/bold] at {args.rate}s "
                  f"apart — about {hours:.1f} hours\n")

    stats = asyncio.run(run(conn, dois, key, args.rate))

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
