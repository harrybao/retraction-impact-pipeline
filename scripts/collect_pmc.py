"""Ask PubMed Central for the articles the publishers refuse to serve.

    python scripts/collect_pmc.py --resolve           # DOIs -> PMCIDs
    python scripts/collect_pmc.py --fetch --limit 50  # sample first
    python scripts/collect_pmc.py --fetch             # all of them

The largest single route out of the 39,459 refused documents, and the one that
needs no negotiation, no licence and no evasion: 18,133 of them (46%) have a
free copy on a host that is not the publisher who refused us, and PubMed
Central holds more of those than everywhere else combined.

Two things make this different from every other recovery attempt so far.

**It is the sanctioned endpoint, not the web page.** PMC's article pages refuse
our browser roughly 85% of the time. `efetch.fcgi?db=pmc` is the interface NCBI
publishes for exactly this purpose, and it answers. Asking the front door
politely, over and over, was never going to work; asking the door that was
built to be asked does.

**The identity check is unchanged.** A capture counts only if it contains the
DOI it is filed under. That rule caught 10,265 publisher refusal pages that had
been filed as successes, and it applies to a PMC record just as much: the
converter can map a DOI to a neighbouring record, and in this corpus a
retraction notice and its article often sit adjacent in PMC.

What comes back is JATS XML, not MHTML. That is recorded per document
(`capture_format='jats'`, `source_kind='pmc-efetch'`) so nothing downstream can
mistake one for the other, and so the question of whether the client accepts it
can be answered later without re-fetching anything. For Stages 2 and 3 the XML
is the better artefact: title, abstract, sections and references arrive already
separated.

About 40% of PMC records carry "The publisher of this article does not allow
downloading of the full text" instead of a body. Those are recorded as
`pmc-no-fulltext`, not as successes - a stub that parses cleanly is exactly the
failure this project has been bitten by before.

Rate: NCBI permits 3 requests a second without an API key. We use fewer.
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx
from rich.console import Console

from rwpipe import config, db, fetch, storage

console = Console()

TOOL = "COMP5703-CS43"
EMAIL = config.POLITE_CONTACT
UA = f"{TOOL} student project (mailto:{EMAIL})"

# The converter's documented address since PMC moved to its own host. The old
# one (www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/) answers 301 to this.
IDCONV = "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/"
EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

BATCH = 200          # the converter's documented maximum
# ...but the real limit is the URL, not the count. IOP writes DOIs like
# 10.1088/1742-6596/1432/1/012074, and 200 of those return HTTP 414 "Submitted
# URI too large". Ten batches - 2,000 documents - failed that way and were
# logged as JSONDecodeError, because the code read the body before the status.
IDS_CHARS = 1800     # characters of joined ids per request
GAP = 1.0            # the endpoint throttles below the documented 3/s
# Waits after each HTTP 429, about eight minutes in all. NCBI documents no
# rate limit for the converter, but refuses in spells: on 6 October two runs
# met 429s that outlasted the old waits (10-50 s, 150 s in all), while the
# same request asked alone a few minutes either side was answered, and
# spaced requests went 200, 429, 200, 200, 200.
RETRY_WAITS = (15, 30, 60, 120, 240)

NO_FULLTEXT = "does not allow downloading of the full text"


def unasked(conn) -> list[str]:
    return [r[0] for r in conn.execute(
        """SELECT doi FROM documents
           WHERE status IN ('blocked','failed')
             AND doi NOT IN (SELECT doi FROM pmc_ids)
           ORDER BY doi""")]


def batches(dois: list[str]):
    """Group by URL length as well as count - see IDS_CHARS."""
    cur: list[str] = []
    n = 0
    for d in dois:
        if cur and (len(cur) >= BATCH or n + len(d) + 1 > IDS_CHARS):
            yield cur
            cur, n = [], 0
        cur.append(d)
        n += len(d) + 1
    if cur:
        yield cur


def ask(client: httpx.Client, chunk: list[str]) -> dict | None:
    """One converter request, with the status read before the body.

    Two failures cost 2,000 documents between them and both were reported as
    JSONDecodeError, which describes our parser rather than the request: HTTP
    414 because 200 IOP DOIs make a URL too long for the server, and HTTP 429
    because the endpoint throttles harder than the documented 3/s. A retry
    that does not read the status cannot tell "ask again more slowly" from
    "never ask this again".
    """
    tries = len(RETRY_WAITS) + 1
    for attempt in range(tries):
        try:
            r = client.get(IDCONV, params={"tool": TOOL, "email": EMAIL,
                                           "ids": ",".join(chunk),
                                           "idtype": "doi", "format": "json"})
        except Exception as exc:                            # noqa: BLE001
            console.print(f"  [yellow]{len(chunk)} ids: {type(exc).__name__}[/yellow]")
            if attempt < tries - 1:
                time.sleep(5 * (attempt + 1))
            continue
        if r.status_code == 429:
            if attempt < tries - 1:
                wait = retry_after(r) or RETRY_WAITS[attempt]
                console.print(f"  [yellow]HTTP 429, waiting {wait:.0f}s[/yellow]")
                time.sleep(wait)
            continue
        if r.status_code != 200:
            first = (r.text or "").strip().splitlines()
            console.print(f"  [red]{len(chunk)} ids: HTTP {r.status_code} "
                          f"{first[0][:60] if first else ''}[/red]")
            return None
        try:
            data = r.json()
        except Exception:                                   # noqa: BLE001
            console.print(f"  [red]{len(chunk)} ids: HTTP 200 but not JSON[/red]")
            return None
        # An error the converter reports in a 200 answer is not "none of these
        # DOIs is in PMC"; read as data, it would record them all as not-found.
        if not isinstance(data, dict) or data.get("status", "ok") != "ok":
            msg = data.get("errmsg", "") if isinstance(data, dict) else ""
            console.print(f"  [red]{len(chunk)} ids: converter error {str(msg)[:60]}[/red]")
            return None
        return data
    console.print(f"  [red]{len(chunk)} ids: gave up after {tries} attempts[/red]",
                  soft_wrap=True)                 # R looks for "gave up after"
    return None


def retry_after(r: httpx.Response) -> float:
    """Seconds the server asks us to wait, or 0. Retry-After may also be an
    HTTP date; that form is ignored rather than allowed to raise."""
    try:
        s = float(r.headers.get("Retry-After") or 0)
    except ValueError:
        return 0.0
    # "nan" parses, and a day-long wait would stall the run: capped at the
    # longest of our own waits.
    return min(max(s, 0.0), float(RETRY_WAITS[-1])) if math.isfinite(s) else 0.0


def efetch(client: httpx.Client, pmcid: str) -> httpx.Response | None:
    """One record from E-utilities, waiting out HTTP 429 as `ask` does.
    None when NCBI still refuses after every wait."""
    for attempt in range(len(RETRY_WAITS) + 1):
        r = client.get(EFETCH, params={"db": "pmc", "id": pmcid, "retmode": "xml",
                                       "tool": TOOL, "email": EMAIL})
        if r.status_code != 429:
            return r
        if attempt < len(RETRY_WAITS):
            wait = retry_after(r) or RETRY_WAITS[attempt]
            console.print(f"  [yellow]HTTP 429, waiting {wait:.0f}s[/yellow]")
            time.sleep(wait)
    return None


def resolve(conn, client: httpx.Client, limit: int | None) -> None:
    todo = unasked(conn)
    if limit:
        todo = todo[:limit]
    if not todo:
        console.print("[green]every refused DOI has already been asked about[/green]")
        return
    console.print(f"[bold]asking NCBI about {len(todo):,} refused DOIs[/bold] "
                  f"({(len(todo) + BATCH - 1) // BATCH} requests)\n")

    found = done = 0
    for chunk in batches(todo):
        data = ask(client, chunk)
        if data is None:
            # Left unasked for the next run. After a refusal spell, stop: each
            # further batch would wait out the same eight minutes.
            break
        done += len(chunk)

        rows, seen = [], set()
        for rec in data.get("records", []):
            doi = (rec.get("doi") or "").lower()
            seen.add(doi)
            pmcid = rec.get("pmcid")
            rows.append((doi, pmcid, rec.get("pmid"),
                         "found" if pmcid else (rec.get("errmsg") or "not-found")))
            found += bool(pmcid)
        # A DOI the converter says nothing about is an answer too, and one we
        # should not pay for twice.
        for doi in chunk:
            if doi.lower() not in seen:
                rows.append((doi, None, None, "not-found"))

        conn.executemany(
            """INSERT OR REPLACE INTO pmc_ids (doi, pmcid, pmid, status, checked_at)
               VALUES (?,?,?,?, datetime('now'))""", rows)
        conn.commit()
        if done % 2000 < len(chunk):
            console.print(f"  [dim]{done:>6,}/{len(todo):,}[/dim]  in PMC {found:,}")
        time.sleep(GAP)

    console.print(f"\n[bold]{found:,} of {done:,} refused documents asked are in "
                  f"PubMed Central[/bold]  ({found / max(done, 1) * 100:.1f}%)")
    if done < len(todo):
        console.print(f"  {len(todo) - done:,} not asked (NCBI refused); the next run asks them")


def fetch_all(conn, client: httpx.Client, limit: int | None) -> None:
    rows = conn.execute(
        """SELECT p.doi, p.pmcid, d.doi_prefix
           FROM pmc_ids p JOIN documents d ON d.doi = p.doi
           WHERE p.pmcid IS NOT NULL AND d.status IN ('blocked','failed')
           ORDER BY p.doi""").fetchall()
    if limit:
        rows = rows[:limit]
    if not rows:
        console.print("[yellow]nothing to fetch - run --resolve first[/yellow]")
        return

    console.print(f"[bold]fetching {len(rows):,} records from PMC[/bold]\n")
    got = stub = wrong = err = 0
    started = time.time()

    for n, (doi, pmcid, prefix) in enumerate(rows, 1):
        try:
            r = efetch(client, pmcid)
        except Exception as exc:                            # noqa: BLE001
            err += 1
            conn.execute("UPDATE documents SET error=?, attempts=attempts+1 "
                         "WHERE doi=?", (f"pmc: {type(exc).__name__}", doi))
            time.sleep(3)
            continue
        if r is None:
            # Still refused after eight minutes: stop rather than wait that
            # long for every record. The rest stay blocked, so the next run
            # fetches them.
            console.print(f"  [red]{pmcid}: gave up after {len(RETRY_WAITS) + 1} "
                          f"attempts; {len(rows) - n + 1} records left for the next run[/red]",
                          soft_wrap=True)         # R looks for "gave up after"
            asked = n - 1
            break
        if r.status_code != 200:
            # The status was never read here, so a refusal page went on to the
            # DOI check and was recorded as "pmc-wrong-record".
            err += 1
            conn.execute("UPDATE documents SET error=?, attempts=attempts+1 "
                         "WHERE doi=?", (f"pmc: http {r.status_code}", doi))
            time.sleep(GAP)
            continue
        xml = r.text

        if NO_FULLTEXT in xml:
            stub += 1
            conn.execute(
                "UPDATE documents SET error=?, attempts=attempts+1 WHERE doi=?",
                ("pmc-no-fulltext: publisher opted out", doi))
        elif not fetch.contains_doi(xml, doi):
            # Same rule as everywhere else. A record that does not carry its own
            # DOI is not evidence about this paper, however well-formed it is.
            wrong += 1
            conn.execute(
                "UPDATE documents SET error=?, attempts=attempts+1 WHERE doi=?",
                (f"pmc-wrong-record: {pmcid}", doi))
        else:
            path, raw_n, gz_n = storage.write_capture(doi, prefix, xml, fmt="jats")
            conn.execute(
                """UPDATE documents
                   SET status='done', usable=1, http_status=?, error=NULL,
                       mhtml_path=?, mhtml_bytes=?, gz_bytes=?,
                       capture_format='jats', source_kind='pmc-efetch',
                       oa_host='pmc.ncbi.nlm.nih.gov',
                       final_url=?, final_host='pmc.ncbi.nlm.nih.gov',
                       attempts=attempts+1, fetched_at=datetime('now')
                   WHERE doi=?""",
                (r.status_code, storage.rel(path), raw_n, gz_n,
                 f"{EFETCH}?db=pmc&id={pmcid}", doi))
            got += 1

        if n % 25 == 0:
            conn.commit()
        if n % 250 == 0:
            el = max(time.time() - started, 1)
            console.print(f"  [dim]{n:>6,}/{len(rows):,}[/dim]  "
                          f"full text {got:,}  opted out {stub:,}  "
                          f"wrong record {wrong:,}  error {err:,}  "
                          f"| {n / el * 3600:,.0f}/h")
        time.sleep(GAP)
    else:
        asked = len(rows)

    conn.commit()
    console.print(f"\n[bold]{asked:,} of {len(rows):,} records asked for[/bold]")
    console.print(f"  full text recovered  {got:>7,}")
    console.print(f"  publisher opted out  {stub:>7,}")
    console.print(f"  wrong record         {wrong:>7,}")
    console.print(f"  request failed       {err:>7,}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resolve", action="store_true", help="DOIs -> PMCIDs")
    ap.add_argument("--fetch", action="store_true", help="PMCIDs -> JATS XML")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    if not (args.resolve or args.fetch):
        ap.error("pass --resolve, --fetch, or both")

    conn = db.connect()
    db.init(conn)
    with httpx.Client(timeout=60.0, follow_redirects=True,
                      headers={"User-Agent": UA}) as client:
        if args.resolve:
            resolve(conn, client, args.limit)
        if args.fetch:
            fetch_all(conn, client, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
