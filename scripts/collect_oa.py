"""Capture the free copy where the publisher's page is shut.

    python scripts/collect_oa.py --probe
    python scripts/collect_oa.py --rate 1.0

8,682 of the 39,708 missing documents have an open-access copy on a host that
answered a plain request on 2026-09-02 - 7,660 of them in PubMed Central, the
rest in repositories and small publishers. That is 22% of the gap, available
with no licence, no institutional signature and no campus network.

**What this does not do is pretend the copy is the publisher's page.** The
client asked for the publisher pages of the CSV's DOIs; a PubMed Central copy is
the same paper from a different host, and part of PMC is the author's accepted
manuscript rather than the published version of record - post-peer-review, so
the science is what was published and retracted, but not the typeset article.
Three columns record that per document (`source_kind`, `oa_host`,
`oa_version`) so the difference is in the data rather than in someone's memory,
and so it can be undone with one DELETE if the client would rather not have it.

The version is read from the page, not from OpenAlex, which labels 7,948 of
these `submittedVersion` when the pages themselves say "Author manuscript ...
Published in final edited form as". A field that is wrong 92% of the time is
not a field to build on.

Captured through the same browser path as the rest of the archive, so these
arrive as MHTML - a rendered page, fully loaded, which is what the client
asked for.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playwright.async_api import async_playwright
from rich.console import Console

from rwpipe import config, db, fetch, storage

console = Console()

# NIH asks for no more than three requests a second and prefers off-peak; one a
# second is well inside that, and PMC is 88% of this queue.
DEFAULT_RATE = 1.0

TARGETS = """
SELECT d.doi, d.doi_prefix,
       COALESCE(r1.title, r2.title) AS rec_title,
       CASE WHEN r1.original_doi IS NOT NULL THEN 'original' ELSE 'retraction' END AS doc_role,
       (SELECT l.landing_url FROM oa_all_locations l
         WHERE l.doi = d.doi AND l.is_oa = 1
           AND l.landing_url IS NOT NULL AND l.landing_url <> ''
           AND l.host IN (SELECT DISTINCT target FROM probe_results
                           WHERE run = 'oa-hosts' AND verdict IN ('ok','pdf'))
         ORDER BY
           -- PubMed Central first: it holds most of this queue, it is stable,
           -- and it carries retraction notices as well as articles.
           CASE WHEN l.host LIKE '%ncbi.nlm.nih.gov' THEN 0 ELSE 1 END,
           l.idx
         LIMIT 1) AS oa_url,
       (SELECT l.host FROM oa_all_locations l
         WHERE l.doi = d.doi AND l.is_oa = 1
           AND l.landing_url IS NOT NULL AND l.landing_url <> ''
           AND l.host IN (SELECT DISTINCT target FROM probe_results
                           WHERE run = 'oa-hosts' AND verdict IN ('ok','pdf'))
         ORDER BY
           CASE WHEN l.host LIKE '%ncbi.nlm.nih.gov' THEN 0 ELSE 1 END,
           l.idx
         LIMIT 1) AS oa_host
FROM documents d
LEFT JOIN records r1 ON r1.original_doi   = d.doi
LEFT JOIN records r2 ON r2.retraction_doi = d.doi
WHERE COALESCE(d.usable,0) = 0
GROUP BY d.doi
HAVING oa_url IS NOT NULL
ORDER BY oa_host, d.doi
"""

# The article floor for repository pages. 15 KB, not 50: the smallest real
# article page measured on this population was 39 KB with 2,281 characters of
# prose, and the floor exists to catch redirect shims and interstitials, which
# are a few KB. Identity does the work the size check used to pretend to do -
# every capture must still contain the DOI it is filed under.
OA_MIN_BYTES = 15_000

# Appended to TARGETS by --skip-pmc.
NOT_PMC = " AND oa_host NOT LIKE '%ncbi.nlm.nih.gov'"

# What the page says about itself. PMC stamps an accepted manuscript clearly.
MANUSCRIPT = re.compile(
    r"author manuscript|published in final edited form|accepted manuscript", re.I)
PREPRINT = re.compile(r"\bpreprint\b|has not been (peer[- ]reviewed|certified)", re.I)


def version_of(mhtml: str) -> str:
    """Read the version off the page rather than trusting the metadata."""
    head = mhtml[:400000]
    if PREPRINT.search(head):
        return "preprint"
    if MANUSCRIPT.search(head):
        return "acceptedManuscript"
    return "published"


async def run(conn, rows, rate, limit):
    stats: Counter = Counter()
    started = time.time()
    async with async_playwright() as pw:
        # Same launch as the main crawler, so these captures are produced the
        # same way as the other 68,922 rather than by a second, subtly
        # different browser.
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"])
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        try:
            for i, r in enumerate(rows, 1):
                if limit and i > limit:
                    break
                doi = r["doi"]
                res, mhtml = await fetch.fetch_page(
                    context, doi,
                    expect_title=r["rec_title"],
                    doc_role=r["doc_role"],
                    url=r["oa_url"],
                    min_bytes=OA_MIN_BYTES)

                # The 50 KB gate in fetch.classify was calibrated on
                # commercial publisher article pages, where most of the bytes
                # are navigation, scripts and recommendation rails. A
                # repository serves the same article with none of that, and
                # measured on fourteen pages the gate got it exactly backwards:
                # academicjournals.org came in at 39-48 KB with 7-12 paragraphs
                # and 2,000-3,300 characters of prose and was rejected, while
                # ajol.info came in at 65 KB with 4 paragraphs and 1,700
                # characters - an abstract-only landing page - and was accepted.
                # Size was measuring furniture, not content.
                #
                # So on this path the size gate is replaced, not lowered.
                # Identity is unchanged: the capture must carry its own DOI.
                # Substance is judged the way the campus kit judges it, by
                # paragraph count, and anything thin is still saved but marked,
                # because Stage 2 is what decides whether a short document is a
                # retraction notice or a stub. Every other block reason - a
                # challenge page, a refusal, the wrong article - still rejects.
                rescued = False
                if (not res.ok and mhtml and res.blocked
                        and (res.block_reason or "").startswith("body too small")
                        and fetch.contains_doi(mhtml, doi)):
                    rescued = True
                    stats["rescued-from-size-gate"] += 1

                if (res.ok or rescued) and mhtml:
                    ver = version_of(mhtml)
                    path, size, gz = storage.write_capture(
                        doi, r["doi_prefix"], mhtml, res.capture_format or "mhtml")
                    conn.execute(
                        """UPDATE documents
                              SET status='done', usable=1, http_status=?,
                                  mhtml_path=?, mhtml_bytes=?, gz_bytes=?,
                                  capture_format=?, final_url=?, final_host=?,
                                  source_kind='oa-mirror', oa_host=?, oa_version=?,
                                  error=NULL, fetched_at=datetime('now')
                            WHERE doi=?""",
                        (res.http_status, storage.rel(path), size, gz,
                         res.capture_format or "mhtml", res.final_url,
                         res.final_host, r["oa_host"], ver, doi))
                    conn.commit()
                    stats["saved"] += 1
                    stats["v:" + ver] += 1
                    if rescued:
                        # Recorded, so the two populations can be compared
                        # later rather than blurred together.
                        conn.execute(
                            "UPDATE documents SET error='oa: below the publisher"
                            " size gate, kept on identity' WHERE doi=?", (doi,))
                elif res.blocked:
                    stats["blocked:" + (res.block_reason or "?")[:24]] += 1
                else:
                    stats["failed:" + (res.error or "?")[:24]] += 1

                if i % 50 == 0:
                    el = max(time.time() - started, 1)
                    console.print(
                        f"[dim]{i:>6,}/{len(rows):,}[/dim] saved {stats['saved']:,}  "
                        f"published {stats['v:published']:,}  "
                        f"manuscript {stats['v:acceptedManuscript']:,}  "
                        f"| {i / el * 3600:,.0f}/h")
                await asyncio.sleep(rate)
        finally:
            await context.close()
            await browser.close()
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--rate", type=float, default=DEFAULT_RATE)
    ap.add_argument("--probe", action="store_true")
    # PubMed Central has already been asked properly. collect_pmc.py went to
    # NCBI's own efetch endpoint for all 9,350 records they hold and recovered
    # 5,413; the remainder are 3,933 the publisher has opted out of full text
    # for and a handful with no PMCID. Browsing those same pages would refuse
    # us 85% of the time and could not produce full text where NCBI itself
    # says there is none. This flag leaves them out so the run spends its time
    # on the 994 hosts nobody has tried.
    ap.add_argument("--skip-pmc", action="store_true",
                    help="skip hosts collect_pmc.py has already answered for")
    args = ap.parse_args()

    conn = db.connect()
    db.init(conn)
    sql = TARGETS
    if args.skip_pmc:
        # TARGETS ends in ORDER BY, so the filter goes on the outside.
        sql = f"SELECT * FROM ({TARGETS}) WHERE 1=1{NOT_PMC}"
    rows = conn.execute(sql).fetchall()

    if args.probe:
        console.print(f"{len(rows):,} documents with a reachable open-access copy")
        by_host: Counter = Counter(r["oa_host"] for r in rows)
        for h, n in by_host.most_common(12):
            console.print(f"   {h:<38} {n:,}")
        return 0
    if not rows:
        console.print("[green]nothing to collect[/green]")
        return 0

    n = min(len(rows), args.limit or len(rows))
    console.print(f"[bold]{n:,} documents[/bold] at {args.rate}s apart "
                  f"- about {n * args.rate / 3600:.1f} h")
    stats = asyncio.run(run(conn, rows, args.rate, args.limit))
    console.print(f"\n[bold]{stats['saved']:,} saved[/bold]")
    for k, v in stats.most_common():
        if k != "saved":
            console.print(f"   {k:<32} {v:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
