"""Give the skipped documents the host they were always going to have.

    python scripts/backfill_hosts.py --limit 50   # sample first
    python scripts/backfill_hosts.py

2,258 refused documents carry no `final_host` at all. The circuit breaker
stopped them before a single request was made - `attempts = 0` - so nothing
ever recorded where they would have gone, and the missing-pages register files
them under "(never resolved)" as though the DOI were broken.

They are not broken. Twenty sampled on 2026-09-04 all resolved on the first
try: fourteen to journals.sagepub.com, six to www.tandfonline.com. So the
largest single line in the register - larger than any real publisher - is two
publishers that are already counted separately, hidden behind a blank field.
That understates SAGE and Taylor & Francis and inflates the mystery.

The resolution is done with `follow_redirects=False`, so the only server asked
is doi.org, which exists to answer exactly this. The publishers themselves are
not touched: this fixes a label, and there is no reason to spend 2,258 requests
on hosts we are in the middle of negotiating with to do it.

`status` and `error` are left alone. A document that was skipped is still
skipped; it just now says by whom.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx
from rich.console import Console

from rwpipe import config, db

console = Console()

UA = f"COMP5703-CS43 student project (mailto:{config.POLITE_CONTACT})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--rate", type=float, default=0.25, help="seconds between")
    args = ap.parse_args()

    conn = db.connect()
    db.init(conn)
    sql = """SELECT doi FROM documents
             WHERE usable IS NOT 1 AND (final_host IS NULL OR final_host = '')
             ORDER BY doi"""
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    dois = [r[0] for r in conn.execute(sql)]
    if not dois:
        console.print("[green]every refused document already names a host[/green]")
        return 0

    console.print(f"[bold]resolving {len(dois):,} DOIs through doi.org[/bold] "
                  f"(redirects not followed - no publisher is contacted)\n")

    hosts: dict[str, int] = {}
    failed = 0
    with httpx.Client(timeout=30.0, follow_redirects=False,
                      headers={"User-Agent": UA}) as cl:
        for n, doi in enumerate(dois, 1):
            try:
                r = cl.head(f"https://doi.org/{doi}")
                loc = r.headers.get("location") or ""
                host = urlsplit(loc).netloc.lower() if loc else ""
            except Exception:                               # noqa: BLE001
                host = ""
            if host:
                conn.execute(
                    "UPDATE documents SET final_host = ? WHERE doi = ?",
                    (host, doi))
                hosts[host] = hosts.get(host, 0) + 1
            else:
                failed += 1
            if n % 50 == 0:
                conn.commit()
                console.print(f"  [dim]{n:>6,}/{len(dois):,}[/dim]  "
                              f"{len(hosts)} distinct hosts, {failed} unresolved")
            time.sleep(args.rate)
    conn.commit()

    console.print(f"\n[bold]{sum(hosts.values()):,} documents now name a host"
                  f"[/bold], {failed:,} still do not")
    for h, c in sorted(hosts.items(), key=lambda x: -x[1])[:15]:
        console.print(f"  {h:<38} {c:>6,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
