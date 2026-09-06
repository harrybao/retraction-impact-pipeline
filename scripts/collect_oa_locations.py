"""Where can each missing document be read legally, for free?

    python scripts/collect_oa_locations.py --probe
    python scripts/collect_oa_locations.py --redo

39,708 documents have no usable capture. 20,926 of them are open access, and
that number answers nothing on its own.

**Open access is a licence, not an open door.** An earlier version of this
docstring asserted that an open-access copy lives in PubMed Central, arXiv or a
repository, none of which run bot management. Measured, most of them are on the
publisher's own platform: 2,362 at iopscience.iop.org, 1,627 at
tandfonline.com, 854 at sciencedirect.com - every one a door already known to
be shut. Bronze open access, 5,073 of these, is by definition free to read on
the publisher's site and nowhere else.

What matters is whether any copy sits on a host that will answer us. Asking 150
hosts once each (`probe_oa_hosts.py`, 2026-09-02): 38 answered, and behind them
are **8,682 documents - 22% of the gap**, all with an HTML landing page, so all
capturable as MHTML like the rest of the archive.

The gap between 20,926 and 8,682 is the whole reason this script only asks
where and downloads nothing.

`best_oa_location` is not enough on its own: OpenAlex ranks the publisher's
version of record first, which is the one copy we cannot fetch. 15,262 of the
open papers list more than one location, so every location is stored and a
repository copy can be preferred later. The single largest find came from that
- 6,005 documents whose free copy is at ncbi.nlm.nih.gov, which a first pass
had written off because it was counting `pmc.ncbi.nlm.nih.gov` and treating a
different hostname for the same PubMed Central as a different publisher.

One request per 50 DOIs, which is OpenAlex's limit. The daily allowance is
1,000 requests: when it is gone the response says so in Retry-After, measured
in hours, and this stops rather than sleeping against it.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx
from rich.console import Console

from rwpipe import config, db

console = Console()

OPENALEX = "https://api.openalex.org/works"
BATCH = 50
SELECT = ("id,doi,open_access,best_oa_location,primary_location,"
          "locations")
HEADERS = {"User-Agent": f"COMP5703-CS43 (mailto:{config.POLITE_CONTACT})"}

TARGETS = """
SELECT d.doi FROM documents d
LEFT JOIN oa_locations o ON o.doi = d.doi
WHERE COALESCE(d.usable,0) = 0 AND o.doi IS NULL
ORDER BY d.doi
"""

# --redo re-asks about everything. The first pass stored only
# `best_oa_location`, which is OpenAlex's judgement of which copy is best and
# means the publisher's version of record - the one copy we cannot fetch. The
# alternates were in the response and were discarded.
REDO = """
SELECT doi FROM documents WHERE COALESCE(usable,0) = 0 ORDER BY doi
"""


class QuotaExhausted(Exception):
    """The daily allowance is gone; waiting will not help before tomorrow."""

    def __init__(self, seconds, limit, remaining):
        self.seconds, self.limit, self.remaining = seconds, limit, remaining
        super().__init__("daily quota exhausted")


def host_of(url):
    if not url:
        return None
    h = (urlsplit(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def pick(work):
    """The best free copy OpenAlex knows of, and what shape it is in.

    `best_oa_location` is OpenAlex's own choice and is usually right; when it
    is absent the locations list is searched for any that is open. Both a
    landing page and a PDF are kept when both exist - they are not
    alternatives, they decide different things downstream.
    """
    loc = work.get("best_oa_location")
    if not loc:
        for cand in (work.get("locations") or []):
            if cand and cand.get("is_oa"):
                loc = cand
                break
    if not loc:
        return {}
    landing = loc.get("landing_page_url")
    pdf = loc.get("pdf_url")
    return {
        "landing_url": landing,
        "pdf_url": pdf,
        "host": host_of(pdf or landing),
        "version": loc.get("version"),
        "license": loc.get("license"),
    }


async def one_batch(client, dois, retries=5):
    filt = "doi:" + "|".join(dois)
    r = None
    for attempt in range(retries):
        try:
            r = await client.get(OPENALEX, headers=HEADERS, timeout=60,
                                 params={"filter": filt, "per-page": BATCH,
                                         "select": SELECT,
                                         "mailto": config.POLITE_CONTACT})
        except Exception:                                      # noqa: BLE001
            if attempt == retries - 1:
                return None
            await asyncio.sleep(2 ** attempt)
            continue
        if r.status_code == 429:
            # Distinguish the two limits before deciding to wait. A per-second
            # rate clears in seconds; the daily allowance does not - it comes
            # back with Retry-After measured in hours, and every retry against
            # it is wasted. The response says which one it is, so read it.
            retry_after = r.headers.get("Retry-After")
            remaining = r.headers.get("X-RateLimit-Remaining")
            if retry_after and retry_after.isdigit() and int(retry_after) > 900:
                raise QuotaExhausted(int(retry_after),
                                     r.headers.get("X-RateLimit-Limit"),
                                     remaining)
            await asyncio.sleep(min(60, 5 * (2 ** attempt)))
            continue
        break
    if r is None or r.status_code != 200:
        return None
    return r.json().get("results") or []


async def run(conn, dois, rate):
    stats = Counter()
    started = time.time()
    batches = [dois[i:i + BATCH] for i in range(0, len(dois), BATCH)]
    async with httpx.AsyncClient() as client:
        for i, batch in enumerate(batches, 1):
            try:
                got = await one_batch(client, batch)
            except QuotaExhausted as q:
                hrs = q.seconds / 3600
                console.print(
                    f"\n[red]OpenAlex daily quota exhausted[/red] "
                    f"(limit {q.limit}, remaining {q.remaining})")
                console.print(f"[yellow]resets in {hrs:.1f} h. "
                              f"Stopping - everything asked so far is saved, and "
                              f"re-running picks up where this left off.[/yellow]")
                stats["stopped-on-quota"] = 1
                break
            if got is None:
                stats["request-failed"] += len(batch)
                await asyncio.sleep(rate)
                continue

            seen = set()
            for w in got:
                doi = (w.get("doi") or "").replace("https://doi.org/", "").lower()
                if not doi:
                    continue
                seen.add(doi)
                oa = w.get("open_access") or {}
                loc = pick(w)
                conn.execute(
                    """INSERT OR REPLACE INTO oa_locations
                       (doi, is_oa, oa_status, landing_url, pdf_url, host,
                        version, license, n_locations, error, fetched_at)
                       VALUES (?,?,?,?,?,?,?,?,?,NULL, datetime('now'))""",
                    (doi, 1 if oa.get("is_oa") else 0, oa.get("oa_status"),
                     loc.get("landing_url"), loc.get("pdf_url"), loc.get("host"),
                     loc.get("version"), loc.get("license"),
                     len(w.get("locations") or [])))
                # Every location, in OpenAlex's own order, so a repository
                # copy can be preferred later over the publisher's.
                for idx, cand in enumerate(w.get("locations") or []):
                    if not cand:
                        continue
                    src = (cand.get("source") or {})
                    landing = cand.get("landing_page_url")
                    pdf = cand.get("pdf_url")
                    conn.execute(
                        """INSERT OR REPLACE INTO oa_all_locations
                           (doi, idx, is_oa, host, landing_url, pdf_url,
                            version, license, source_name)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (doi, idx, 1 if cand.get("is_oa") else 0,
                         host_of(pdf or landing), landing, pdf,
                         cand.get("version"), cand.get("license"),
                         src.get("display_name")))

                if oa.get("is_oa"):
                    stats["open"] += 1
                    if loc.get("pdf_url"):
                        stats["has pdf"] += 1
                    if loc.get("landing_url"):
                        stats["has landing page"] += 1
                    if not loc.get("pdf_url") and not loc.get("landing_url"):
                        stats["open but no address"] += 1
                else:
                    stats["closed"] += 1

            # A DOI OpenAlex returned nothing for is recorded as asked-and-absent,
            # not left to be asked again forever. Absent from OpenAlex is a fact
            # about OpenAlex, so it is stored as an error rather than as closed.
            for doi in batch:
                if doi not in seen:
                    conn.execute(
                        """INSERT OR REPLACE INTO oa_locations
                           (doi, is_oa, error, fetched_at)
                           VALUES (?, NULL, 'not in openalex', datetime('now'))""",
                        (doi,))
                    stats["not in openalex"] += 1
            conn.commit()

            # Every fifth batch, not every twentieth. A line every 1,000 DOIs
            # meant a stalled run and a healthy one looked the same for as long
            # as anyone was willing to wait.
            if i % 5 == 0:
                el = max(time.time() - started, 1)
                done = i * BATCH
                console.print(f"[dim]{done:,}/{len(dois):,}[/dim]  "
                              f"open {stats['open']:,}  pdf {stats['has pdf']:,}  "
                              f"page {stats['has landing page']:,}  "
                              f"| {done / el * 3600:,.0f}/h")
            await asyncio.sleep(rate)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--rate", type=float, default=0.15)
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--redo", action="store_true",
                    help="re-ask about every missing document, to pick up "
                         "the alternate locations the first pass discarded")
    args = ap.parse_args()

    conn = db.connect()
    db.init(conn)

    sql = (REDO if args.redo else TARGETS)
    sql += f" LIMIT {int(args.limit)}" if args.limit else ""
    dois = [r["doi"] for r in conn.execute(sql)]
    if args.probe:
        console.print(f"{len(dois):,} documents to ask about "
                      f"({(len(dois) + BATCH - 1) // BATCH:,} requests)")
        return 0
    if not dois:
        console.print("[green]nothing left to ask[/green]")
        return 0

    n_req = (len(dois) + BATCH - 1) // BATCH
    console.print(f"[bold]{len(dois):,} documents in {n_req:,} requests[/bold] "
                  f"- about {n_req * (args.rate + 0.4) / 60:.0f} min")
    stats = asyncio.run(run(conn, dois, args.rate))
    console.print("")
    for k, v in stats.most_common():
        console.print(f"   {k:<22} {v:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
