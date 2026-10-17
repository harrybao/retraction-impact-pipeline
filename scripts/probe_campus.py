"""Everything that might depend on being on the university network, in one run.

    python scripts/probe_campus.py

Run it once at home to get a baseline, then once on the university wifi and
compare. Anything that flips to 200 was an IP-entitlement problem, not a
licensing one - and several thousand pages hang on that difference.

Why this covers more than Elsevier. SAGE's published policy grants text and
data mining under the standard licence at no extra fee, "provided you only
download articles to which you have legitimate access", and SAGE publishes the
TDM full-text URLs through Crossref - which we already hold for 7,799
unreachable pages. Legitimate access is decided by the requesting IP. Every
one of those links returned 403 when tested from a home connection, which
proves nothing about the licence and everything about where we were sitting.

The same shape applies to Oxford University Press, ACS, RSC and Elsevier. So
this is not a PlumX test with extras; it is the test for the whole
text-and-data-mining route, and it has never been run from an address the
publishers recognise.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import re

import httpx
from rich.console import Console

from rwpipe import config, db, plumx

console = Console()

UA = {"User-Agent": f"COMP5703-CS43 student project (mailto:{config.POLITE_CONTACT})",
      "Accept": "application/json"}

# Hosts whose TDM links are worth testing, and how many pages sit behind each.
TDM_HOSTS = ["journals.sagepub.com", "academic.oup.com", "pubs.acs.org",
             "pubs.rsc.org", "cell.com", "www.tandfonline.com",
             "validate.perfdrive.com", "dl.acm.org"]

PER_HOST = 6          # enough to see a flip, few enough to stay polite

# A refusal that arrives as HTTP 200 is the oldest trap in this project:
# Springer and Nature served captcha pages with a 200 and the first crawler
# filed them as articles. Measured here on 2026-08-31, IOP's TDM links return
# 200 with exactly 14,371 bytes of "Radware Bot Manager Captcha" - a size-only
# check scored that 6/6 and turned a total refusal into a success story.
CHALLENGE = re.compile(
    r"bot manager|captcha|just a moment|checking your browser|"
    r"attention required|client challenge|are you a robot|access denied|"
    r"enable javascript and cookies", re.I)


def verdict(resp) -> str:
    """What a TDM response actually is. Content first, status code last."""
    if resp.status_code != 200:
        return f"http{resp.status_code}"
    body = resp.content[:6000]
    try:
        head = body.decode("utf-8", "ignore")
    except Exception:                                      # noqa: BLE001
        head = ""
    if CHALLENGE.search(head):
        return "challenge"
    if len(resp.content) < 3000:
        return "tiny"
    return "OK"


def where() -> tuple[str, bool]:
    try:
        d = httpx.get("https://ipinfo.io/json", timeout=15).json()
        s = f'{d.get("ip")}  {d.get("org")}'
    except Exception:                                      # noqa: BLE001
        return "unknown", False
    on_campus = any(k in s for k in
                    ("University of Sydney", "Sydney Uni", "AARNet", "AS7575"))
    return s, on_campus


def elsevier(key: str, conn=None, ip: str = "") -> None:
    doi = "10.1016/j.jhep.2014.10.009"
    h = dict(UA); h["X-ELS-APIKey"] = key
    tok = plumx.load_insttoken()
    if tok:
        h["X-ELS-Insttoken"] = tok
    checks = [
        ("PlumX Metrics",   f"https://api.elsevier.com/analytics/plumx/doi/{doi}", {}),
        ("ScienceDirect full text", f"https://api.elsevier.com/content/article/doi/{doi}", {}),
        ("Scopus Search",   "https://api.elsevier.com/content/search/scopus", {"query": f"DOI({doi})"}),
        ("Scopus Abstract", f"https://api.elsevier.com/content/abstract/doi/{doi}", {}),
        ("Author Search",   "https://api.elsevier.com/content/search/author", {"query": "AUTHLAST(Wakefield)"}),
        ("SciVal",          "https://api.elsevier.com/analytics/scival/publication/metrics", {"metricTypes": "CitationCount"}),
    ]
    console.print("[bold]Elsevier APIs[/bold]" + ("  [green]+insttoken[/green]" if tok else ""))
    for name, url, params in checks:
        try:
            r = httpx.get(url, headers=h, params=params, timeout=30)
        except Exception as exc:                           # noqa: BLE001
            console.print(f"  [red]ERR[/red]  {name}: {type(exc).__name__}")
            continue
        colour = "green" if r.status_code == 200 else "red"
        console.print(f"  [{colour}]{r.status_code}[/{colour}]  {name}")
        if conn is not None:
            db.record_probe(conn, "campus:elsevier", name,
                            "ok" if r.status_code == 200 else f"http{r.status_code}",
                            http_status=r.status_code, nbytes=len(r.content),
                            exit_ip=ip)
            conn.commit()


def tdm(conn, ip: str = "") -> None:
    console.print("\n[bold]Publisher TDM links[/bold] "
                  "[dim](from Crossref; entitlement is by IP)[/dim]")
    for host in TDM_HOSTS:
        rows = conn.execute(
            """SELECT a.tdm_links, COUNT(*) OVER () AS _n FROM documents d
               JOIN api_records a ON a.doi = d.doi
               WHERE d.final_host = ? AND d.status IN ('blocked','failed')
                 AND a.tdm_links IS NOT NULL AND a.tdm_links <> ''
               ORDER BY d.shard LIMIT ?""", (host, PER_HOST)).fetchall()
        blocked = conn.execute(
            """SELECT COUNT(*) n FROM documents
               WHERE final_host = ? AND status IN ('blocked','failed')""",
            (host,)).fetchone()["n"]
        if not rows:
            console.print(f"  [dim] --  {host}  (no TDM links recorded)[/dim]")
            continue
        cnt: Counter = Counter()
        for r in rows:
            url = r["tdm_links"].split(";")[0]
            try:
                resp = httpx.get(url, headers={"User-Agent": UA["User-Agent"]},
                                 timeout=30, follow_redirects=True)
            except Exception:                              # noqa: BLE001
                cnt["error"] += 1
                continue
            v = verdict(resp)
            cnt[v] += 1
            db.record_probe(conn, "campus:tdm", host, v, item=url,
                            http_status=resp.status_code, nbytes=len(resp.content),
                            exit_ip=ip)
            conn.commit()
        ok = cnt["OK"]
        colour = "green" if ok else "dim"
        console.print(f"  [{colour}]{ok}/{len(rows)}[/{colour}]  "
                      f"{host:<26} {blocked:>6,} pages blocked   "
                      f"[dim]{dict(cnt)}[/dim]")


def main() -> int:
    loc, on_campus = where()
    console.print(f"[bold]exit IP[/bold]  {loc}")
    console.print("[green]on a university address[/green]" if on_campus else
                  "[yellow]NOT a university address — IP entitlement will not "
                  "apply. This is the baseline run.[/yellow]")
    console.print()

    conn = db.connect()
    db.init(conn)
    # The exit IP is stored with every row: an entitlement result is only
    # meaningful together with where it was asked from, and the whole point of
    # this probe is comparing home against campus.
    elsevier(plumx.load_key(), conn, loc)
    tdm(conn, loc)

    console.print("\n[dim]Run this again on the university wifi. Any line that "
                  "flips to 200 / OK was an IP problem, and the pages behind it "
                  "are recoverable.[/dim]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
