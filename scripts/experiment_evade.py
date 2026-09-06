"""Experiment - can any blocked host be reached by behaving less like a bot?

The client's instruction was "find smarter ways to avoid captchas". Avoid, not
bypass: nothing here solves a challenge or forges a fingerprint. It changes how
we arrive.

Two conditions on the same DOIs so the comparison is fair:

  A  current      what the crawler does now: fresh context, straight to the
                  article URL, snapshot once the page settles
  B  warmed       arrive the way a reader does - land on the publisher's home
                  page first, accept the cookie banner, keep the session, pause,
                  then follow to the article, scroll, and only then snapshot

Condition B tests one specific hypothesis: that some walls fire on *arrival
pattern* rather than on the client itself. A request for a deep article URL
with no prior session, no cookies and no referrer is a shape human traffic
almost never has. If B succeeds where A fails, several thousand pages written
off as unreachable are recoverable.

Also re-tests hosts whose verdict was formed before the rate-limiting fix,
when we were hitting some publishers five times faster than intended. Those
may have been provoked rather than hostile.
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

# Cookie/consent buttons, in the wording publishers actually use.
CONSENT = [
    "button:has-text('Accept all')", "button:has-text('Accept All')",
    "button:has-text('Accept cookies')", "button:has-text('I Accept')",
    "button:has-text('Accept')", "button:has-text('Agree')",
    "button:has-text('Continue')", "#onetrust-accept-btn-handler",
    "[aria-label='Accept cookies']", ".cc-allow",
]


async def dismiss_consent(page) -> bool:
    for sel in CONSENT:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=1200):
                await el.click(timeout=2500)
                await page.wait_for_timeout(800)
                return True
        except (PWError, Exception):        # noqa: BLE001
            continue
    return False


async def probe(ctx, doi: str, *, warmed: bool, host: str) -> tuple[bool, str]:
    page = await ctx.new_page()
    try:
        if warmed:
            # Arrive at the front door first: pick up cookies and a session the
            # way a reader would, so the article request is not the first thing
            # this client has ever asked the site for.
            try:
                await page.goto(f"https://{host}/", wait_until="domcontentloaded",
                                timeout=30_000)
                await dismiss_consent(page)
                await page.wait_for_timeout(2500)
            except PWError:
                pass

        r = await page.goto(f"https://doi.org/{doi}",
                            wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(3000)

        if warmed:
            await dismiss_consent(page)
            # a reader scrolls before reading; an instant snapshot does not
            for y in (400, 1200, 2000):
                try:
                    await page.mouse.wheel(0, y)
                    await page.wait_for_timeout(700)
                except PWError:
                    break
            await page.wait_for_timeout(2000)

        body = await page.content()
        title = (await page.title() or "").strip()
        blocked, reason = classify(r.status if r else None,
                                   urlparse(page.url).netloc, title, body)
        return (not blocked), (reason or f"ok {len(body)//1024}KB")
    except PWError as exc:
        return False, f"{type(exc).__name__}: {str(exc).splitlines()[0][:44]}"
    finally:
        await page.close()


async def run_host(browser, host: str, dois: list[str], delay: float) -> dict:
    out: dict[str, list[tuple[bool, str]]] = {"A current": [], "B warmed": []}
    for label, warmed in (("A current", False), ("B warmed", True)):
        ctx = await browser.new_context(
            user_agent=config.USER_AGENT,
            viewport={"width": 1440, "height": 900},
            locale="en-AU", ignore_https_errors=True)
        try:
            for doi in dois:
                ok, why = await probe(ctx, doi, warmed=warmed, host=host)
                out[label].append((ok, why))
                mark = "[green]OK[/green]     " if ok else "[red]blocked[/red]"
                console.print(f"  {host[:26]:<28} {label:<10} {mark} {why[:44]}")
                await asyncio.sleep(delay)
        finally:
            await ctx.close()
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-host", type=int, default=3)
    ap.add_argument("--hosts", type=int, default=6)
    ap.add_argument("--delay", type=float, default=20.0,
                    help="seconds between requests - deliberately slow")
    args = ap.parse_args()

    conn = db.connect()
    hosts = [r["final_host"] for r in conn.execute(
        """SELECT final_host, COUNT(*) n FROM documents
           WHERE final_host IS NOT NULL AND final_host <> ''
           GROUP BY final_host HAVING n >= 40 AND SUM(status='done') = 0
           ORDER BY n DESC LIMIT ?""", (args.hosts,))]
    samples = {}
    for h in hosts:
        samples[h] = [r["doi"] for r in conn.execute(
            """SELECT doi FROM documents WHERE final_host = ?
               ORDER BY (shard % 7919) LIMIT ?""", (h, args.per_host))]
    totals = {h: conn.execute(
        "SELECT COUNT(*) n FROM documents WHERE final_host=?", (h,)).fetchone()["n"]
        for h in hosts}
    conn.close()

    console.print(f"[bold]{len(hosts)} blocked hosts x {args.per_host} DOIs x "
                  f"2 conditions, {args.delay:.0f}s apart[/bold]\n")

    results = {}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled"])
        try:
            for h in hosts:
                if samples[h]:
                    results[h] = await run_host(browser, h, samples[h], args.delay)
                    console.print("")
        finally:
            await browser.close()

    t = Table(title="does arriving differently help?", header_style="bold")
    for col in ("host", "pages", "A current", "B warmed"):
        t.add_column(col)
    recovered = 0
    for h, r in results.items():
        a = sum(1 for ok, _ in r["A current"] if ok)
        b = sum(1 for ok, _ in r["B warmed"] if ok)
        if b > a:
            recovered += totals[h]
        t.add_row(h[:30], f"{totals[h]:,}",
                  f"{a}/{len(r['A current'])}",
                  f"[green]{b}/{len(r['B warmed'])}[/green]" if b > a
                  else f"{b}/{len(r['B warmed'])}")
    console.print(t)
    if recovered:
        console.print(f"\n[bold green]warming helped on hosts covering "
                      f"{recovered:,} pages[/bold green]")
    else:
        console.print("\n[yellow]warming made no difference on any host[/yellow]")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
