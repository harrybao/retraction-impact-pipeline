"""Ask each host that holds an open-access copy whether it will hand it over.

    python scripts/probe_oa_hosts.py --hosts 120

20,926 of the missing documents are open access and that number answers nothing
by itself: open access is a licence, not an open door. The copy has to be
somewhere we can fetch from, and OpenAlex's preferred location is the
publisher's version of record - 2,362 at iopscience.iop.org, 1,627 at
tandfonline.com, 854 at sciencedirect.com, all of them shut to us.

A first attempt at counting the reachable ones inferred reachability from
evidence already in the database, and got it wrong in both directions:

* `pmc.ncbi.nlm.nih.gov` counted as reachable and `ncbi.nlm.nih.gov` as not,
  though they are the same PubMed Central behind two URL forms - 1,203
  documents written off on a hostname;
* `journals.sagepub.com` counted as reachable because the archive holds old
  captures from it, when it refuses us today.

Inference from stale evidence is how "ScienceDirect 100%" got into a report.
This asks instead: one real OA URL per host, one request, identity-checked.
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

from rwpipe import db

console = Console()

UA = "COMP5703-CS43 student project (mailto:xbao0816@uni.sydney.edu.au)"
RUN = "oa-hosts"

CHALLENGE = re.compile(
    r"just a moment|bot manager|captcha|checking your browser|attention required|"
    r"client challenge|are you a robot|access denied|sorry, you have been blocked|"
    r"enable javascript and cookies", re.I)
SOFT = re.compile(r"there was a problem providing the content|"
                  r"this content is not available", re.I)

HOSTS = """
SELECT l.host, COUNT(DISTINCT l.doi) AS n
FROM oa_all_locations l
JOIN documents d ON d.doi = l.doi
WHERE COALESCE(d.usable,0) = 0 AND l.is_oa = 1
  AND l.host IS NOT NULL AND l.host <> ''
GROUP BY l.host
ORDER BY n DESC
"""

SAMPLE = """
SELECT l.doi, l.landing_url, l.pdf_url
FROM oa_all_locations l
JOIN documents d ON d.doi = l.doi
WHERE COALESCE(d.usable,0) = 0 AND l.is_oa = 1 AND l.host = ?
  AND (l.landing_url IS NOT NULL OR l.pdf_url IS NOT NULL)
ORDER BY l.doi LIMIT 1
"""


def classify(resp, doi):
    if resp is None:
        return "connect-error", None, None
    body = resp.content
    head = body[:8000].decode("utf-8", "ignore")
    n = len(body)
    if CHALLENGE.search(head):
        return "challenge", resp.status_code, n
    if resp.status_code != 200:
        return "http%d" % resp.status_code, resp.status_code, n
    if body[:4] == b"%PDF":
        # A PDF is the article; the DOI is often not in the extracted bytes and
        # requiring it here would reject good captures for the wrong reason.
        return ("pdf" if n > 20000 else "tiny-pdf"), 200, n
    text = body[:400000].decode("utf-8", "ignore")
    if SOFT.search(text):
        return "soft-refusal", 200, n
    if n < 3000:
        return "tiny", 200, n
    if doi.lower() not in text.lower():
        return "not-this-doi", 200, n
    return "ok", 200, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hosts", type=int, default=120)
    ap.add_argument("--delay", type=float, default=1.5)
    args = ap.parse_args()

    conn = db.connect()
    db.init(conn)

    hosts = conn.execute(HOSTS).fetchall()[:args.hosts]
    covered = sum(h["n"] for h in hosts)
    console.print(f"[bold]{len(hosts)} hosts, {covered:,} open-access documents "
                  f"behind them[/bold]\n")

    counts: Counter = Counter()
    good = []
    with httpx.Client(follow_redirects=True, timeout=45,
                      headers={"User-Agent": UA}) as client:
        for i, h in enumerate(hosts, 1):
            row = conn.execute(SAMPLE, (h["host"],)).fetchone()
            if row is None:
                continue
            # The landing page first: it is the only shape that can become
            # MHTML, which is the format the client asked for. The PDF is the
            # fallback and is worth recording separately.
            url = row["landing_url"] or row["pdf_url"]
            try:
                resp = client.get(url)
            except Exception:                                  # noqa: BLE001
                resp = None
            verdict, status, nbytes = classify(resp, row["doi"])
            counts[verdict] += 1
            conn.execute(
                """INSERT INTO probe_results
                   (run, target, item, verdict, http_status, bytes, detail,
                    exit_ip, probed_at)
                   VALUES (?,?,?,?,?,?,?, 'home', datetime('now'))""",
                (RUN, h["host"], row["doi"], verdict, status, nbytes, url[:300]))
            conn.commit()
            if verdict in ("ok", "pdf"):
                good.append((h["host"], h["n"], verdict, nbytes))
                console.print(f"[green]  {h['n']:>6,}  {h['host']:<38} "
                              f"{verdict} {nbytes:,}[/green]")
            elif i % 20 == 0:
                console.print(f"[dim]  {i}/{len(hosts)} probed, "
                              f"{len(good)} answering[/dim]")
            time.sleep(args.delay)

    reach = sum(g[1] for g in good)
    console.print(f"\n[bold]{len(good)} hosts answered, "
                  f"{reach:,} documents behind them[/bold]")
    console.print("\nverdicts:")
    for k, v in counts.most_common():
        console.print(f"   {k:<16} {v:>4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
