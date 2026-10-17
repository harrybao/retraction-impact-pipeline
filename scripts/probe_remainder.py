"""One request per host per route, over the documents no plan covers.

    python scripts/probe_remainder.py

Everything already agreed or in flight reaches 86%. The client's target is 90%,
and the only place the difference can come from - without touching the five
publishers whose controls we have declined to defeat - is the 7,063 documents
that belong to no route: a long tail of 200-odd hosts, most of them small
publishers that were written off by the circuit breaker after a handful of
attempts.

Written off is not the same as unavailable. `www.cellphysiolbiochem.com` and
`www.scielo.br` are both in this remainder AND in the list of hosts measured to
answer a plain HTTP client; 623 e3s-conferences documents are here and every
one of them has an open-access copy. Those are not new discoveries, they are
routes nobody connected to this queue.

Three routes per host, cheapest first, one request each:

    plain-http   the same request without a browser. "Blocked" in this
                 database has often meant blocked while driving Playwright,
                 which is a claim about our client recorded as a claim about
                 the publisher - cureus.com was 518/518 blocked and returned
                 10 of 10 to httpx.
    crossref     the publisher's own registered address, taken from
                 `resource.primary.URL`, instead of the doi.org redirect chain.
                 647 documents in this remainder never got past doi.org at all.
    oa-mirror    a free copy on a host that never refused us.

The point is to measure the yield before spending 7,000 requests on it. A host
that answers goes into a collector; a host that refuses all three is recorded
as refusing all three, which is what "we could not get it" has to mean if it is
going in a report.
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx
from rich.console import Console

from rwpipe import config, db, fetch

console = Console()

UA = f"COMP5703-CS43 student project (mailto:{config.POLITE_CONTACT})"
CROSSREF = "https://api.crossref.org/works/"
RUN = "remainder-routes"

FIVE = {"journals.sagepub.com", "www.tandfonline.com", "dl.acm.org",
        "pubs.rsc.org", "pubs.acs.org"}
CAMPUS = {"academic.oup.com", "portlandpress.com", "pubs.aip.org",
          "aacrjournals.org", "karger.com"}
IOP = {"validate.perfdrive.com", "iopscience.iop.org", "journals.asm.org"}
ELS = {"www.sciencedirect.com", "linkinghub.elsevier.com", "cell.com"}
SKIP_URL_HOST = {"doi.org", "dx.doi.org", "linkinghub.elsevier.com"}


def remainder(conn):
    """Documents that belong to no existing plan, one sample per host."""
    rows = conn.execute("""
        SELECT d.doi, d.final_host, d.error,
               (SELECT o.landing_url FROM oa_all_locations o
                 WHERE o.doi = d.doi AND o.is_oa = 1
                   AND o.host <> COALESCE(d.final_host,'zzz')
                   AND o.host NOT LIKE '%doi.org' LIMIT 1) AS oa_url
        FROM documents d
        WHERE d.usable IS NOT 1
          AND COALESCE(d.error,'') NOT LIKE 'soft refusal%'
          AND COALESCE(d.error,'') NOT LIKE 'pmc-no-fulltext%'
    """).fetchall()
    by_host = defaultdict(list)
    for doi, host, err, oa in rows:
        h = host or "(unresolved)"
        if h in FIVE or h in CAMPUS or h in IOP or h in ELS:
            continue
        by_host[h].append((doi, oa))
    return by_host


def judge(body: str, doi: str, code: int) -> str:
    if code != 200:
        return f"http-{code}"
    low = body[:6000].lower()
    if "just a moment" in low or "cf-chl" in low or "captcha" in low:
        return "challenge"
    if not fetch.contains_doi(body, doi):
        return "no-doi"
    if len(body) < 8000:
        return "tiny"
    return "OK"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hosts", type=int, default=60)
    ap.add_argument("--rate", type=float, default=0.7)
    args = ap.parse_args()

    conn = db.connect()
    db.init(conn)
    by_host = remainder(conn)
    ranked = sorted(by_host.items(), key=lambda kv: -len(kv[1]))[:args.hosts]
    covered = sum(len(v) for _, v in ranked)
    total = sum(len(v) for v in by_host.values())
    console.print(f"[bold]{len(by_host)} hosts, {total:,} documents with no "
                  f"route[/bold]")
    console.print(f"probing the top {len(ranked)}, covering {covered:,} "
                  f"({covered/max(total,1)*100:.0f}%)\n")

    wins: Counter = Counter()
    recoverable = 0
    with httpx.Client(timeout=40.0, follow_redirects=True,
                      headers={"User-Agent": UA,
                               "Accept": "text/html,application/xhtml+xml"}) as cl:
        for host, items in ranked:
            doi, oa_url = items[0]
            results = {}

            # 1. plain HTTP through the resolver
            try:
                r = cl.get(f"https://doi.org/{doi}")
                results["plain-http"] = judge(r.text, doi, r.status_code)
            except Exception as exc:                        # noqa: BLE001
                results["plain-http"] = f"err-{type(exc).__name__}"
            time.sleep(args.rate)

            # 2. the publisher's own registered address
            if results["plain-http"] != "OK":
                try:
                    # Crossref answers JSON, and the client-wide
                    # "Accept: text/html" header made it answer something else
                    # - which arrived as JSONDecodeError on all 26 hosts and
                    # would have been read as "the route does not work". It was
                    # a header we set ourselves.
                    m = cl.get(CROSSREF + doi,
                               headers={"Accept": "application/json"}
                               ).json()["message"]
                    url = ((m.get("resource") or {}).get("primary") or {}).get("URL")
                    if url and urlsplit(url).netloc.lower() not in SKIP_URL_HOST:
                        r = cl.get(url)
                        results["crossref-url"] = judge(r.text, doi, r.status_code)
                    else:
                        results["crossref-url"] = "no-address"
                except Exception as exc:                    # noqa: BLE001
                    results["crossref-url"] = f"err-{type(exc).__name__}"
                time.sleep(args.rate)

            # 3. a free copy somewhere that never refused us
            if "OK" not in results.values() and oa_url:
                try:
                    r = cl.get(oa_url)
                    results["oa-mirror"] = judge(r.text, doi, r.status_code)
                except Exception as exc:                    # noqa: BLE001
                    results["oa-mirror"] = f"err-{type(exc).__name__}"
                time.sleep(args.rate)

            won = next((k for k, v in results.items() if v == "OK"), None)
            if won:
                wins[won] += 1
                recoverable += len(items)
            for route, verdict in results.items():
                db.record_probe(conn, RUN, host, verdict, item=doi, detail=route)
            conn.commit()

            mark = f"[green]{won}[/green]" if won else "[dim]none[/dim]"
            detail = "  ".join(f"{k}={v}" for k, v in results.items())
            console.print(f"  {host[:30]:<31}{len(items):>5} docs  {mark:<22} {detail}")

    console.print(f"\n[bold]{recoverable:,} of {covered:,} documents sit behind a "
                  f"host that answered at least one route[/bold]")
    for route, n in wins.most_common():
        console.print(f"   {route:<14} opened {n} hosts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
