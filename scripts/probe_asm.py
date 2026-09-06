"""Did ASM's IP allowlist actually open the door?

    python scripts/probe_asm.py --n 8

ASM allowlisted our residential egress on 2026-09-04 while
their escalated ticket with Atypon is still open, and asked us to see whether
it made a difference. This asks exactly that, with a small sample and at the
rate ASM's own TDM licence publishes: 1 request per 6 seconds on weekday
mornings in America/Los_Angeles, 1 per 2 seconds otherwise.

Deliberately small. The permission question is not settled - the Library
declined to write the letter their licensing manager asked for - so this
measures whether the block is gone, and collects nothing.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx
from rich.console import Console

from rwpipe import db, fetch

console = Console()

UA = "COMP5703-CS43 student project (mailto:xbao0816@uni.sydney.edu.au)"
RUN = "asm-allowlist-2026-09-04"


def delay_now() -> float:
    """ASM publishes the rate; use theirs rather than guessing one."""
    la = datetime.now(ZoneInfo("America/Los_Angeles"))
    weekday_morning = la.weekday() < 5 and 6 <= la.hour < 12
    return 6.0 if weekday_morning else 2.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=8)
    args = ap.parse_args()

    conn = db.connect()
    db.init(conn)
    rows = conn.execute(
        """SELECT doi, status, error FROM documents
           WHERE final_host = 'journals.asm.org' OR doi LIKE '10.1128/%'
           ORDER BY (status = 'blocked') DESC, doi LIMIT ?""",
        (args.n,)).fetchall()
    if not rows:
        console.print("[red]no ASM documents found[/red]")
        return 1

    gap = delay_now()
    console.print(f"[bold]{len(rows)} ASM DOIs[/bold], {gap:.0f}s apart "
                  f"(ASM's published TDM rate)\n")

    ok = challenged = other = 0
    with httpx.Client(follow_redirects=True, timeout=45.0,
                      headers={"User-Agent": UA,
                               "Accept": "text/html,application/xhtml+xml"}) as cl:
        for i, r in enumerate(rows):
            if i:
                time.sleep(gap)
            url = f"https://doi.org/{r['doi']}"
            try:
                resp = cl.get(url)
                body = resp.text
                code = resp.status_code
            except Exception as exc:                        # noqa: BLE001
                console.print(f"  {r['doi']:<34} error {type(exc).__name__}")
                conn.execute(
                    """INSERT INTO probe_results
                       (run, target, item, verdict, http_status, bytes, detail,
                        probed_at)
                       VALUES (?,?,?,?,?,?,?, datetime('now'))""",
                    (RUN, "journals.asm.org", r["doi"], "error", None, 0,
                     type(exc).__name__))
                other += 1
                continue

            # A challenge page is a 200 with no article in it, so the verdict
            # cannot come from the status code - the same mistake that filed
            # 10,049 refusal pages as successes.
            has_doi = fetch.contains_doi(body, r["doi"])
            low = body.lower()
            is_challenge = ("just a moment" in low
                            or "cf-chl" in low or "cf_chl" in low)
            verdict = ("article" if has_doi and not is_challenge
                       else "challenge" if is_challenge
                       else f"http-{code}")
            ok += verdict == "article"
            challenged += verdict == "challenge"
            other += verdict.startswith("http-")
            console.print(f"  {r['doi']:<34} {code} {len(body):>8,}B  {verdict}")
            conn.execute(
                """INSERT INTO probe_results
                   (run, target, item, verdict, http_status, bytes, detail,
                    probed_at)
                   VALUES (?,?,?,?,?,?,?, datetime('now'))""",
                (RUN, "journals.asm.org", r["doi"], verdict, code, len(body),
                 str(resp.headers.get("cf-ray") or "")))
    conn.commit()
    console.print(f"\n[bold]article {ok}  challenge {challenged}  other {other}"
                  f"[/bold]  of {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
