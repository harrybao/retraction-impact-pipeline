"""What does this Elsevier key actually reach, and from where?

    python scripts/probe_elsevier.py

Run it twice - once from home, once on the university wifi - and diff the two.
Elsevier resolves institutional entitlement from the requesting IP, so the
difference between the two runs *is* the answer to "does the University's
subscription cover this API".

Why a matrix rather than one endpoint: measured from home on 2026-08-30, this
key returned 200 from Scopus Search but 401 from Author Search - the same API
family. A single 200 therefore proves nothing about entitlement; it can be an
open tier. Reading one endpoint's success as proof of a subscription is exactly
the mistake this script exists to stop.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx
from rich.console import Console

from rwpipe import plumx

console = Console()
DOI = "10.1016/j.jhep.2014.10.009"

CHECKS = [
    ("PlumX Metrics",        f"https://api.elsevier.com/analytics/plumx/doi/{DOI}", {}),
    ("ScienceDirect full text", f"https://api.elsevier.com/content/article/doi/{DOI}", {}),
    ("ScienceDirect Search", "https://api.elsevier.com/content/search/sciencedirect", {"query": "retraction"}),
    ("Scopus Search",        "https://api.elsevier.com/content/search/scopus", {"query": f"DOI({DOI})"}),
    ("Scopus Abstract",      f"https://api.elsevier.com/content/abstract/doi/{DOI}", {}),
    ("Scopus Citations",     "https://api.elsevier.com/content/abstract/citations", {"doi": DOI}),
    ("Author Search",        "https://api.elsevier.com/content/search/author", {"query": "AUTHLAST(Wakefield)"}),
    ("SciVal",               "https://api.elsevier.com/analytics/scival/publication/metrics", {"metricTypes": "CitationCount"}),
]


def main() -> int:
    key = plumx.load_key()
    hdr = {"X-ELS-APIKey": key, "Accept": "application/json",
           "User-Agent": "COMP5703-CS43 student project"}
    tok = plumx.load_insttoken()
    if tok:
        hdr["X-ELS-Insttoken"] = tok

    try:
        org = httpx.get("https://ipinfo.io/json", timeout=15).json()
        where = f"{org.get('ip')}  {org.get('org')}"
    except Exception:                                      # noqa: BLE001
        where = "unknown"

    console.print(f"[bold]exit IP[/bold]  {where}")
    # Never echo any part of a credential, not even a fragment. A key
    # tail is enough to confirm a guess and appears in logs, screenshots
    # and pasted terminal output.
    console.print("[bold]key[/bold]      loaded from .env"
                  + ("  [green]+ insttoken[/green]" if tok else ""))
    campus = any(s in where for s in ("Sydney Uni", "University of Sydney", "AARNet"))
    console.print("[green]on a university address[/green]" if campus else
                  "[yellow]NOT a university address - entitlement by IP will not "
                  "apply[/yellow]")
    console.print()

    ok = 0
    for name, url, params in CHECKS:
        try:
            r = httpx.get(url, headers=hdr, params=params, timeout=30)
        except Exception as exc:                           # noqa: BLE001
            console.print(f"  [red]ERR[/red]  {name}: {type(exc).__name__}")
            continue
        if r.status_code == 200:
            ok += 1
            console.print(f"  [green]200[/green]  {name}")
            continue
        try:
            msg = r.json()["service-error"]["status"].get("statusText", "")
        except Exception:                                  # noqa: BLE001
            msg = r.text[:80].replace("\n", " ")
        console.print(f"  [red]{r.status_code}[/red]  {name}")
        console.print(f"       [dim]{msg[:88]}[/dim]")

    console.print(f"\n[bold]{ok}/{len(CHECKS)} reachable[/bold]")
    if not campus:
        console.print("[yellow]Re-run this on the university wifi and compare. "
                      "Any endpoint that flips to 200 was an IP problem, not a "
                      "licensing one.[/yellow]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
