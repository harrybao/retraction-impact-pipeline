"""Feasibility probe - measure what each publisher actually does to a crawler.

    python scripts/probe.py --per-prefix 3 --top 12

Samples real DOIs from the frontier, resolves them through doi.org with a real
browser, and records the redirect target, bot-wall behaviour, render time and
MHTML size. The output is the evidence base for two proposal numbers we
otherwise have to guess at: total storage, and total crawl wall-clock.

Prefixes are probed concurrently (different hosts), DOIs within one prefix
strictly sequentially with a per-host delay.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playwright.async_api import async_playwright
from rich.console import Console
from rich.table import Table

from rwpipe import config, db
from rwpipe.fetch import fetch_page

console = Console()


def sample_dois(conn, top: int, per_prefix: int) -> dict[str, list[str]]:
    prefixes = [
        r["doi_prefix"]
        for r in conn.execute(
            "SELECT doi_prefix, COUNT(*) n FROM pages "
            "GROUP BY doi_prefix ORDER BY n DESC LIMIT ?", (top,)
        )
    ]
    out = {}
    for p in prefixes:
        # Deterministic pseudo-random sample: reproducible across runs, but
        # spread across the whole prefix instead of clustering on one journal.
        rows = conn.execute(
            "SELECT doi FROM pages WHERE doi_prefix=? "
            "ORDER BY (page_id * 2654435761) % 1000003 LIMIT ?", (p, per_prefix)
        ).fetchall()
        out[p] = [r["doi"] for r in rows]
    return out


async def probe_prefix(browser, prefix: str, dois: list[str], headed_ua: str) -> list[dict]:
    ctx = await browser.new_context(
        user_agent=headed_ua,
        viewport={"width": 1440, "height": 900},
        locale="en-AU",
        ignore_https_errors=True,
    )
    results = []
    try:
        for i, doi in enumerate(dois):
            res, mhtml = await fetch_page(ctx, doi)
            d = res.as_dict()
            d["prefix"] = prefix
            results.append(d)
            host = res.final_host or "?"
            mark = "[green]ok[/green]" if res.ok else (
                "[red]BLOCKED[/red]" if res.blocked else "[yellow]err[/yellow]")
            console.print(
                f"  {prefix:<9} {mark:<20} {host:<34} "
                f"{(res.mhtml_bytes or 0)/1e6:6.2f} MB  {res.elapsed_ms or 0:>6} ms"
            )
            if i < len(dois) - 1:
                delay = config.HOST_DELAY.get(host, config.DEFAULT_HOST_DELAY)
                await asyncio.sleep(delay)
    finally:
        await ctx.close()
    return results


async def run(top: int, per_prefix: int, headless: bool) -> list[dict]:
    conn = db.connect()
    samples = sample_dois(conn, top, per_prefix)
    conn.close()
    console.print(
        f"[bold]probing {len(samples)} DOI prefixes x {per_prefix} samples "
        f"(headless={headless})[/bold]\n"
    )
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            batches = await asyncio.gather(
                *(probe_prefix(browser, p, d, config.USER_AGENT)
                  for p, d in samples.items())
            )
        finally:
            await browser.close()
    return [r for b in batches for r in b]


def summarise(rows: list[dict]) -> None:
    by_prefix: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_prefix[r["prefix"]].append(r)

    table = Table(title="publisher feasibility probe", header_style="bold")
    for col in ("prefix", "resolves to", "ok", "blocked", "err",
                "median MHTML", "median load"):
        table.add_column(col)

    for prefix, rs in sorted(by_prefix.items(), key=lambda kv: -len(kv[1])):
        hosts = {r["final_host"] for r in rs if r["final_host"]}
        ok = [r for r in rs if r["ok"]]
        blocked = [r for r in rs if r["blocked"]]
        errs = [r for r in rs if r["error"]]
        sizes = [r["mhtml_bytes"] for r in ok if r["mhtml_bytes"]]
        times = [r["elapsed_ms"] for r in rs if r["elapsed_ms"]]
        table.add_row(
            prefix,
            ", ".join(sorted(hosts))[:38] or "-",
            f"{len(ok)}/{len(rs)}",
            str(len(blocked)),
            str(len(errs)),
            f"{statistics.median(sizes)/1e6:.2f} MB" if sizes else "-",
            f"{statistics.median(times)/1000:.1f} s" if times else "-",
        )
    console.print(table)

    ok_rows = [r for r in rows if r["ok"] and r["mhtml_bytes"]]
    if ok_rows:
        med = statistics.median(r["mhtml_bytes"] for r in ok_rows)
        console.print(
            f"\n[bold]extrapolation to the full frontier (132,781 pages)[/bold]"
        )
        console.print(f"  median MHTML page      : {med/1e6:.2f} MB")
        console.print(f"  projected raw storage  : {med*132_781/1e12:.2f} TB")
        console.print(f"  projected gzipped (~25%): {med*132_781*0.25/1e12:.2f} TB")
        med_t = statistics.median(r["elapsed_ms"] for r in rows if r["elapsed_ms"])
        secs = (med_t/1000 + config.DEFAULT_HOST_DELAY) * 132_781
        console.print(
            f"  single worker wall-clock: {secs/86400:.0f} days"
            f"  ->  16 parallel workers: {secs/86400/16:.1f} days"
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=12, help="how many DOI prefixes")
    ap.add_argument("--per-prefix", type=int, default=3, help="samples per prefix")
    ap.add_argument("--headed", action="store_true", help="show the browser")
    args = ap.parse_args()

    rows = asyncio.run(run(args.top, args.per_prefix, headless=not args.headed))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = config.REPORTS / f"probe-{stamp}.json"
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    console.print(f"\nraw results -> {out}\n")
    summarise(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
