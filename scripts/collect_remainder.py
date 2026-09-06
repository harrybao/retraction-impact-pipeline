"""Collect the long tail, from the hosts that answered a probe.

    python scripts/collect_remainder.py --dry-run
    python scripts/collect_remainder.py

443 documents across 14 small publishers. Not much against a 30,000-document
gap, and worth doing anyway: they are free, they need nobody's permission, and
they are the only part of the remainder that opened at all.

The route per host is not guessed - it is read from `probe_results`, where
`probe_remainder.py` recorded which of plain HTTP, the publisher's own
Crossref-registered address, or an open-access mirror actually returned the
article. A host that answered one route is collected through that route and no
other.

What this run says about the rest of the gap matters more than the 443. Of 116
hosts probed, 14 answered: the pattern is that hosts holding hundreds of
documents refuse every route, and hosts holding a dozen open. There is no large
recoverable population hiding in the tail, and after this run the remainder can
be described as measured rather than as untried.

Identity is checked as everywhere else: a capture counts only if it carries the
DOI it is filed under.
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

from rwpipe import db, fetch, storage

console = Console()

UA = "COMP5703-CS43 student project (mailto:xbao0816@uni.sydney.edu.au)"
CROSSREF = "https://api.crossref.org/works/"
PROBE_RUN = "remainder-routes"
MIN_BYTES = 8_000          # the probe's own floor for "this is an article"


def routes(conn) -> dict[str, str]:
    """host -> the route that answered, from the probe rather than a guess."""
    best: dict[str, str] = {}
    for host, verdict, route in conn.execute(
            "SELECT target, verdict, detail FROM probe_results WHERE run = ?",
            (PROBE_RUN,)):
        if verdict == "OK" and host not in best:
            best[host] = route
    return best


def url_for(cl, doi: str, route: str, oa_url: str | None) -> str | None:
    if route == "plain-http":
        return f"https://doi.org/{doi}"
    if route == "oa-mirror":
        return oa_url
    if route == "crossref-url":
        try:
            m = cl.get(CROSSREF + doi,
                       headers={"Accept": "application/json"}).json()["message"]
        except Exception:                                  # noqa: BLE001
            return None
        u = ((m.get("resource") or {}).get("primary") or {}).get("URL")
        if u and urlsplit(u).netloc.lower() not in ("doi.org", "dx.doi.org"):
            return u
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=float, default=1.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = db.connect()
    db.init(conn)
    open_hosts = routes(conn)
    if not open_hosts:
        console.print("[yellow]no probed routes - run probe_remainder.py first"
                      "[/yellow]")
        return 1

    rows = conn.execute("""
        SELECT d.doi, d.doi_prefix, d.final_host,
               (SELECT o.landing_url FROM oa_all_locations o
                 WHERE o.doi = d.doi AND o.is_oa = 1 LIMIT 1) AS oa_url
        FROM documents d
        WHERE d.usable IS NOT 1 AND d.final_host IN (%s)
        ORDER BY d.final_host, d.doi
    """ % ",".join("?" * len(open_hosts)), tuple(open_hosts)).fetchall()

    console.print(f"[bold]{len(rows):,} documents across {len(open_hosts)} hosts"
                  f"[/bold] that answered a probe\n")
    if args.dry_run:
        for h, n in Counter(r[2] for r in rows).most_common():
            console.print(f"   {h[:38]:<39}{n:>5}  via {open_hosts[h]}")
        return 0

    stats: Counter = Counter()
    per_host: dict = defaultdict(Counter)
    with httpx.Client(timeout=45.0, follow_redirects=True,
                      headers={"User-Agent": UA}) as cl:
        for n, (doi, prefix, host, oa_url) in enumerate(rows, 1):
            route = open_hosts[host]
            url = url_for(cl, doi, route, oa_url)
            if not url:
                stats["no-address"] += 1
                per_host[host]["no-address"] += 1
                continue
            try:
                r = cl.get(url)
                body = r.text
            except Exception as exc:                        # noqa: BLE001
                stats["error"] += 1
                per_host[host]["error"] += 1
                conn.execute("UPDATE documents SET error=?, attempts=attempts+1 "
                             "WHERE doi=?", (f"tail: {type(exc).__name__}", doi))
                time.sleep(2)
                continue

            if r.status_code != 200:
                stats[f"http-{r.status_code}"] += 1
                per_host[host][f"http-{r.status_code}"] += 1
                conn.execute("UPDATE documents SET error=?, attempts=attempts+1 "
                             "WHERE doi=?", (f"tail: http {r.status_code}", doi))
            elif not fetch.contains_doi(body, doi):
                stats["no-doi"] += 1
                per_host[host]["no-doi"] += 1
                conn.execute("UPDATE documents SET error=?, attempts=attempts+1 "
                             "WHERE doi=?", ("tail: does not carry its DOI", doi))
            elif len(body.encode("utf-8", "ignore")) < MIN_BYTES:
                stats["thin"] += 1
                per_host[host]["thin"] += 1
                conn.execute("UPDATE documents SET error=?, attempts=attempts+1 "
                             "WHERE doi=?", ("tail: too small to be an article", doi))
            else:
                path, raw_n, gz_n = storage.write_capture(doi, prefix, body, "html")
                conn.execute(
                    """UPDATE documents
                          SET status='done', usable=1, http_status=200,
                              mhtml_path=?, mhtml_bytes=?, gz_bytes=?,
                              capture_format='html', source_kind=?,
                              final_url=?, final_host=?, error=NULL,
                              attempts=attempts+1, fetched_at=datetime('now')
                        WHERE doi=?""",
                    (storage.rel(path), raw_n, gz_n, f"tail-{route}",
                     url, urlsplit(url).netloc, doi))
                stats["saved"] += 1
                per_host[host]["saved"] += 1

            if n % 20 == 0:
                conn.commit()
                console.print(f"  [dim]{n:>5}/{len(rows):,}[/dim]  "
                              f"saved {stats['saved']:,}")
            time.sleep(args.rate)
    conn.commit()

    console.print(f"\n[bold]{stats['saved']:,} saved[/bold] of {len(rows):,}")
    for k, v in stats.most_common():
        if k != "saved":
            console.print(f"   {k:<14} {v:,}")
    console.print("\n[bold]by host[/bold]")
    for h, c in sorted(per_host.items(), key=lambda kv: -kv[1]["saved"]):
        console.print(f"   {h[:36]:<37} saved {c['saved']:>4}  "
                      f"{dict((k, v) for k, v in c.items() if k != 'saved')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
