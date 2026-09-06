"""Collect from hosts that answer a plain HTTP client but refuse a browser.

    python scripts/collect_plain_http.py --host www.cureus.com
    python scripts/collect_plain_http.py --prefix 10.7759 --rate 3 --dry-run

The whole archive was captured with Playwright, because MHTML needs a browser,
and bot-management products fingerprint exactly that. So "blocked" in this
database has always meant "blocked while driving a browser" - a fact about our
client that was recorded as a fact about the publisher.

Measured 2026-09-01: cureus.com is 518/518 blocked in `documents`, and ten of
ten DOIs returned the full article to httpx with an honest User-Agent. No
licence, no campus network, no correspondence - a different client.

Two things are deliberately not reinvented here:

* **Storage.** `storage.write_capture(..., fmt="html")` puts the file where
  every other capture lives and `storage.rel()` records it data-relative, so a
  teammate's merge resolves it against their own data root. A hand-rolled path
  in this script would have written to a directory only this script knew about,
  and Stage 2 reads `mhtml_path`.
* **Identity.** `fetch.contains_doi` and `fetch.title_matches` are the same
  gates the archive-wide verification used. A separate rule here would mean two
  definitions of "usable" in one dataset, and the first one cost a month:
  10,049 ScienceDirect refusals passed a size check and were reported as
  successes.

`usable` is set only where the DOI is present, which is the decisive test the
rest of the corpus was held to. HTML rather than MHTML loses the inlined
images; the article body, abstract and notice all survive, and
`capture_format` records which container each document is in.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx
from rich.console import Console

from rwpipe import db, fetch, storage

console = Console()

UA = "COMP5703-CS43 student project (mailto:xbao0816@uni.sydney.edu.au)"

CHALLENGE = re.compile(
    r"just a moment|bot manager|captcha|checking your browser|attention required|"
    r"client challenge|are you a robot|access denied|sorry, you have been blocked|"
    r"enable javascript and cookies", re.I)
SOFT = re.compile(r"there was a problem providing the content", re.I)

TARGETS = """
SELECT d.doi, d.doi_prefix, d.final_url,
       COALESCE(r1.title, r2.title) AS rec_title
FROM documents d
LEFT JOIN records r1 ON r1.original_doi   = d.doi
LEFT JOIN records r2 ON r2.retraction_doi = d.doi
WHERE COALESCE(d.usable,0) = 0
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", help="documents whose final_host is this")
    ap.add_argument("--prefix", help="documents with this DOI prefix")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--rate", type=float, default=3.0,
                    help="seconds between requests to the same host")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.host and not args.prefix:
        console.print("[red]give --host or --prefix[/red]")
        return 2

    conn = db.connect()
    db.init(conn)

    sql, params = TARGETS, []
    if args.host:
        sql += " AND d.final_host = ?"; params.append(args.host)
    if args.prefix:
        sql += " AND d.doi_prefix = ?"; params.append(args.prefix)
    sql += " GROUP BY d.doi ORDER BY d.doi"
    if args.limit:
        sql += " LIMIT %d" % int(args.limit)

    rows = conn.execute(sql, params).fetchall()
    if not rows:
        console.print("[green]nothing to collect[/green]")
        return 0

    console.print(f"[bold]{len(rows):,} documents[/bold] at {args.rate}s apart "
                  f"- about {len(rows) * args.rate / 3600:.1f} h")
    if args.dry_run:
        for r in rows[:10]:
            console.print(f"   {r['doi']}  {r['final_url']}")
        return 0

    stats: Counter = Counter()
    started = time.time()

    with httpx.Client(follow_redirects=True, timeout=60,
                      headers={"User-Agent": UA}) as client:
        for i, r in enumerate(rows, 1):
            doi, prefix = r["doi"], r["doi_prefix"]
            url = r["final_url"] or ""
            # A challenge redirect is not an address worth re-requesting, and
            # the DOI resolver also supplies the registered capitalisation -
            # which matters where a journal ISSN carries a capital letter.
            if not url or "perfdrive.com" in url or "/validate" in url:
                url = "https://doi.org/" + doi

            try:
                resp = client.get(url)
            except Exception as exc:                           # noqa: BLE001
                stats["error:" + type(exc).__name__] += 1
                time.sleep(args.rate)
                continue

            html = resp.text
            head = html[:8000]
            if CHALLENGE.search(head):
                stats["challenge"] += 1
            elif resp.status_code != 200:
                stats["http%d" % resp.status_code] += 1
            elif SOFT.search(html[:300000]):
                stats["soft-refusal"] += 1
            elif len(resp.content) < 3000:
                stats["tiny"] += 1
            elif not fetch.contains_doi(html, doi):
                stats["not-this-doi"] += 1
            else:
                # Same secondary gate as the crawler: a page can carry the right
                # DOI in a reference list while being something else entirely.
                sim = 1.0
                if r["rec_title"]:
                    _, sim = fetch.title_matches(html, r["rec_title"])
                if r["rec_title"] and sim < 0.30:
                    stats["wrong-title"] += 1
                else:
                    path, size, gz = storage.write_capture(doi, prefix, html, "html")
                    conn.execute(
                        """UPDATE documents
                              SET status='done', usable=1, http_status=?,
                                  mhtml_path=?, mhtml_bytes=?, gz_bytes=?,
                                  capture_format='html', final_url=?,
                                  error=NULL, fetched_at=datetime('now')
                            WHERE doi=?""",
                        (resp.status_code, storage.rel(path), size, gz,
                         str(resp.url)[:500], doi))
                    conn.commit()
                    stats["saved"] += 1

            if i % 25 == 0:
                el = max(time.time() - started, 1)
                console.print(
                    f"[dim]{i:>6,}/{len(rows):,}[/dim] saved {stats['saved']:,}  "
                    f"challenge {stats['challenge']:,}  "
                    f"wrong-doi {stats['not-this-doi']:,}  | {i / el * 3600:,.0f}/h")
            time.sleep(args.rate)

    console.print(f"\n[bold]{stats['saved']:,} saved[/bold]")
    for k, v in stats.most_common():
        if k != "saved":
            console.print(f"   {k:<18} {v:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
