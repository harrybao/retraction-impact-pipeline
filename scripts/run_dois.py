"""Run the whole pipeline on a handful of DOIs, from the Retraction Watch CSV or not.

    python scripts/run_dois.py prepare [--csv PATH] [--csv-only] ITEM [ITEM ...]
    python scripts/crawl.py --workers 1 --max-attempts 1
    python scripts/run_dois.py requeue        # then, after a pause:
    python scripts/crawl.py --workers 1 --max-attempts 2
    python scripts/verify_archives.py
    python scripts/extract_text.py
    python scripts/run_dois.py report [--reference DIR] [--out PATH]

This is the client's handover test: a few extreme examples from the Retraction
Watch CSV, to see whether the code works.
The R function `run_pipeline()` runs the five steps above against a scratch
data directory (RWPIPE_DATA), so a test never touches the collected dataset.

ITEM is a DOI, in any form `clean_doi` accepts, or a Record ID written as
`record:241`. A DOI queues that one page. A Record ID queues both of the
record's pages, and it is the only way to name a record that has no usable
DOI.

A DOI the CSV does not hold is run all the same - a pipeline should take any
DOI. It gets a record of its own in this run's database only, filled from
Crossref's public metadata: its title, journal and publisher, and whether
Crossref lists a retraction of it, or lists it as a retraction notice. A DOI
Crossref does not know is run by its DOI alone. `--csv-only` turns this off
(no Crossref requests; such DOIs are reported as not run).

Every item ends as one or more rows of `run_report.csv`, each with a
plain-language reason. A test that fails is still a result: the publisher
refused, the record has no DOI, doi.org does not know the DOI. Nothing is
silently dropped. Where our collected dataset holds the page anyway, `our_dataset` says
by which route (for example a teammate's library-access batch, or the Elsevier
API). The page then exists, but it cannot be re-fetched from an arbitrary
machine.
"""
from __future__ import annotations

import argparse
import csv
import html
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rwpipe import config, db  # noqa: E402
from rwpipe.csvload import build_tasks, clean_doi, load_records  # noqa: E402

REQUEST = "run_request.json"
REPORT = "run_report.csv"
MAX_SCRATCH_DOCUMENTS = 1_000

RECORD_ITEM = re.compile(r"^\s*record\s*:\s*(\d+)\s*$", re.I)

RECORD_SQL = """INSERT OR REPLACE INTO records (record_id, title, subject, journal,
    publisher, country, article_type, retraction_nature, reason, paywalled,
    retraction_date, original_date, original_doi, retraction_doi)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""

# How a document in our dataset was obtained, in words the client can act on.
ROUTES = {
    None: "our crawler",
    "": "our crawler",
    "oa-mirror": "an open-access copy found by our crawler",
    "elsevier-api": "the Elsevier API (needs an API key)",
    "pmc-efetch": "PubMed Central",
    "iop-sftp": "IOP's licensed text-and-data-mining delivery",
    "teammate-tandf": "a teammate's Taylor & Francis collection on University library access",
    "teammate-sage": "a teammate's SAGE collection",
    "campus-network": "collection on the University of Sydney network",
    "wayback": "the Internet Archive",
    "tail-crossref-url": "a plain request to the publisher URL registered with Crossref",
    "tail-plain-http": "a plain HTTP request",
}


# --- prepare --------------------------------------------------------------
def check_scratch(conn, data_dir: Path) -> None:
    """Refuse to queue test pages into the collected dataset.

    `crawl.py` fetches every pending page in the database it is pointed at, so
    a test prepared against the real database would re-crawl whatever is
    pending there.
    """
    if data_dir.resolve() == (config.ROOT / "data").resolve():
        raise SystemExit("RWPIPE_DATA must point at a scratch directory, "
                         "not the collected dataset")
    n = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    if n > MAX_SCRATCH_DOCUMENTS:
        raise SystemExit(f"{data_dir} holds {n:,} documents - not a scratch directory")


def resolve(df, items: list[str]) -> list[dict]:
    """What each requested item means in the CSV, and which pages it queues."""
    by_doi: dict[str, list[int]] = {}
    for rid, o, r in zip(df["Record ID"], df["original_doi"], df["retraction_doi"]):
        for d in (o, r):
            if d:
                by_doi.setdefault(d, []).append(int(rid))
    ids = set(int(x) for x in df["Record ID"])
    rows = df.set_index("Record ID")

    out = []
    for item in items:
        m = RECORD_ITEM.match(item)
        if m:
            rid = int(m.group(1))
            if rid not in ids:
                out.append(dict(item=item, kind="record", record_ids=[], pages=[],
                                note="Record ID not in the Retraction Watch CSV"))
                continue
            rec = rows.loc[rid]
            pages = [d for d in (rec["original_doi"], rec["retraction_doi"]) if d]
            note = "" if pages else ("record has no usable DOI (the CSV cell is empty or "
                                     "'unavailable'), so there is no page to fetch")
            if pages and not rec["original_doi"]:
                note = "record has no usable original DOI; only its retraction notice can be fetched"
            out.append(dict(item=item, kind="record", record_ids=[rid], pages=pages, note=note))
            continue

        doi = clean_doi(item)
        if not doi:
            out.append(dict(item=item, kind="invalid", record_ids=[], pages=[],
                            note="not a DOI"))
        elif doi not in by_doi:
            out.append(dict(item=item, kind="doi", record_ids=[], pages=[],
                            note="DOI not in the Retraction Watch CSV"))
        else:
            out.append(dict(item=item, kind="doi", record_ids=sorted(by_doi[doi]),
                            pages=[doi], note=""))
    return out


# --- DOIs the CSV does not hold ---------------------------------------------
CROSSREF = "https://api.crossref.org/works/"
# Crossref's update types that withdraw a paper from the record, in the order a
# record is filed under when a paper has several (a retraction outranks an
# expression of concern). Corrections do not count.
NOTICE_TYPES = ("retraction", "withdrawal", "removal", "expression_of_concern")
# Publishers' title prefixes ("RETRACTED ARTICLE: ...", "Retraction Note: ...").
# The CSV's titles carry none, and identity checks compare a page with the
# record's title, so they are taken off.
NOTICE_PREFIX = re.compile(r"^\s*(retracted(\s+article)?|retraction(\s+note)?(\s+to)?|"
                           r"notice\s+of\s+retraction|withdrawn(\s+article)?|"
                           r"expression\s+of\s+concern)\s*[:.-]\s*", re.I)
TAG = re.compile(r"<[^>]+>")
# A notice that retracts many papers gets one record per paper, as in the CSV;
# the titles of the first few are asked for, the rest stay blank (identity
# checks skip a blank title rather than match against a wrong one).
TITLE_LOOKUPS = 20
# The CSV's columns that queue() writes into the records table.
RECORD_COLUMNS = ["Record ID", "Title", "Subject", "Journal", "Publisher", "Country",
                  "ArticleType", "RetractionNature", "Reason", "Paywalled", "RetractionDate",
                  "OriginalPaperDate", "original_doi", "retraction_doi"]


class CrossrefUnavailable(Exception):
    """Crossref did not answer - unlike a 404, which says it does not know a DOI."""


def crossref_work(doi: str, tries: int = 3) -> dict | None:
    """Crossref's metadata for one DOI; None when Crossref does not know it
    (HTTP 404). Raises CrossrefUnavailable when it does not answer."""
    # A DOI pasted from a doi.org address arrives percent-encoded (%3C for <);
    # quote() would encode the % again and Crossref would not find it.
    url = (CROSSREF + urllib.parse.quote(urllib.parse.unquote(doi), safe="/") + "?"
           + urllib.parse.urlencode({"mailto": config.POLITE_CONTACT}))
    req = urllib.request.Request(url, headers={
        "User-Agent": f"cs43-retraction-pipeline (mailto:{config.POLITE_CONTACT})"})
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r).get("message")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError,
                ValueError):
            pass
        if i < tries - 1:
            time.sleep(2 * (i + 1))
    raise CrossrefUnavailable(doi)


def ask_once_down(fetch=crossref_work):
    """`fetch`, except that once Crossref has failed to answer, the rest of the
    run does not ask it again (a thousand DOIs would otherwise wait out a
    thousand timeouts)."""
    down = []

    def f(doi):
        if down:
            raise CrossrefUnavailable(doi)
        try:
            return fetch(doi)
        except CrossrefUnavailable:
            down.append(doi)
            raise
    return f


def _safe(fetch, doi):
    try:
        return fetch(doi)
    except CrossrefUnavailable:
        return None


def _csv_date(d: dict | None) -> str:
    """A Crossref date as the CSV writes its dates ("5/3/2012 0:00")."""
    try:
        parts = (list(d["date-parts"][0]) + [1, 1])[:3]
        y, m, day = (int(x) for x in parts)
        return f"{m}/{day}/{y} 0:00"
    except (KeyError, IndexError, TypeError, ValueError):
        return ""


def _iso_date(d: dict | None) -> str:
    """A Crossref date for a reader: 2026-10-10."""
    try:
        return "-".join(f"{int(x):02d}" for x in d["date-parts"][0][:3])
    except (KeyError, IndexError, TypeError, ValueError):
        return ""


def _first(m: dict | None, key: str) -> str:
    v = (m or {}).get(key) or [""]
    return (v[0] if isinstance(v, list) and v else v) or ""


def _title(m: dict | None) -> str:
    """Crossref's title as plain text: JATS/MathML tags and entities out, the
    publisher's "RETRACTED ARTICLE:" prefix off."""
    t = re.sub(r"\s+", " ", html.unescape(TAG.sub(" ", _first(m, "title")))).strip()
    return NOTICE_PREFIX.sub("", t)


def _notices(updates) -> list[dict]:
    """The updates that withdraw a paper, best first (a retraction before an
    expression of concern), one per DOI."""
    ups = sorted((u for u in (updates or []) if isinstance(u, dict)
                  and u.get("type") in NOTICE_TYPES and clean_doi(u.get("DOI") or "")),
                 key=lambda u: NOTICE_TYPES.index(u["type"]))
    out, seen = [], set()
    for u in ups:
        d = clean_doi(u["DOI"])
        if d not in seen:
            seen.add(d)
            out.append(u)
    return out


def _what(u: dict) -> str:
    return (u.get("label") or u["type"].replace("_", " ")).lower()


def _record_id(original: str, retraction: str | None) -> int:
    """Negative, so it cannot meet a Retraction Watch Record ID; derived from
    the pair, so a paper and its notice requested together share one record,
    and a resumed run gives a DOI the same record again."""
    return -(zlib.crc32(f"{original}|{retraction or ''}".encode("utf-8")) + 1)


def _record(original, retraction=None, **fields) -> dict:
    rec = {c: "" for c in RECORD_COLUMNS}
    rec.update(fields)
    rec.update({"Record ID": _record_id(original, retraction), "original_doi": original,
                "retraction_doi": retraction or None})
    return rec


def unlisted_record(doi: str, fetch=crossref_work) -> tuple[list[dict], str]:
    """Records in the CSV's columns for a DOI the CSV does not hold, and a note
    saying what Crossref knows of it.

    A notice is filed as the CSV files one: its own DOI in the retraction
    column, each retracted paper's in the original column (one record per
    paper), the paper's title as the record's title (identity checks compare
    a notice page with it).
    """
    try:
        m = fetch(doi)
    except CrossrefUnavailable:
        return [_record(doi)], ("not in the Retraction Watch CSV, and Crossref did not answer, "
                                "so its title and retraction status are unknown: run by its DOI alone")
    if m is None:
        return [_record(doi)], ("not in the Retraction Watch CSV, and Crossref has no record of it: "
                                "run by its DOI alone")
    own = dict(Title=_title(m), Journal=_first(m, "container-title"),
               Publisher=m.get("publisher") or "", ArticleType=m.get("type") or "",
               OriginalPaperDate=_csv_date(m.get("issued")))
    ups = _notices(m.get("update-to"))
    if ups:
        best = ups[0]
        dated = dict(RetractionDate=_csv_date(best.get("updated")),
                     RetractionNature=best.get("label") or "")
        papers = [clean_doi(u["DOI"]) for u in ups]
        if papers == [doi]:
            # A Springer "RETRACTED ARTICLE": the article's own page carries
            # the notice, so the CSV holds it in both columns.
            return [_record(doi, doi, **own, **dated)], (
                f"not in the Retraction Watch CSV; Crossref marks the article itself as "
                f"withdrawn by a {_what(best)} on its own page")
        recs = []
        for i, paper in enumerate(papers):
            pm = _safe(fetch, paper) if i < TITLE_LOOKUPS and paper != doi else None
            fields = {**own, **dated, "OriginalPaperDate": _csv_date((pm or {}).get("issued"))}
            fields["Title"] = _title(pm) if pm else (own["Title"] if len(papers) == 1 else "")
            recs.append(_record(paper, doi, **fields))
        what = (f"the {_what(best)} notice for {papers[0]}" if len(papers) == 1 else
                f"a {_what(best)} notice for {len(papers)} papers ({papers[0]} and others)")
        return recs, f"not in the Retraction Watch CSV; Crossref lists it as {what}"
    by = _notices(m.get("updated-by"))
    if by:
        best = by[0]
        notice = clean_doi(best["DOI"])
        when = _iso_date(best.get("updated"))
        rec = _record(doi, notice, **own, RetractionDate=_csv_date(best.get("updated")),
                      RetractionNature=best.get("label") or "")
        return [rec], (f"not in the Retraction Watch CSV; Crossref lists a {_what(best)} of it "
                       f"(notice {notice}{', ' + when if when else ''})")
    return [_record(doi, **own)], ("not in the Retraction Watch CSV, and Crossref lists no "
                                   "retraction of it: run as an ordinary paper, without a "
                                   "retraction date")


def add_unlisted(df, resolved: list[dict], lookup=None):
    """Give each requested DOI the CSV does not hold records of its own, so the
    rest of the pipeline runs on it as on any other. The records go into this
    run's database only; the CSV and the collected dataset are not changed."""
    if lookup is None:
        fetch = ask_once_down()

        def lookup(d):
            return unlisted_record(d, fetch=fetch)
    rows, seen = {}, {}
    for r in resolved:
        doi = clean_doi(r["item"]) if r["kind"] == "doi" and not r["pages"] else ""
        if not doi:
            continue
        if doi not in seen:
            recs, note = lookup(doi)
            for rec in recs:
                rows.setdefault(rec["Record ID"], rec)
            seen[doi] = ([rec["Record ID"] for rec in recs], note)
        rids, note = seen[doi]
        r.update(kind="unlisted", record_ids=rids, pages=[doi], note=note)
    if rows:
        # Only the new rows are given values; the CSV's own rows keep None for
        # a missing DOI, which the database stores as NULL.
        df = pd.concat([df, pd.DataFrame(list(rows.values()), columns=RECORD_COLUMNS)],
                       ignore_index=True)
    return df


def queue(conn, df, resolved: list[dict]) -> int:
    """Insert the records involved and a pending page for each requested DOI.

    All records that name a requested DOI are inserted, because a document's
    role (original, retraction, or both) is read from them. Only the requested
    pages are queued: a notice shared by 659 records must not queue 659
    articles.
    """
    pages = {d for r in resolved for d in r["pages"]}
    rids = {rid for r in resolved for rid in r["record_ids"]}
    names = df["original_doi"].isin(pages) | df["retraction_doi"].isin(pages)
    sub = df[df["Record ID"].isin(rids) | names]
    conn.executemany(RECORD_SQL, [
        (int(r["Record ID"]), r["Title"], r["Subject"], r["Journal"], r["Publisher"],
         r["Country"], r["ArticleType"], r["RetractionNature"], r["Reason"],
         r["Paywalled"], r["RetractionDate"], r["OriginalPaperDate"],
         r["original_doi"], r["retraction_doi"])
        for _, r in sub.iterrows()])
    # One row per (record, page) - the role is read from them - but one page
    # to fetch per DOI. The count returned is the second: "787 page(s)" for a
    # run that fetches 16 was the first, and read as a fault.
    queued = set()
    for t in build_tasks(sub):
        if t.doi in pages:
            conn.execute("INSERT OR IGNORE INTO pages (record_id, kind, doi, doi_prefix) "
                         "VALUES (?,?,?,?)", (t.record_id, t.kind, t.doi, t.prefix))
            queued.add(t.doi)
    conn.commit()
    db.sync_documents(conn)
    return len(queued)


def prepare(args) -> int:
    csv_path = Path(args.csv) if args.csv else config.RAW_CSV
    df = load_records(csv_path)
    conn = db.connect()
    db.init(conn)
    check_scratch(conn, config.DATA)
    csv_records = len(df)
    resolved = resolve(df, args.items)
    if not args.csv_only:
        df = add_unlisted(df, resolved)
    n = queue(conn, df, resolved)
    (config.DATA / REQUEST).write_text(json.dumps(
        {"csv": str(csv_path), "csv_records": csv_records, "items": resolved},
        indent=1, ensure_ascii=False), encoding="utf-8")
    for r in resolved:
        what = ", ".join(r["pages"]) or "-"
        print(f"  {r['item']:<40} pages: {what}  {r['note']}")
    print(f"{n} page(s) to fetch (one per DOI) in {config.DATA}")
    return 0


# --- requeue --------------------------------------------------------------
# Refusals that come and go. Springer and Nature serve "Client Challenge" to
# some requests and the article to others: a teammate's run at another hour
# held 1,357 pages our crawler had been refused. An HTTP 403 or a missing
# DOI is structural and is not retried.
TRANSIENT = ("challenge title:%", "challenge markup:%", "body too small%")
# doi.org's own "DOI not found" page is small, and so reads as an interstitial;
# it will not change in a minute, so it is not retried.
DOI_HOSTS = ("doi.org", "dx.doi.org")


def unregistered(http_status, final_host) -> bool:
    return http_status == 404 and (final_host or "").lower() in DOI_HOSTS


def requeue_transient(conn) -> int:
    where = " OR ".join("error LIKE ?" for _ in TRANSIENT)
    hosts = ",".join("?" for _ in DOI_HOSTS)
    n = conn.execute(f"UPDATE documents SET status = 'pending' "
                     f"WHERE status = 'blocked' AND ({where}) AND NOT ("
                     f"COALESCE(http_status, 0) = 404 AND LOWER(COALESCE(final_host, '')) IN ({hosts}))",
                     TRANSIENT + DOI_HOSTS).rowcount
    conn.commit()
    return n


def requeue(args) -> int:
    print(f"requeued {requeue_transient(db.connect())}")
    return 0


# --- report ---------------------------------------------------------------
# What each recovery route said about a page that is still not held, so a
# refusal does not hide behind the crawler's reason (7 October 2026: NCBI
# refused and the report said only "the publisher refused automated access").
ARCHIVE_SAYS = {
    "no-snapshot": "holds no copy",
    "no-snapshot-doi": "holds no copy of the DOI address (the page's own address was not known)",
    "thin": "holds only a stub or a bot check",
    "wrong-article": "holds a copy that is not this document",
    "other-doi": "holds a copy that declares another DOI (kept for review)",
    "error-page": "holds a copy of an error page",
    "unreadable": "holds a copy that could not be read",
}


def has_table(conn, name) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (name,)).fetchone() is not None


def routes_tried(conn, doi) -> list[str]:
    """PubMed Central's and the Internet Archive's answers for one page; empty
    when the run did not try the recovery routes."""
    out = []
    if has_table(conn, "pmc_ids"):
        r = conn.execute("SELECT pmcid FROM pmc_ids WHERE doi = ?", (doi,)).fetchone()
        out.append("PubMed Central: " + ("not asked (NCBI did not answer)" if r is None else
                                         "holds it, but not its full text" if r[0] else
                                         "does not hold it"))
    if has_table(conn, "wayback_captures"):
        r = conn.execute("SELECT status FROM wayback_captures WHERE doi = ?", (doi,)).fetchone()
        out.append("Internet Archive: " + ("not asked (the Archive did not answer)" if r is None
                                           else ARCHIVE_SAYS.get(r[0], r[0])))
    return out


def explain(status, error, usable, quality, http_status=None, final_host=None) -> str:
    """A crawler status and error, in words."""
    e = error or ""
    if usable != 1 and unregistered(http_status, final_host):
        return "doi.org does not know this DOI: it is not registered, or it is mistyped"
    if status == "done":
        if usable == 1 and quality in ("full", "notice-only"):
            return "ok"
        if usable == 1 and quality == "thin":
            return "fetched and verified, but the page carries very little text"
        if usable == 1:
            return f"fetched and verified; text extraction: {quality or 'not run'}"
        if usable == 0:
            return "fetched, but verification found it is not this paper (error or wrong page)"
        return "fetched, not yet verified"
    if status == "blocked":
        m = re.match(r"http (\d+)", e)
        if m:
            return f"the publisher refused automated access (HTTP {m.group(1)})"
        if e.startswith(("challenge", "bot-management")):
            return "the publisher served a bot check instead of the article"
        if e.startswith("body too small"):
            return "the publisher served an interstitial page, not the article"
        if e.startswith("identity check failed"):
            return "the page returned was not this paper (" + e.split(": ", 1)[-1][:120] + ")"
        if e.startswith("capture too small"):
            return "the page never finished loading the article"
        return "blocked: " + e[:160]
    if status == "failed":
        return "browser or network error: " + e[:160]
    return "not attempted"


def load_reference(ref: Path | None, dois: set[str],
                   unlisted: frozenset[str] = frozenset()) -> dict[str, str]:
    """How our collected dataset holds each DOI, read from its exports."""
    out: dict[str, str] = {d: "not in our dataset (not in the Retraction Watch CSV)"
                           for d in dois & unlisted}
    dois = dois - unlisted
    if not ref or not dois:
        return out
    idx = ref / "stage2_index.csv"
    if idx.exists():
        with open(idx, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                if r["doi"] in dois:
                    route = ROUTES.get(r.get("source_kind") or None, r.get("source_kind"))
                    out[r["doi"]] = (f"held ({r['capture_format']}, text {r['quality']}), "
                                     f"obtained via {route}")
    miss = ref / "not_collected.csv"
    if miss.exists():
        with open(miss, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                if r["doi"] in dois and r["doi"] not in out:
                    route = r.get("route") or "none-known"
                    out[r["doi"]] = (f"not held: {r.get('cause') or 'unknown'}"
                                     + ("" if route == "none-known" else f"; possible route: {route}"))
    for d in dois - out.keys():
        out[d] = "not in our dataset (the record may be newer than our CSV snapshot)"
    return out


FIELDS = ["item", "doi", "page", "records", "publisher", "journal", "csv_title",
          "status", "http_status", "final_host", "capture_format", "verified",
          "quality", "extracted_title", "notice_chars", "body_chars",
          "notice_start", "reason", "obtained_via", "our_dataset",
          "openalex_citations", "altmetric_record", "altmetric_posts", "altmetric_news",
          "plumx_record", "plumx_news"]

PLUMX_NEWS = re.compile(r'"mention\.NEWS_COUNT"\s*:\s*"?([0-9]+)')


def media_of(conn, doi: str, page: str) -> dict:
    """What the media and citation collectors found for one page's DOI.

    A notice's DOI is looked up in the notice tables, a paper's in the paper
    tables; blank means that collector did not run or holds nothing for it.
    """
    out: dict = {}

    def one(sql):
        try:
            return conn.execute(sql, (doi,)).fetchone()
        except Exception:                                  # noqa: BLE001
            return None

    notice = page == "retraction"
    if not notice:
        e = one("SELECT cited_by_count FROM engagement WHERE doi = ?")
        if e is not None:
            out["openalex_citations"] = e["cited_by_count"]
    a = one("SELECT found FROM altmetric_notice WHERE retraction_doi = ?" if notice
            else "SELECT found FROM altmetric WHERE doi = ?")
    if a is not None:
        out["altmetric_record"] = "yes" if a["found"] == 1 else "no"
        posts = "altmetric_notice_posts" if notice else "altmetric_posts"
        key = "retraction_doi" if notice else "doi"
        n = one(f"SELECT COUNT(*) n, SUM(kind = 'news') news FROM {posts} WHERE {key} = ?")
        out["altmetric_posts"] = n["n"] if n else 0
        out["altmetric_news"] = (n["news"] or 0) if n else 0
    p = one("SELECT found, detail_json FROM plumx_notice WHERE retraction_doi = ?" if notice
            else "SELECT found, detail_json FROM plumx WHERE doi = ?")
    if p is not None:
        out["plumx_record"] = "yes" if p["found"] == 1 else "no"
        m = PLUMX_NEWS.search(p["detail_json"] or "")
        out["plumx_news"] = int(m.group(1)) if m else 0
    return out


def report(args) -> int:
    req = json.loads((config.DATA / REQUEST).read_text(encoding="utf-8"))
    conn = db.connect()
    ref = Path(args.reference) if args.reference else None
    dois = {d for r in req["items"] for d in r["pages"]}
    unlisted = frozenset(d for r in req["items"] if r.get("kind") == "unlisted" for d in r["pages"])
    held = load_reference(ref, dois, unlisted)

    rows = []
    for r in req["items"]:
        if not r["pages"]:
            rows.append(dict(item=r["item"], status="not run", reason=r["note"],
                             records=len(r["record_ids"])))
            continue
        for d in r["pages"]:
            doc = conn.execute(
                """SELECT d.status, d.http_status, d.final_host, d.capture_format,
                          d.usable, d.error, d.source_kind,
                          t.quality, t.title, t.body_chars, t.notice
                   FROM documents d LEFT JOIN document_text t ON t.doi = d.doi
                   WHERE d.doi = ?""", (d,)).fetchone()
            recs = conn.execute(
                """SELECT record_id, title, journal, publisher,
                          CASE WHEN original_doi = ? AND retraction_doi = ? THEN 'both'
                               WHEN retraction_doi = ? THEN 'retraction'
                               ELSE 'original' END AS page
                   FROM records WHERE original_doi = ? OR retraction_doi = ?
                   ORDER BY record_id < 0, record_id""", (d, d, d, d, d)).fetchall()
            pages = {x["page"] for x in recs}
            page = "both" if "both" in pages or len(pages) > 1 else (pages.pop() if pages else "")
            first = recs[0] if recs else None
            reason = explain(doc["status"], doc["error"], doc["usable"], doc["quality"],
                             doc["http_status"], doc["final_host"])
            if doc["usable"] != 1:
                reason = "; ".join([reason] + routes_tried(conn, d))
            if r["note"]:
                reason = f"{reason}; {r['note']}"
            rows.append(dict(
                item=r["item"], doi=d, page=page, records=len(recs),
                publisher=first["publisher"] if first else "",
                journal=first["journal"] if first else "",
                csv_title=first["title"] if first else "",
                status=doc["status"], http_status=doc["http_status"],
                final_host=doc["final_host"], capture_format=doc["capture_format"],
                verified=doc["usable"], quality=doc["quality"],
                extracted_title=doc["title"], notice_chars=len(doc["notice"] or ""),
                body_chars=doc["body_chars"],
                notice_start=re.sub(r"\s+", " ", (doc["notice"] or ""))[:200],
                reason=reason,
                obtained_via=(ROUTES.get(doc["source_kind"], doc["source_kind"])
                              if doc["usable"] == 1 else ""),
                our_dataset=held.get(d, ""),
                **media_of(conn, d, page)))

    out = Path(args.out) if args.out else config.DATA / REPORT
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in FIELDS})
    for row in rows:
        print(f"  {row.get('doi') or row['item']:<40} {row.get('status', ''):<8} {row['reason']}")
    print(f"report: {out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--csv", default=None, help="Retraction Watch CSV (default: config.RAW_CSV)")
    p.add_argument("--csv-only", action="store_true",
                   help="report DOIs the CSV does not hold as not run, without asking Crossref")
    p.add_argument("items", nargs="+")
    sub.add_parser("requeue", help="put pages refused by a bot check back in the queue")
    r = sub.add_parser("report")
    r.add_argument("--reference", default=None,
                   help="directory with our exports (stage2_index.csv, not_collected.csv)")
    r.add_argument("--out", default=None)
    args = ap.parse_args()
    if os.environ.get("RWPIPE_DATA") is None:
        raise SystemExit("set RWPIPE_DATA to a scratch directory first")
    return {"prepare": prepare, "requeue": requeue, "report": report}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
