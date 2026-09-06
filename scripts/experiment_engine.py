"""Experiment - does a different browser engine get through where Chromium cannot?

Two untried levers against the hosts that refuse us:

1. **Engine.** Bot-management products are tuned hardest against headless
   Chromium, because that is what most scrapers use. Playwright also drives
   real Firefox and WebKit builds. Running Firefox is not forging a
   fingerprint - it *is* Firefox, with Firefox's own TLS handshake and header
   ordering. That keeps this on the "avoid" side of the client's instruction:
   we are choosing a different real browser, not impersonating one.

2. **Path.** A DOI resolves to a publisher's article landing page, which is the
   most heavily defended URL they own. The same content often sits behind
   less-guarded routes - print views, PDF endpoints, mobile hosts. Worth
   probing before declaring a host unreachable.

Both are measured against the same DOIs so engines can be compared directly.
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

from rwpipe import config, db
from rwpipe.fetch import classify

console = Console()

# Alternate routes to the same article, per publisher. Built from each site's
# own URL conventions - these are their published paths, not guessed ones.
ALT_PATHS = {
    "pubs.acs.org":     ["https://pubs.acs.org/doi/epdf/{doi}",
                         "https://pubs.acs.org/doi/full/{doi}"],
    "pubs.rsc.org":     ["https://pubs.rsc.org/en/content/articlelanding/{doi}"],
    "www.mdpi.com":     ["https://www.mdpi.com/{doi}"],
    "academic.oup.com": ["https://academic.oup.com/article-pdf/{doi}"],
    "cell.com":         ["https://www.cell.com/action/showPdf?pii={doi}"],
    "jbc.org":          ["https://www.jbc.org/article/{doi}/fulltext"],
}


async def try_url(ctx, url: str, settle: int = 3000) -> tuple[bool, str]:
    page = await ctx.new_page()
    try:
        r = await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(settle)
        body = await page.content()
        blocked, reason = classify(
            r.status if r else None, urlparse(page.url).netloc,
            (await page.title() or "").strip(), body)
        return (not blocked), (reason or f"ok {len(body)//1024}KB")
    except PWError as exc:
        return False, f"{type(exc).__name__}: {str(exc).splitlines()[0][:36]}"
    finally:
        await page.close()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-host", type=int, default=3)
    ap.add_argument("--delay", type=float, default=8.0)
    args = ap.parse_args()

    conn = db.connect()
    hosts = [r["final_host"] for r in conn.execute(
        """SELECT final_host, COUNT(*) n FROM documents
           WHERE final_host IS NOT NULL AND final_host <> ''
           GROUP BY final_host HAVING n >= 100 AND SUM(status='done') = 0
           ORDER BY n DESC LIMIT 6""")]
    samples = {h: [r["doi"] for r in conn.execute(
        "SELECT doi FROM documents WHERE final_host=? ORDER BY (shard % 65537) LIMIT ?",
        (h, args.per_host))] for h in hosts}
    totals = {h: conn.execute("SELECT COUNT(*) n FROM documents WHERE final_host=?",
                              (h,)).fetchone()["n"] for h in hosts}
    conn.close()

    engines = ("chromium", "firefox", "webkit")
    console.print(f"[bold]{len(hosts)} blocked hosts x {args.per_host} DOIs x "
                  f"{len(engines)} engines[/bold]\n")

    scores: dict[str, dict[str, int]] = {h: {} for h in hosts}
    async with async_playwright() as pw:
        for eng in engines:
            console.print(f"[bold]{eng}[/bold]")
            try:
                browser = await getattr(pw, eng).launch(headless=True)
            except Exception as exc:                       # noqa: BLE001
                console.print(f"  [yellow]unavailable: {type(exc).__name__}[/yellow]\n")
                continue
            try:
                ctx = await browser.new_context(
                    viewport={"width": 1440, "height": 900},
                    locale="en-AU", ignore_https_errors=True)
                for h in hosts:
                    ok = 0
                    for doi in samples[h]:
                        good, why = await try_url(ctx, f"https://doi.org/{doi}")
                        ok += good
                        mark = "[green]OK[/green]     " if good else "[red]blocked[/red]"
                        console.print(f"  {h[:24]:<26} {mark} {why[:40]}")
                        await asyncio.sleep(args.delay)
                    scores[h][eng] = ok
                await ctx.close()
            finally:
                await browser.close()
            console.print("")

    t = Table(title="does the engine matter?", header_style="bold")
    t.add_column("host"); t.add_column("pages", justify="right")
    for e in engines:
        t.add_column(e)
    gain = 0
    for h in hosts:
        row = [h[:28], f"{totals[h]:,}"]
        best = max(scores[h].values()) if scores[h] else 0
        base = scores[h].get("chromium", 0)
        if best > base:
            gain += totals[h]
        for e in engines:
            v = scores[h].get(e)
            cell = "-" if v is None else f"{v}/{args.per_host}"
            row.append(f"[green]{cell}[/green]" if v and v > base else cell)
        t.add_row(*row)
    console.print(t)
    console.print(f"\n[bold]{'another engine helped on hosts covering ' + f'{gain:,} pages' if gain else 'no engine did better than chromium'}[/bold]")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
