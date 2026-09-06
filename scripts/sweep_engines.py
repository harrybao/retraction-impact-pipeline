"""Re-test the blocked hosts with a sample large enough to trust.

    python scripts/sweep_engines.py --per-host 40
    python scripts/sweep_engines.py --per-host 40 --hosts cell.com pubs.rsc.org

Why this exists. The first WebKit sweep tried **8 DOIs per host** and wrote off
eleven hosts covering 10,788 pages on a 1-in-8 result. That is exactly the
evidence standard this project later disavowed, after a 2-of-6 sample was read
as a 33% success rate and 3,366 pages were requeued on it - those hosts then
failed 152 consecutive attempts. The rule adopted from that mistake is: no
success-rate claim from fewer than several dozen attempts. The original sweep
predates the rule and has never been redone under it.

So the verdicts on those hosts are not evidence, they are guesses with a
number attached. This re-runs them properly.

Politeness: these hosts already refuse us. Requests are serial, one host at a
time, with a real delay - a sweep that provokes a harder block would cost more
than it could ever discover.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playwright.async_api import async_playwright
from rich.console import Console

from rwpipe import db

console = Console()

CHALLENGE = re.compile(
    r"just a moment|checking your browser|are you a robot|recaptcha|"
    r"access denied|client challenge|attention required|verify you are human",
    re.I)

# A retraction notice is legitimately short - some are two paragraphs. Judging
# capture by length alone marks them as failures, which is how a page we
# actually need gets filed as a block.
NOTICE = re.compile(r"^\s*(retraction|retracted|withdrawal|withdrawn|"
                    r"expression of concern|correction|corrigendum|erratum)",
                    re.I)


def targets(conn, host: str, n: int) -> list[str]:
    return [r["doi"] for r in conn.execute(
        """SELECT doi FROM documents
           WHERE final_host = ? AND status IN ('blocked','failed')
           ORDER BY shard LIMIT ?""", (host, n))]


def blocked_hosts(conn, limit: int) -> list[tuple[str, int]]:
    return [(r["final_host"], r["n"]) for r in conn.execute(
        """SELECT final_host, COUNT(*) n FROM documents
           WHERE status IN ('blocked','failed') AND final_host IS NOT NULL
             AND final_host NOT IN ('doi.org','linkinghub.elsevier.com')
           GROUP BY 1 ORDER BY n DESC LIMIT ?""", (limit,))]


async def probe(pg, doi: str) -> tuple[str, int]:
    """One page. Returns (verdict, visible characters)."""
    try:
        resp = await pg.goto(f"https://doi.org/{doi}", timeout=45000,
                             wait_until="domcontentloaded")
    except Exception as exc:                                # noqa: BLE001
        return f"error:{type(exc).__name__}", 0
    try:
        await pg.wait_for_timeout(2000)
        title = await pg.title()
        text = await pg.evaluate("document.body.innerText")
        head = (await pg.content())[:4000]
    except Exception:                                       # noqa: BLE001
        return "error:read", 0

    if CHALLENGE.search(title) or CHALLENGE.search(head):
        return "challenge", len(text)
    if resp and resp.status >= 400:
        return f"http{resp.status}", len(text)
    if NOTICE.match(title) and len(text) > 600:
        return "notice", len(text)          # short but real, and the one we want
    if len(text) > 3000:
        return "content", len(text)
    return "thin", len(text)


async def sweep(hosts, per_host: int, engine: str, delay: float):
    conn = db.connect()
    db.init(conn)
    run = f"sweep:{engine}"
    results = {}
    async with async_playwright() as p:
        browser = await getattr(p, engine).launch(headless=True)
        for host, total in hosts:
            dois = targets(conn, host, per_host)
            if not dois:
                continue
            ctx = await browser.new_context()
            pg = await ctx.new_page()
            cnt: Counter = Counter()
            started = time.time()
            for doi in dois:
                verdict, chars = await probe(pg, doi)
                cnt[verdict.split(":")[0]] += 1
                # One row per attempt. The rate printed below is a summary of
                # these, and a summary nobody can re-derive is not evidence.
                db.record_probe(conn, run, host, verdict.split(":")[0],
                                item=doi, nbytes=chars, detail=verdict)
                conn.commit()
                await asyncio.sleep(delay)
            await ctx.close()

            got = cnt["content"] + cnt["notice"]
            pct = 100.0 * got / len(dois)
            results[host] = (got, len(dois), pct, total, dict(cnt))
            mark = "[green]" if pct >= 20 else "[dim]"
            console.print(
                f"{mark}{pct:>5.0f}%[/] {got:>3}/{len(dois):<3} "
                f"({total:>6,} pages blocked)  {host}   "
                f"[dim]{dict(cnt)}  {time.time()-started:.0f}s[/dim]")
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-host", type=int, default=40)
    ap.add_argument("--engine", default="webkit",
                    choices=["webkit", "firefox", "chromium"])
    ap.add_argument("--hosts", nargs="*", default=None)
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--delay", type=float, default=2.0)
    args = ap.parse_args()

    conn = db.connect()
    if args.hosts:
        hosts = [(h, 0) for h in args.hosts]
    else:
        hosts = blocked_hosts(conn, args.top)

    console.print(f"[bold]{args.engine}[/bold] vs {len(hosts)} blocked hosts, "
                  f"{args.per_host} pages each, {args.delay}s apart\n")

    results = asyncio.run(sweep(hosts, args.per_host, args.engine, args.delay))

    worth = [(h, v) for h, v in results.items() if v[2] >= 20]
    console.print(f"\n[bold]worth pursuing ({len(worth)} hosts)[/bold]")
    recover = 0
    for h, (got, n, pct, total, _) in sorted(worth, key=lambda x: -x[1][3]):
        est = int(total * pct / 100)
        recover += est
        console.print(f"  {pct:>5.0f}%  {total:>6,} blocked  -> ~{est:,} recoverable  {h}")
    console.print(f"\n[bold]~{recover:,} pages[/bold] on this engine")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
