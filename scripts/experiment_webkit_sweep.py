"""Which of the blocked hosts does WebKit actually reach?

WebKit cracked MDPI completely (18/18 where Chromium got 0 of 1,036) but that
was four hosts tested, not forty. This sweeps every host the crawler cannot
reach, so the decision about whether to build a WebKit path is made against
the whole blocked set rather than one lucky publisher.

Sample size is chosen to separate "no better" from "clearly better" rather
than to estimate a rate precisely: 8 attempts distinguishes 0% from >50%
comfortably, and any host that looks promising gets re-measured properly
before a single page is requeued. An earlier requeue in this project was
justified by 2 successes out of 6, which turned out to be noise.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playwright.async_api import async_playwright, Error as PWError
from rich.console import Console
from rich.table import Table

from rwpipe import db
from rwpipe.fetch import classify

console = Console()


async def probe(ctx, doi: str) -> tuple[bool, int]:
    page = await ctx.new_page()
    try:
        r = await page.goto(f"https://doi.org/{doi}",
                            wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(3000)
        body = await page.content()
        blocked, _ = classify(r.status if r else None,
                              urlparse(page.url).netloc,
                              (await page.title() or "").strip(), body)
        return (not blocked), len(body) // 1024
    except PWError:
        return False, 0
    finally:
        await page.close()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-host", type=int, default=8)
    ap.add_argument("--hosts", type=int, default=18)
    ap.add_argument("--delay", type=float, default=3.0)
    args = ap.parse_args()

    conn = db.connect()
    rows = conn.execute(
        """SELECT final_host, COUNT(*) n, SUM(status='done') ok FROM documents
           WHERE final_host IS NOT NULL AND final_host <> ''
           GROUP BY final_host
           HAVING n >= 150 AND (SUM(status='done') * 1.0 / COUNT(*)) < 0.05
           ORDER BY n DESC LIMIT ?""", (args.hosts,)).fetchall()
    targets = [(r["final_host"], r["n"], r["ok"] or 0) for r in rows]
    samples = {h: [x["doi"] for x in conn.execute(
        "SELECT doi FROM documents WHERE final_host=? ORDER BY (shard % 49999) LIMIT ?",
        (h, args.per_host))] for h, _, _ in targets}
    conn.close()

    console.print(f"[bold]sweeping {len(targets)} chromium-blocked hosts "
                  f"x {args.per_host} DOIs with WebKit[/bold]\n")

    results: dict[str, tuple[int, int]] = {}
    async with async_playwright() as pw:
        browser = await pw.webkit.launch(headless=True)
        try:
            ctx = await browser.new_context(
                viewport={"width": 1440, "height": 900},
                locale="en-AU", ignore_https_errors=True)
            for host, total, _ in targets:
                dois = samples[host]
                if not dois:
                    continue
                ok = 0
                sizes = []
                for d in dois:
                    good, kb = await probe(ctx, d)
                    ok += good
                    if good:
                        sizes.append(kb)
                    await asyncio.sleep(args.delay)
                med = sorted(sizes)[len(sizes) // 2] if sizes else 0
                results[host] = (ok, med)
                console.print(f"  {host[:30]:<32} {ok}/{len(dois)}"
                              + (f"   median {med} KB" if med else ""))
            await ctx.close()
        finally:
            await browser.close()

    t = Table(title="WebKit against chromium-blocked hosts", header_style="bold")
    for col in ("host", "pages", "chromium", "webkit", "median size"):
        t.add_column(col)
    recoverable = 0
    for host, total, chrome_ok in targets:
        if host not in results:
            continue
        ok, med = results[host]
        promising = ok >= max(2, len(samples[host]) // 2)
        if promising:
            recoverable += total
        t.add_row(host[:30], f"{total:,}",
                  f"{chrome_ok}/{total:,}",
                  f"[green]{ok}/{len(samples[host])}[/green]" if promising
                  else f"{ok}/{len(samples[host])}",
                  f"{med} KB" if med else "-")
    console.print(t)
    console.print(f"\n[bold]hosts where WebKit looks clearly better cover "
                  f"{recoverable:,} pages[/bold]")
    console.print("[dim]promising hosts still need a larger sample before "
                  "anything is requeued[/dim]")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
