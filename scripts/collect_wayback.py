"""Recover unreachable pages from the Internet Archive, as real MHTML.

    python scripts/collect_wayback.py --probe      # how many have a snapshot
    python scripts/collect_wayback.py --limit 200
    python scripts/collect_wayback.py

Why this route is worth more than its coverage suggests: `web.archive.org` does
not refuse crawlers, so Chromium can render a snapshot and capture it through
the DevTools Protocol. It is the **only** recovery path that produces MHTML -
the format the client actually asked for. PubMed Central gives XML, the
publisher APIs give XML or PDF; this gives the page.

What it is *not*: a snapshot is the publisher's page as it stood on a
particular date, which may predate the retraction. The capture date is stored
alongside the retraction date so any analysis can exclude snapshots taken
before the notice existed. Presenting a pre-retraction snapshot as evidence of
what a reader sees today would be wrong, and the data has to make that
checkable.

Availability was measured at 26% (63 of 240) across the largest blocked hosts,
concentrated in Oxford (76%), ACM (50%) and ACS (43%); cell.com, RSC and
jbc.org have none.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx
from playwright.async_api import async_playwright
from rich.console import Console

from rwpipe import config, db, storage

console = Console()

HEADERS = {"User-Agent": f"COMP5703-CS43 student project (mailto:{config.POLITE_CONTACT})"}
OFFLINE = "Temporarily Offline"
CHALLENGE = re.compile(r"just a moment|checking your browser|captcha|bot manager", re.I)


def targets(conn, limit):
    sql = """SELECT doi, doi_prefix, final_url, final_host FROM documents
             WHERE status IN ('blocked','failed')
               AND final_url IS NOT NULL AND final_url NOT LIKE 'about:%'
               AND doi NOT IN (SELECT doi FROM wayback_captures)
             ORDER BY CASE final_host
                        WHEN 'academic.oup.com'     THEN 1   -- 76% have a snapshot
                        WHEN 'dl.acm.org'           THEN 2   -- 50%
                        WHEN 'pubs.acs.org'         THEN 3   -- 43%
                        WHEN 'www.tandfonline.com'  THEN 4   -- 30%
                        WHEN 'journals.sagepub.com' THEN 5   -- 10%
                        ELSE 9 END, shard"""
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


async def snapshot_url(client, url):
    """Closest snapshot, or None. Distinguishes 'no snapshot' from 'IA down'."""
    try:
        r = await client.get("https://archive.org/wayback/available",
                             params={"url": url}, headers=HEADERS, timeout=30)
        if r.status_code != 200:
            return None, "api-error"
        s = (r.json().get("archived_snapshots") or {}).get("closest") or {}
    except Exception:                                      # noqa: BLE001
        return None, "api-error"
    if not s.get("available"):
        return None, "no-snapshot"
    return (s["url"], s.get("timestamp", "")), "ok"


async def run(conn, rows, rate):
    stats = Counter()
    async with httpx.AsyncClient() as client, async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        pg = await ctx.new_page()
        cdp = await ctx.new_cdp_session(pg)

        for i, r in enumerate(rows, 1):
            got, why = await snapshot_url(client, r["final_url"])
            if not got:
                stats[why] += 1
                if why == "no-snapshot":
                    conn.execute(
                        """INSERT OR REPLACE INTO wayback_captures
                           (doi, snapshot_url, snapshot_date, status, path, bytes, fetched_at)
                           VALUES (?,?,?,?,?,?, datetime('now'))""",
                        (r["doi"], None, None, "no-snapshot", None, None))
                    conn.commit()
                await asyncio.sleep(rate)
                continue

            url, ts = got
            try:
                await pg.goto(url, timeout=60000, wait_until="domcontentloaded")
                await pg.wait_for_timeout(2500)
                text = await pg.evaluate("document.body.innerText")
            except Exception:                              # noqa: BLE001
                stats["render-error"] += 1
                await asyncio.sleep(rate)
                continue

            if OFFLINE in text:
                # The archive is down, not the page missing. Recording this as
                # "no snapshot" would bake an outage into the dataset - the
                # mistake this project has made six times. Leave it unrecorded
                # so the next run retries.
                stats["ia-offline"] += 1
                await asyncio.sleep(rate * 3)
                continue
            if len(text) < 2000 or CHALLENGE.search(text[:3000]):
                stats["thin"] += 1
                await asyncio.sleep(rate)
                continue

            try:
                snap = await cdp.send("Page.captureSnapshot", {"format": "mhtml"})
                # write_capture encodes; handing it bytes raised
                # AttributeError: 'bytes' object has no attribute 'encode',
                # and the call sits outside this try, so the first genuine
                # success would have killed the run rather than skipping one
                # page. The trial run reached zero successes, so the defect
                # stayed invisible - a collector that had never saved anything
                # looked like a collector that worked.
                mhtml = snap["data"]
            except Exception:                              # noqa: BLE001
                stats["mhtml-error"] += 1
                await asyncio.sleep(rate)
                continue

            try:
                path, size, gz = storage.write_capture(
                    r["doi"], r["doi_prefix"], mhtml, "mhtml")
            except Exception as exc:                       # noqa: BLE001
                stats["write-error"] += 1
                console.print(f"  [red]write failed[/red] {type(exc).__name__} {r['doi']}")
                await asyncio.sleep(rate)
                continue
            conn.execute(
                """INSERT OR REPLACE INTO wayback_captures
                   (doi, snapshot_url, snapshot_date, status, path, bytes, fetched_at)
                   VALUES (?,?,?,?,?,?, datetime('now'))""",
                (r["doi"], url, ts[:8], "ok", storage.rel(path), gz))
            conn.commit()
            stats["ok"] += 1
            console.print(f"  [green]ok[/green] {size:>8,}B  snap {ts[:8]}  {r['doi']}")

            if i % 100 == 0:
                console.print(f"[dim]  {i:,}/{len(rows):,}  {dict(stats)}[/dim]")
            await asyncio.sleep(rate)
        await browser.close()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--rate", type=float, default=1.5)
    ap.add_argument("--probe", action="store_true")
    args = ap.parse_args()

    conn = db.connect()
    conn.execute("""CREATE TABLE IF NOT EXISTS wayback_captures (
        doi TEXT PRIMARY KEY, snapshot_url TEXT, snapshot_date TEXT,
        status TEXT, path TEXT, bytes INTEGER, fetched_at TEXT)""")
    conn.commit()

    rows = targets(conn, args.limit)
    if args.probe:
        console.print(f"{len(rows):,} unreachable pages with a URL to look up")
        return 0
    if not rows:
        console.print("[green]nothing left[/green]")
        return 0

    console.print(f"[bold]Wayback: {len(rows):,} pages[/bold] at {args.rate}s apart\n")
    stats = asyncio.run(run(conn, rows, args.rate))
    console.print(f"\n[bold]{dict(stats)}[/bold]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
