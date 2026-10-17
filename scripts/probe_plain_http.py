"""Ask every blocked host once, with a plain HTTP client instead of a browser.

    python scripts/probe_plain_http.py --hosts 150

The archive was captured with Playwright, because MHTML needs a browser. Bot
management products fingerprint precisely that: headless Chrome, the automation
flags, the TLS and header order a real browser emits. So "blocked" in this
database has always meant "blocked while driving a browser" - a claim about our
client, recorded as a claim about the publisher.

Measured on 2026-09-01: cureus.com is 518/518 blocked in the database, and ten
of ten DOIs returned the full article to httpx with an honest User-Agent. That
is 497 documents that needed no licence, no campus network and no email - only
a different client. Whether any other host behaves the same way is not
something to reason about; it is one request per host away.

One request per host, ordered by how many documents sit behind it. Results go
to probe_results so this never has to be re-run to recall the answer, and so a
host that changes its mind later can be compared against what it did today.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx
from rich.console import Console

from rwpipe import config, db

console = Console()

UA = f"COMP5703-CS43 student project (mailto:{config.POLITE_CONTACT})"
RUN = "plain-http"

CHALLENGE = re.compile(
    r"just a moment|bot manager|captcha|checking your browser|attention required|"
    r"client challenge|are you a robot|access denied|sorry, you have been blocked|"
    r"enable javascript and cookies", re.I)
# ScienceDirect answers 200 with a full-size page and no article in it. Size is
# not evidence; this sentence is.
SOFT = re.compile(r"there was a problem providing the content", re.I)

TARGETS = """
SELECT final_host AS host, COUNT(*) AS n
FROM documents
WHERE COALESCE(usable,0) = 0 AND final_host IS NOT NULL AND final_host <> ''
GROUP BY final_host
ORDER BY n DESC
"""

SAMPLE = """
SELECT doi, final_url FROM documents
WHERE final_host = ? AND COALESCE(usable,0) = 0 AND final_url IS NOT NULL
ORDER BY doi LIMIT 1
"""


def classify(resp, doi, err=None):
    """A verdict, not a guess. Identity is checked last and it is decisive: a
    refusal page can be large, well formed and about something else."""
    if resp is None:
        return "connect-error:" + (err or "?"), None, None
    body = resp.content
    head = body[:8000].decode("utf-8", "ignore")
    n = len(body)
    if CHALLENGE.search(head):
        return "challenge", resp.status_code, n
    if resp.status_code != 200:
        return "http%d" % resp.status_code, resp.status_code, n
    if SOFT.search(body[:300000].decode("utf-8", "ignore")):
        return "soft-refusal", 200, n
    if body[:4] == b"%PDF":
        return "pdf", 200, n
    if n < 3000:
        return "tiny", 200, n
    if doi.lower().encode() not in body.lower():
        return "not-this-doi", 200, n
    return "ok", 200, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hosts", type=int, default=150,
                    help="how many hosts, largest gap first")
    ap.add_argument("--delay", type=float, default=2.0,
                    help="seconds between requests; these are different hosts")
    args = ap.parse_args()

    conn = db.connect()
    db.init(conn)

    hosts = conn.execute(TARGETS).fetchall()[:args.hosts]
    covered = sum(h["n"] for h in hosts)
    console.print(f"[bold]{len(hosts)} hosts, {covered:,} documents behind them[/bold]")

    wins = []
    counts = {}
    with httpx.Client(follow_redirects=True, timeout=45,
                      headers={"User-Agent": UA}) as client:
        for i, h in enumerate(hosts, 1):
            row = conn.execute(SAMPLE, (h["host"],)).fetchone()
            if row is None:
                continue
            doi, url = row["doi"], row["final_url"]
            # A challenge redirect is not an address worth re-requesting; the
            # DOI resolver reaches the article and supplies the right case.
            if "perfdrive.com" in url or "/validate" in url:
                url = "https://doi.org/" + doi
            try:
                resp, err = client.get(url), None
            except Exception as exc:                           # noqa: BLE001
                resp, err = None, type(exc).__name__
            verdict, status, nbytes = classify(resp, doi, err)
            counts[verdict.split(":")[0]] = counts.get(verdict.split(":")[0], 0) + 1
            conn.execute(
                """INSERT INTO probe_results
                   (run, target, item, verdict, http_status, bytes, detail, exit_ip, probed_at)
                   VALUES (?,?,?,?,?,?,?,?, datetime('now'))""",
                (RUN, h["host"], doi, verdict, status, nbytes, url[:300], "home"))
            conn.commit()

            if verdict in ("ok", "pdf"):
                wins.append((h["host"], h["n"], nbytes))
                console.print(f"[green]  {h['n']:>6,}  {h['host']:<34} "
                              f"{verdict} {nbytes:,}[/green]")
            elif i % 20 == 0:
                console.print(f"[dim]  {i}/{len(hosts)} probed, "
                              f"{len(wins)} hosts answering[/dim]")
            time.sleep(args.delay)

    console.print(f"\n[bold]{len(wins)} hosts returned the article "
                  f"({sum(w[1] for w in wins):,} documents)[/bold]")
    for host, n, nbytes in sorted(wins, key=lambda w: -w[1]):
        console.print(f"   {n:>6,}  {host}")
    console.print("\nverdicts:")
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        console.print(f"   {k:<20} {v:>4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
