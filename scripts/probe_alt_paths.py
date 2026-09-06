"""Ask the publisher for its own HTML address, instead of guessing one.

    python scripts/probe_alt_paths.py --per-host 3        # sample
    python scripts/probe_alt_paths.py --per-host 3 --apply

`experiment_engine.py` defines ALT_PATHS at line 40 - six publishers' URL
templates - and never references it again: every navigation in that file goes
to `https://doi.org/{doi}`. So the alternate-path experiment was written and
never run, and "we tried alternate paths" has been in the status report on the
strength of code that does not execute.

This runs it, and takes the addresses from the publishers rather than from a
template. Crossref carries two fields nobody here has read:

    resource.primary.URL    where the registrant says the article lives. This
                            is the address doi.org redirects to, but taken
                            directly it skips linkinghub and the other JS
                            interstitials that a redirect chain lands on.
    link[] content-type     the registered full-text locations. Only
                            text/html entries are used here: a PDF or an XML
                            file is not the rendered publisher page the client
                            asked for, and packaging one as MHTML would be
                            dressing up a different artefact.

Deliberately narrow, per the same discipline as every other route:

* one candidate per DOI per attempt, not a shotgun of six;
* doi.org, dx.doi.org and linkinghub.elsevier.com are skipped as candidates -
  they are redirect stops, not destinations;
* a candidate counts only if the page carries its own DOI and is not a
  challenge, which is the rule that caught 10,265 refusal pages filed as
  successes;
* nothing is written to `documents` unless --apply is passed, so a sample can
  be read before it becomes data.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx
from playwright.async_api import async_playwright
from rich.console import Console

from crawl import new_context
from rwpipe import db, fetch, storage

console = Console()

UA = "COMP5703-CS43 student project (mailto:xbao0816@uni.sydney.edu.au)"
CROSSREF = "https://api.crossref.org/works/"
RUN = "alt-paths"

# Redirect stops, never destinations. Landing on one means the hop has not
# finished; offering one as a candidate just re-enters the chain we are trying
# to leave.
SKIP_HOST = {"doi.org", "dx.doi.org", "linkinghub.elsevier.com", "www.doi.org"}


def candidates(meta: dict) -> list[str]:
    """Publisher-registered HTML addresses for this DOI, best first."""
    out: list[str] = []
    primary = ((meta.get("resource") or {}).get("primary") or {}).get("URL")
    if primary:
        out.append(primary)
    for l in meta.get("link") or []:
        if (l.get("content-type") or "").lower() in ("text/html", "unspecified"):
            if l.get("URL"):
                out.append(l["URL"])
    seen, keep = set(), []
    for u in out:
        host = urlsplit(u).netloc.lower()
        if host in SKIP_HOST or u in seen:
            continue
        seen.add(u)
        keep.append(u)
    return keep


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-host", type=int, default=3)
    ap.add_argument("--hosts", type=int, default=12)
    ap.add_argument("--only", default=None,
                    help="restrict to one host, once it has earned a bigger sample")
    ap.add_argument("--apply", action="store_true",
                    help="save successful captures instead of only reporting")
    args = ap.parse_args()

    conn = db.connect()
    db.init(conn)
    if args.only:
        hosts = [args.only]
    else:
        hosts = [r[0] for r in conn.execute(
        """SELECT final_host FROM documents
           WHERE usable IS NOT 1 AND final_host IS NOT NULL
             AND final_host NOT IN ('doi.org','linkinghub.elsevier.com')
           GROUP BY final_host ORDER BY COUNT(*) DESC LIMIT ?""", (args.hosts,))]
    targets = []
    for h in hosts:
        targets += conn.execute(
            """SELECT doi, doi_prefix, final_host FROM documents
               WHERE usable IS NOT 1 AND final_host = ?
               ORDER BY doi LIMIT ?""", (h, args.per_host)).fetchall()
    console.print(f"[bold]{len(targets)} DOIs across {len(hosts)} hosts[/bold]\n")

    tally: Counter = Counter()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await new_context(browser)
        with httpx.Client(timeout=30.0, headers={"User-Agent": UA}) as cl:
            for doi, prefix, host in targets:
                try:
                    m = cl.get(CROSSREF + doi).json()["message"]
                except Exception as exc:                    # noqa: BLE001
                    console.print(f"  {doi[:32]:<32} crossref {type(exc).__name__}")
                    tally["crossref-error"] += 1
                    continue
                cands = candidates(m)
                if not cands:
                    tally["no-html-candidate"] += 1
                    console.print(f"  {doi[:32]:<32} {host[:22]:<22} "
                                  f"[dim]no registered HTML address[/dim]")
                    continue

                url = cands[0]
                page = await ctx.new_page()
                verdict, size = "error", 0
                try:
                    resp = await page.goto(url, wait_until="domcontentloaded",
                                           timeout=45000)
                    await page.wait_for_timeout(2500)
                    html = await page.content()
                    size = len(html)
                    blocked, why = fetch.classify(
                        resp.status if resp else 0, urlsplit(url).netloc,
                        await page.title(), html)
                    if blocked:
                        verdict = (why or "blocked")[:28]
                    elif not fetch.contains_doi(html, doi):
                        verdict = "does not carry its DOI"
                    else:
                        verdict = "ARTICLE"
                        if args.apply:
                            path, raw_n, gz_n = storage.write_capture(
                                doi, prefix, html, "html")
                            conn.execute(
                                """UPDATE documents SET status='done', usable=1,
                                       http_status=?, mhtml_path=?, mhtml_bytes=?,
                                       gz_bytes=?, capture_format='html',
                                       final_url=?, final_host=?, error=NULL,
                                       source_kind='alt-path',
                                       fetched_at=datetime('now')
                                 WHERE doi=?""",
                                (resp.status if resp else None,
                                 storage.rel(path), raw_n, gz_n, url,
                                 urlsplit(url).netloc, doi))
                            conn.commit()
                except Exception as exc:                    # noqa: BLE001
                    verdict = f"error-{type(exc).__name__}"
                finally:
                    await page.close()

                tally[verdict.split(":")[0]] += 1
                db.record_probe(conn, RUN, host, verdict, item=doi, detail=url[:250])
                mark = "[green]" if verdict == "ARTICLE" else "[dim]"
                console.print(f"  {doi[:32]:<32} {host[:22]:<22} "
                              f"{mark}{verdict[:34]}[/]  {size:,}B")
                await asyncio.sleep(1.5)
        await browser.close()
    conn.commit()

    console.print("\n[bold]verdicts[/bold]")
    for k, v in tally.most_common():
        console.print(f"  {k:<34} {v:>4}")
    if not args.apply and tally.get("ARTICLE"):
        console.print("\n[yellow]nothing was saved - re-run with --apply[/yellow]")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
