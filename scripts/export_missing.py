"""Every page we do not have, why, and what could still get it.

    python scripts/export_missing.py

Two files:

    not_collected.csv          one row per missing document
    not_collected_by_host.csv  the same, aggregated by the host that refused

The project has always kept a reason per failure. What it has not had is a
single register a person can open to answer "what is still missing, and is
anyone doing anything about it" - that answer has lived in six different
queries. A gap of this size is a finding in its own right and belongs in the
deliverables, not in somebody's shell history.

`cause` groups the failures by what actually happened, because the fixes are
different and mixing them hides that:

    403-refused        refused at the connection, before a page rendered - so
                       the decision is about the client, not about browsing
    soft-refusal       HTTP 200, a full-size page, no article in it: Elsevier
                       serving "There was a problem providing the content"
    human-verification a challenge page - Cloudflare, reCAPTCHA, Incapsula,
                       Radware
    never-asked        the circuit breaker stopped after the host proved
                       hopeless. These were never tried, which is not the same
                       as unavailable
    interstitial       something arrived, too small or too early to be an
                       article
    not-found          a genuine 404
    pmc-opted-out      PMC holds the record but the publisher forbids full text

`route` is what could still reach it, and every value rests on a measurement
rather than a hope:

    pmc                NCBI holds a copy, resolved through their own ID
                       converter
    oa-mirror          a free copy on a host that is not the publisher who
                       refused us
    campus             one of the five hosts measured to answer a University
                       address and refuse a residential one
    plain-http         the host answered a plain HTTP client after refusing a
                       browser: the block is a fingerprint, not a policy
    publisher-supply   the publisher agreed to deliver files against a DOI list
    elsevier-api       needs the Article Retrieval API entitlement, disabled on
                       our key
    tdm-registered     the publisher has registered a text-mining endpoint for
                       this DOI in Crossref. A lead, NOT a working route: 36 of
                       these URLs were fetched on 2026-09-01 and all 36 failed
                       (19 HTTP 403, 11 truncated, 6 challenge). Crossref says
                       plainly that a registered link may still require a
                       subscription, a login or a licence. It ranks below every
                       route above it and above none-known, because there is
                       something to ask for
    none-known         nothing measured works, and the publisher has not even
                       registered an endpoint. This is the honest column

An earlier version of this script did not read `api_records.tdm_links` at all,
so 4,274 documents with a registered endpoint were reported as none-known -
the same defect as a citation flag that ignores whether the window covers the
retraction: a column that answers a narrower question than its name claims.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console

from rwpipe import config, db

console = Console()

OUT = config.DATA / "clean"

# This was first written as one SQL statement with correlated subqueries per
# row and did not finish in ten minutes: the "does this record still have its
# other document" test re-joined records to documents 39,000 times. The whole
# working set fits in memory, so it is computed here instead - same answer, in
# seconds.

# Measured 2026-09-01: asked once each with a plain HTTP client and an honest
# User-Agent, after the browser had already been refused.
PLAIN_HTTP_OK = {
    "www.cureus.com", "doiserbia.nb.rs", "www.cellphysiolbiochem.com",
    "www.scielo.br", "link.springer.com",
    "openbiomedicalengineeringjournal.com", "online-journals.org",
    "www.ejmste.com", "www.kjorl.org", "pubs.lib.umn.edu", "www.ccsenet.org",
    "www.biorxiv.org", "journal.mrs-k.or.kr", "www.nepjol.info",
    "www.jcancer.org", "scialert.net", "pjms.org.pk", "www.sciedu.ca",
    "www.kci.go.kr",
}

# Measured on the University network 2026-09-01: these answer a campus address
# and refuse a residential one.
CAMPUS = {"academic.oup.com", "portlandpress.com", "pubs.aip.org",
          "aacrjournals.org", "karger.com"}

# Publishers who agreed to deliver the files against a DOI list.
SUPPLY = {"validate.perfdrive.com", "iopscience.iop.org"}

FIELDS = ["doi", "doi_prefix", "doc_role", "record_id", "title", "journal",
          "publisher", "retraction_date", "final_host", "http_status",
          "attempts", "error", "cause", "route", "pmcid", "oa_host",
          "tdm_link", "other_half_held"]


def cause_of(err: str | None) -> str:
    e = (err or "").lower()
    if e.startswith("soft refusal"):
        return "soft-refusal"
    if (e.startswith("challenge") or "recaptcha" in e
            or "incapsula" in e or "perfdrive" in e):
        return "human-verification"
    if e.startswith("circuit breaker") or e.startswith("archived:"):
        return "never-asked"
    if e.startswith("pmc-no-fulltext"):
        return "pmc-opted-out"
    if e.startswith("pmc-wrong-record"):
        return "pmc-wrong-record"
    if e.startswith("http 403"):
        return "403-refused"
    if "too small" in e:
        return "interstitial"
    if e.startswith("http 404"):
        return "not-found"
    if not e:
        return "never-attempted"
    return "other"


def route_of(host: str | None, err: str | None, pmcid: str, oa_host: str,
             tdm: str) -> str:
    if pmcid:
        return "pmc"
    if oa_host:
        return "oa-mirror"
    if host in CAMPUS:
        return "campus"
    if host in PLAIN_HTTP_OK:
        return "plain-http"
    if host in SUPPLY:
        return "publisher-supply"
    if (err or "").lower().startswith("soft refusal"):
        return "elsevier-api"
    if tdm:
        return "tdm-registered"
    return "none-known"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    conn = db.connect()
    console.print("[dim]loading[/dim]")

    recs, by_orig, by_ret = {}, {}, {}
    for rid, t, j, pub, rd, od, rdoi in conn.execute(
            """SELECT record_id, title, journal, publisher, retraction_date,
                      original_doi, retraction_doi FROM records"""):
        recs[rid] = (t, j, pub, rd, od, rdoi)
        if od:
            by_orig[od] = rid
        if rdoi:
            by_ret[rdoi] = rid

    usable = {r[0] for r in conn.execute(
        "SELECT doi FROM documents WHERE usable = 1")}
    pmc = dict(conn.execute(
        "SELECT doi, pmcid FROM pmc_ids WHERE pmcid IS NOT NULL"))
    host_of = dict(conn.execute("SELECT doi, final_host FROM documents"))
    # Crossref carries the publisher's own registered text-mining endpoints.
    # They were collected weeks ago by enrich.py and then never consulted.
    tdm_of = dict(conn.execute(
        """SELECT doi, tdm_links FROM api_records
           WHERE tdm_links IS NOT NULL AND tdm_links <> ''"""))

    # A free copy on a host that is not the one that refused us. doi.org is
    # excluded: it is the resolver, not a place anything is hosted, and it was
    # inflating the count of documents that looked recoverable.
    oa: dict[str, str] = {}
    for doi, h in conn.execute(
            "SELECT doi, host FROM oa_all_locations WHERE is_oa = 1"):
        if not h or "doi.org" in h or h == host_of.get(doi):
            continue
        oa.setdefault(doi, h)

    rows = []
    for doi, prefix, host, code, attempts, err in conn.execute(
            """SELECT doi, doi_prefix, final_host, http_status, attempts, error
               FROM documents WHERE usable IS NOT 1 ORDER BY doi_prefix, doi"""):
        is_o, is_r = doi in by_orig, doi in by_ret
        role = ("both" if is_o and is_r else
                "retraction-notice" if is_r else "original-article")
        rid = by_orig.get(doi) or by_ret.get(doi)
        title = journal = publisher = rdate = ""
        other_half = 0
        if rid in recs:
            title, journal, publisher, rdate, od, rdoi = recs[rid]
            # Does this record still have its other document? A missing article
            # for a record whose notice we already hold costs less than one
            # where we hold nothing, and recovery should be ordered by that.
            other = rdoi if doi == od else od
            other_half = int(bool(other) and other != doi and other in usable)
        pmcid = pmc.get(doi, "")
        oa_host = oa.get(doi, "")
        tdm = (tdm_of.get(doi) or "").split(";")[0]
        rows.append([doi, prefix, role, rid or "", title, journal, publisher,
                     rdate, host or "", "" if code is None else code, attempts,
                     err or "", cause_of(err),
                     route_of(host, err, pmcid, oa_host, tdm),
                     pmcid, oa_host, tdm, other_half])

    p = OUT / "not_collected.csv"
    with p.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(FIELDS)
        w.writerows(rows)
    console.print(f"{p.name:<28} {len(rows):>7,} rows")

    i = {c: n for n, c in enumerate(FIELDS)}
    agg: dict[tuple, list[int]] = {}
    for r in rows:
        k = (r[i["final_host"]] or "(never resolved)",
             r[i["cause"]], r[i["route"]])
        a = agg.setdefault(k, [0, 0])
        a[0] += 1
        a[1] += 1 - r[i["other_half_held"]]

    p2 = OUT / "not_collected_by_host.csv"
    with p2.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["host", "cause", "route", "pages",
                    "pages_whose_record_has_nothing_else"])
        for (h, cse, rt), (n, alone) in sorted(agg.items(),
                                               key=lambda x: -x[1][0]):
            w.writerow([h, cse, rt, n, alone])
    console.print(f"{p2.name:<28} {len(agg):>7,} rows")

    for label, key in (("by cause", "cause"),
                       ("what could still reach it", "route")):
        d: dict[str, int] = {}
        for r in rows:
            d[r[i[key]]] = d.get(r[i[key]], 0) + 1
        console.print("")
        console.print(f"[bold]{label}[/bold]")
        for k, v in sorted(d.items(), key=lambda x: -x[1]):
            console.print(f"  {k:<22} {v:>7,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
