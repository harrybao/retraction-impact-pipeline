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
from urllib.parse import (parse_qs, parse_qsl, unquote, urlencode, urljoin, urlsplit,
                          urlunsplit)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx
from playwright.async_api import async_playwright
from rich.console import Console

from rwpipe import config, db, fetch, storage

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


# Signs that the crawler's final address is a sign-in or bot-check page, not
# the article: a bot-management or SSO host (fetch._BLOCK_HOST), a challenge in
# the path or query, or no web address at all (chrome-error://chromewebdata/).
CHALLENGE_URL = re.compile(r"captcha|__cf_chl|/authorize\b|/login\b|/signin\b|cookieabsent",
                           re.I)
# The Archive refusing (HTTP 429, 503 "Temporarily Offline"): back off after
# this many lookups in a row fail, and stop after that many, leaving the rest
# unrecorded for the next run rather than hammering it for hours.
BACKOFF_AFTER = 3
STOP_AFTER = 6


def _is_challenge(url: str) -> bool:
    parts = urlsplit(url or "")
    return (parts.scheme not in ("http", "https")
            or bool(fetch._BLOCK_HOST.search(parts.hostname or ""))
            or bool(CHALLENGE_URL.search(parts.path + "?" + parts.query)))


def _is_resolver(url: str) -> bool:
    return (urlsplit(url).hostname or "") in ("doi.org", "dx.doi.org")


MAX_URL = 2000          # a carried value longer than this is not an article address


def _carried_urls(final_url: str, doi: str = "") -> list[str]:
    """Article addresses a challenge URL carries: the URL itself with
    Cloudflare's __cf_chl* tokens removed, and every query value that is an
    absolute http(s) address or a path ("/library_read_article.php?id=1"),
    resolved against the challenge page. Radware (validate.perfdrive.com)
    carries it in `ssc`, Springer's sign-in (idp.springer.com) in
    `redirect_uri`, nbpublish's captcha in a relative `return_url`.

    Best first: an address containing the DOI, then one on the challenge
    page's own site, then the rest - a challenge URL may also carry a referrer
    or a home link, and the first address with a snapshot is the one kept.
    """
    parts = urlsplit(final_url)
    out = []
    if "__cf_chl" in parts.query:
        kept = [(k, v) for k, v in parse_qsl(parts.query) if not k.startswith("__cf_chl")]
        out.append(urlunsplit(parts._replace(query=urlencode(kept))))
    for values in parse_qs(parts.query).values():
        for v in values:
            if v.startswith(("http://", "https://")):
                out.append(v)
            elif v.startswith("/") and not v.startswith("//"):
                out.append(urljoin(final_url, v))
    out = [u for u in dict.fromkeys(out) if len(u) <= MAX_URL and not _is_challenge(u)]
    site = ".".join((parts.hostname or "").split(".")[-2:])

    def rank(u):
        h = urlsplit(u).hostname or ""
        return (0 if doi and doi.lower() in unquote(u).lower() else
                1 if site and h.endswith(site) else 2)
    return sorted(out, key=rank)


def lookup_urls(final_url: str | None, doi: str) -> list[str]:
    """The addresses to ask the Archive about, best first.

    `final_url` is where the crawler ended up. When that is a challenge page,
    the Archive was being asked about the challenge: on 6 October the Springer
    mass notice got "no snapshot" that way (its final address was
    idp.springer.com/authorize?...redirect_uri=<the article>), though the
    article has a snapshot. The article address the challenge carries is asked
    instead, and the DOI resolver last, when nothing better is known.
    """
    if final_url and not _is_challenge(final_url):
        return [final_url]
    carried = _carried_urls(final_url, doi) if final_url else []
    return carried + [f"https://doi.org/{doi}"]


async def cdx_latest(client, url):
    """The latest capture of `url` in the CDX index that was the page itself
    (HTTP 200, HTML) - or, for the DOI resolver, which only ever answers with
    a redirect, the latest redirect, which the Archive follows when replayed.
    Only a CDX table can say there is none; any other answer is an error."""
    filters = (["statuscode:30[0-9]"] if _is_resolver(url)
               else ["statuscode:200", "mimetype:text/html"])
    try:
        r = await client.get("https://web.archive.org/cdx/search/cdx",
                             params={"url": url, "output": "json", "limit": "-1",
                                     "fl": "timestamp,original", "filter": filters},
                             headers=HEADERS, timeout=90)
        if r.status_code != 200:
            return None, "api-error"
        rows = r.json()
    except Exception:                                      # noqa: BLE001
        return None, "api-error"
    if not isinstance(rows, list):
        return None, "api-error"
    if len(rows) < 2:                                      # header row only, or []
        return None, "no-snapshot"
    last = rows[-1]
    if not isinstance(last, list) or len(last) < 2:
        return None, "api-error"
    ts, original = str(last[0]), str(last[1])
    return (f"http://web.archive.org/web/{ts}/{original}", ts, "cdx"), "ok"


async def snapshot_url(client, url):
    """A snapshot as (address, timestamp, which index), or None, with why.
    Distinguishes "no snapshot" from "the Archive did not answer".

    The availability API alone is not enough to write "no snapshot": on
    6-7 October it answered "none" four times for the SAGE 659-paper notice
    and once with its 2 August 2025 snapshot, while the CDX index listed two
    good captures every time. "no-snapshot" is recorded for good, so before
    recording it the CDX index is asked (`cdx_latest`). An availability answer
    whose capture was not a page or a redirect (a 403 or 404 the Archive
    recorded) is passed over the same way.
    """
    try:
        r = await client.get("https://archive.org/wayback/available",
                             params={"url": url}, headers=HEADERS, timeout=30)
        if r.status_code != 200:
            return None, "api-error"
        s = (r.json().get("archived_snapshots") or {}).get("closest") or {}
    except Exception:                                      # noqa: BLE001
        return None, "api-error"
    if s.get("available") and str(s.get("status") or "200")[:1] in ("2", "3"):
        return (s["url"], s.get("timestamp", ""), "available"), "ok"
    await asyncio.sleep(1)
    return await cdx_latest(client, url)


def record(conn, doi, status, url=None, ts=None, path=None, size=None):
    conn.execute(
        """INSERT OR REPLACE INTO wayback_captures
           (doi, snapshot_url, snapshot_date, status, path, bytes, fetched_at)
           VALUES (?,?,?,?,?,?, datetime('now'))""",
        (doi, url, ts, status, path, size))
    conn.commit()


async def render(pg, url):
    """Load a snapshot; (text, landed address, landed timestamp)."""
    await pg.goto(url, timeout=60000, wait_until="domcontentloaded")
    await pg.wait_for_timeout(2500)
    text = await pg.evaluate("document.body.innerText")
    if re.search(r"Got an HTTP 30\d response", text):
        # A redirect capture (the DOI resolver's): the Archive shows a notice,
        # then follows it to the page.
        await pg.wait_for_timeout(8000)
        text = await pg.evaluate("document.body.innerText")
    # The date of the capture actually shown, which after a redirect is not
    # the date that was asked for.
    landed = re.search(r"web\.archive\.org/web/(\d{8,14})", pg.url)
    return text, (pg.url if landed else url), (landed.group(1) if landed else None)


def _thin(text: str) -> bool:
    return len(text) < 2000 or bool(CHALLENGE.search(text[:3000]))


async def run(conn, rows, rate):
    stats = Counter()
    refused = 0                                 # Archive lookups failed in a row
    async with httpx.AsyncClient() as client, async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        pg = await ctx.new_page()
        cdp = await ctx.new_cdp_session(pg)

        for i, r in enumerate(rows, 1):
            if i % 100 == 0:
                console.print(f"[dim]  {i:,}/{len(rows):,}  {dict(stats)}[/dim]")
            urls = lookup_urls(r["final_url"], r["doi"])
            got, why, asked = None, "no-snapshot", None
            for u in urls:
                got, w = await snapshot_url(client, u)
                if got:
                    asked = u
                    break
                if w == "api-error":
                    why = w                 # an outage is never recorded as "none"
                await asyncio.sleep(rate)
            if not got and why == "api-error":
                stats["api-error"] += 1
                refused += 1
                if refused >= STOP_AFTER:
                    console.print(f"  [red]the Archive refused {refused} lookups in a row; "
                                  f"stopping - {len(rows) - i + 1:,} pages are left for the "
                                  f"next run[/red]", soft_wrap=True)
                    break
                if refused >= BACKOFF_AFTER:
                    wait = min(60 * 2 ** (refused - BACKOFF_AFTER), 240)
                    console.print(f"  [yellow]the Archive is refusing ({refused} in a row); "
                                  f"waiting {wait}s[/yellow]")
                    await asyncio.sleep(wait)
                continue
            refused = 0
            if not got:
                # Asked only about the DOI resolver: no address of the page
                # itself was known, which is a different finding from "the
                # Archive holds no copy of the page".
                status = ("no-snapshot" if any(not _is_resolver(u) for u in urls)
                          else "no-snapshot-doi")
                stats[status] += 1
                record(conn, r["doi"], status)
                await asyncio.sleep(rate)
                continue

            url, ts, via = got
            try:
                text, url, landed = await render(pg, url)
                ts = landed or ts
                if _thin(text) and OFFLINE not in text and via == "available":
                    # The closest capture is a bot check or a stub; the CDX
                    # index may still hold a capture of the page itself.
                    alt, _ = await cdx_latest(client, asked)
                    if alt and alt[0] != url:
                        text, url, landed = await render(pg, alt[0])
                        ts = landed or alt[1]
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
            if _thin(text):
                # Recorded, so it is not asked about again every run; the
                # snapshot address is kept for anyone who wants to look.
                stats["thin"] += 1
                record(conn, r["doi"], "thin", url, ts[:8])
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
                # Its own file: the crawler's refusal page stays as evidence.
                path, size, gz = storage.write_capture(
                    r["doi"], r["doi_prefix"], mhtml, "mhtml", tag="wayback")
            except Exception as exc:                       # noqa: BLE001
                stats["write-error"] += 1
                console.print(f"  [red]write failed[/red] {type(exc).__name__} {r['doi']}")
                await asyncio.sleep(rate)
                continue
            record(conn, r["doi"], "ok", url, ts[:8], storage.rel(path), gz)
            stats["ok"] += 1
            console.print(f"  [green]ok[/green] {size:>8,}B  snap {ts[:8]}  {r['doi']}")
            await asyncio.sleep(rate)
        await browser.close()
    unanswered = stats["api-error"] + stats["ia-offline"] + stats["render-error"]
    if unanswered:
        # R looks for this line (recover_wayback) so the step does not read "ok".
        console.print(f"  [red]the Archive did not answer for {unanswered} page(s); "
                      f"they are left for the next run[/red]", soft_wrap=True)
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
