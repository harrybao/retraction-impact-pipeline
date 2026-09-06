"""Experiment - can the hard-blocked publishers be reached at all?

    python scripts/experiment_stealth.py

Six publishers currently return zero successes (~5,636 pages, 5.1% of the
frontier). Before writing them off, test the three levers never yet tried, all
against the same DOIs so the comparison is fair:

  A  headless-shell        the current default, and the known-failing baseline
  B  new-headless+stealth  Chromium's modern headless, plus JS patches that
                           remove the obvious automation tells
  C  headed+stealth        a real visible browser window

The distinction that matters: if A fails and C succeeds, we are being detected
as automation (fixable). If all three fail identically, the site refuses
non-human traffic outright and no amount of pacing will change it - which is a
finding to report, not a problem to keep grinding at.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playwright.async_api import async_playwright
from rich.console import Console
from rich.table import Table

from rwpipe import config, db
from rwpipe.fetch import classify

console = Console()

TARGETS = {
    "IOP":  "10.1088",
    "RSC":  "10.1039",
    "MDPI": "10.3390",
    "ACS":  "10.1021",
    "JAMA": "10.1001",
    "APA":  "10.1037",
}
PER_PUBLISHER = 2

# The tells a site checks first. navigator.webdriver alone is a one-line giveaway.
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins',
    {get: () => [1, 2, 3, 4, 5].map(i => ({name: 'Plugin ' + i}))});
Object.defineProperty(navigator, 'languages', {get: () => ['en-AU', 'en']});
Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
window.chrome = {runtime: {}, app: {}, csi: () => {}, loadTimes: () => {}};
const _q = navigator.permissions && navigator.permissions.query;
if (_q) navigator.permissions.query = p => p && p.name === 'notifications'
    ? Promise.resolve({state: Notification.permission}) : _q.call(navigator.permissions, p);
const _gp = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function (p) {
    if (p === 37445) return 'Intel Inc.';
    if (p === 37446) return 'Intel(R) Iris(TM) Graphics 6100';
    return _gp.call(this, p);
};
"""


def sample_dois(conn) -> dict[str, list[str]]:
    out = {}
    for name, prefix in TARGETS.items():
        rows = conn.execute(
            "SELECT doi FROM documents WHERE doi_prefix=? "
            "ORDER BY (shard % 9973) LIMIT ?", (prefix, PER_PUBLISHER)).fetchall()
        out[name] = [r["doi"] for r in rows]
    return out


async def try_one(ctx, doi: str) -> tuple[bool, str]:
    page = await ctx.new_page()
    try:
        resp = await page.goto(f"https://doi.org/{doi}",
                               wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(3500)
        body = await page.content()
        from urllib.parse import urlparse
        blocked, reason = classify(
            resp.status if resp else None,
            urlparse(page.url).netloc,
            (await page.title() or "").strip(),
            body,
        )
        return (not blocked), (reason or f"ok {len(body) // 1024}KB")
    except Exception as exc:                       # noqa: BLE001
        return False, f"{type(exc).__name__}"
    finally:
        await page.close()


async def run_variant(pw, label: str, samples: dict[str, list[str]],
                      *, headless: bool, stealth: bool, channel: str | None):
    # No channel= here: that resolves to a *system-installed* browser, which
    # this machine does not have. Playwright's own builds are used instead -
    # headless=True gives the headless shell, headless=False the full Chromium.
    kwargs = {"headless": headless,
              "args": ["--disable-blink-features=AutomationControlled"]}
    browser = await pw.chromium.launch(**kwargs)
    results: dict[str, list[tuple[bool, str]]] = {}
    try:
        ctx = await browser.new_context(
            user_agent=config.USER_AGENT,
            viewport={"width": 1440, "height": 900},
            locale="en-AU", ignore_https_errors=True)
        if stealth:
            await ctx.add_init_script(STEALTH_JS)
        for name, dois in samples.items():
            results[name] = []
            for doi in dois:
                ok, why = await try_one(ctx, doi)
                results[name].append((ok, why))
                console.print(f"  [dim]{label:<22}[/dim] {name:<5} "
                              f"{'[green]OK[/green]  ' if ok else '[red]blocked[/red]'} {why[:52]}")
                await asyncio.sleep(6)
        await ctx.close()
    finally:
        await browser.close()
    return results


async def main() -> int:
    conn = db.connect()
    samples = sample_dois(conn)
    conn.close()
    n = sum(len(v) for v in samples.values())
    console.print(f"[bold]testing {n} DOIs across {len(samples)} blocked "
                  f"publishers, 3 browser variants[/bold]\n")

    variants = [
        ("A headless-baseline", dict(headless=True,  stealth=False, channel=None)),
        ("B headless+stealth",  dict(headless=True,  stealth=True,  channel=None)),
        ("C headed+stealth",    dict(headless=False, stealth=True,  channel=None)),
    ]
    all_results = {}
    async with async_playwright() as pw:
        for label, kw in variants:
            console.print(f"[bold]{label}[/bold]")
            try:
                all_results[label] = await run_variant(pw, label, samples, **kw)
            except Exception as exc:                # noqa: BLE001
                console.print(f"  [yellow]variant unavailable: "
                              f"{type(exc).__name__}: {str(exc)[:90]}[/yellow]")
                all_results[label] = {}
            console.print("")

    table = Table(title="can the blocked publishers be reached?", header_style="bold")
    table.add_column("publisher")
    table.add_column("pages", justify="right")
    for label, _ in variants:
        table.add_column(label.split()[0] + " " + label.split()[1][:14])
    conn = db.connect()
    for name, prefix in TARGETS.items():
        pages = conn.execute("SELECT COUNT(*) n FROM documents WHERE doi_prefix=?",
                             (prefix,)).fetchone()["n"]
        row = [name, f"{pages:,}"]
        for label, _ in variants:
            rs = all_results.get(label, {}).get(name, [])
            row.append(f"{sum(1 for ok, _ in rs if ok)}/{len(rs)}" if rs else "-")
        table.add_row(*row)
    conn.close()
    console.print(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
