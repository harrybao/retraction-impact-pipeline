"""Merge the Internet Archive captures into `documents`, after checking each.

    python scripts/merge_wayback.py --dry-run
    python scripts/merge_wayback.py

`collect_wayback.py` saves snapshots into `wayback_captures` and nowhere else.
Until a capture is merged here, verification, text extraction and every export
read `documents` and do not see it. This step was run once by hand on
3 September 2026, merging the 35 captures in the dataset; it is now a script so
that the pipeline (`recover_wayback()` in R, and `run_pipeline()`) does the
same thing every time. The query is the one that built the dataset; the checks
are stricter than the hand-run ones (below), and the dataset's 35 pass them.

A capture is merged only if `rwpipe.identity.judge()` accepts it - the same
rule `verify_archives.py` applies to every stored page, so a capture merged
here is one the verifier keeps. In short: not an error page (origin-error
text, or a short error title such as "Error | Sage", "Page Unavailable", "DOI
Not Found"); and it says which document it is - the DOI its <head> declares
must be the one requested (or a recorded alias), and a retraction notice saved
in place of the article, which declares the notice's DOI, is kept apart as
`other-doi`; where it declares none, a DOI standing for one paper must have
an informative page title matching that paper via `fetch.compare_titles()`
(0.55 similarity, with its explicit title-containment exception). Printed DOIs
and titles in the body are not enough to identify a generic-label page.

A DOI found anywhere in the file is not used. In an Archive copy it proves
nothing: the Archive's toolbar and the MHTML headers carry the requested
address, DOI included, 13-14 times whatever page came back. Two adversarial
reviews on 7 October shaped the rule. The first refuted the version that let
the DOI anywhere decide when the page title is a label: it accepted SAGE
"Error | Sage" pages ("Publication not found: <DOI>") and notices saved in
place of their article. The second refuted the next version: reading metadata
from the whole page let an AME article pass as its own retraction notice,
whose DOI it carries after </head>; and a title match at 0.30, against the
best of a mass notice's hundreds of titles, passed 27.5% of deliberately
mismatched pairs (0.55 passes none of them). The 35 Archive pages in the
dataset all pass this rule. The 10 the hand-run merge of 3 September refused
cannot be re-judged: `collect_wayback.py` and the teammates' imports wrote to
the same path, and the teammates' pages, imported later, replaced those
files. All 10 documents are held through the teammates' collections.

One that fails is written back (`wrong-article`, `other-doi`, `error-page`,
`unreadable`) so the next run does not examine it again.

Merged documents are marked `source_kind='wayback'` and `capture_format='html'`
(as the dataset's 35 are) because they are not the publisher's live page: they
are the Archive's copy as it stood on the snapshot date, stored in
`oa_version` ("snapshot 20240213"). A snapshot taken before the retraction is
still the article but carries no notice, and Stage 3 must be able to tell. The
document points at the capture `collect_wayback.py` saved
(`<sha1>.wayback.mhtml.gz`, Chromium's MHTML of the snapshot); readers sniff
the content, so Stage 2 needs no special case.
"""
from __future__ import annotations

import argparse
import gzip
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console

from rwpipe import db, identity, mhtml, storage

console = Console()
MIN_TITLE_SIMILARITY = identity.MIN_TITLE_SIMILARITY

_STORED_CANDIDATES = """
   SELECT w.doi, w.path, w.snapshot_date, w.snapshot_url, d.doi_prefix
     FROM wayback_captures w
     JOIN documents d ON d.doi = w.doi
    WHERE w.path IS NOT NULL AND w.path <> ''
      AND COALESCE(d.usable, 0) = 0"""
CANDIDATES = _STORED_CANDIDATES + " AND w.status = 'ok'"
ALIAS_CANDIDATES = _STORED_CANDIDATES + """
      AND w.status = 'other-doi'
      AND EXISTS (SELECT 1 FROM doi_corrections c
                   WHERE lower(c.doi) = lower(w.doi)
                      OR lower(c.corrected_doi) = lower(w.doi))"""

def judge(html: str, doi: str, titles: list[str], journal: str | None = None,
          papers: int = 1, aliases=(), kind=None) -> tuple[str, float | None]:
    """'ok', 'error-page', 'other-doi' or 'wrong-article', with the title
    similarity when the title decided (else None): `identity.judge()`.

    'other-doi' is a page whose metadata names a different DOI: usually a
    notice saved in place of its article, sometimes the same paper under an
    older or alias DOI (Elmer Press, Dove). It is not merged; the status keeps
    it apart for review. A notice retracting 659 papers stands for 659 titles,
    and some unrelated page will resemble one of them (review of 7 October:
    judged against the best of several hundred titles, 24 of 27 unrelated
    pages passed), so only a DOI standing for one paper is judged by its title
    at all."""
    v = identity.judge(html, doi, titles, journal, papers, aliases, kind=kind)
    return v.verdict, v.similarity


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="check, change nothing")
    args = ap.parse_args()

    if args.dry_run:
        from verify_archives import connect_read_only
        conn = connect_read_only()
    else:
        conn = db.connect()
    have = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='wayback_captures'").fetchone()
    if not have:
        console.print("no Internet Archive captures yet - run collect_wayback.py first")
        return 0
    rows = conn.execute(CANDIDATES).fetchall()
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='doi_corrections'").fetchone():
        # A recorded, reviewed alias changes identity evidence. Rejudge only
        # those cached other-doi results; the judge still decides usability.
        # Keep the capture's previous rejection status as historical evidence.
        rows += conn.execute(ALIAS_CANDIDATES).fetchall()
    if not rows:
        console.print("[green]nothing to merge[/green]")
        return 0

    records, aliases = identity.load_context(conn)
    roles = identity.load_roles(conn)
    merged = rejected = unreadable = 0
    for r in rows:
        p = storage.resolve(r["path"])
        try:
            raw = gzip.open(p, "rb").read() if str(p).endswith(".gz") else p.read_bytes()
            doc = mhtml.parse(raw)
            if not doc.ok:
                raise ValueError(doc.error or "unreadable capture")
            html = doc.html
        except Exception:                                      # noqa: BLE001
            unreadable += 1
            if not args.dry_run:
                conn.execute("UPDATE wayback_captures SET status='unreadable' WHERE doi=?",
                             (r["doi"],))
            continue

        titles, journal, papers = records.get(r["doi"].lower(), ([], None, 1))
        verdict, sim = judge(html, r["doi"], titles, journal, papers,
                             aliases.get(r["doi"].lower(), ()), roles.get(identity.norm_doi(r["doi"])))
        if verdict != "ok":
            rejected += 1
            score = "" if sim is None else f"  title {sim:.2f}"
            console.print(f"  {verdict}  {r['doi']}{score}")
            if not args.dry_run:
                conn.execute("UPDATE wayback_captures SET status=? WHERE doi=?",
                             (verdict, r["doi"]))
            continue

        merged += 1
        console.print(f"  merged  snap {r['snapshot_date'] or '?'}  {r['doi']}")
        if args.dry_run:
            continue
        # The document points at the Archive capture itself: a second copy
        # would double the storage and leave the capture in the archive index
        # as an orphan "rejected" file beside the usable copy.
        conn.execute(
            """UPDATE documents
                  SET status='done', usable=1, capture_format='html',
                      mhtml_path=?, mhtml_bytes=?, gz_bytes=?,
                      final_url=?, source_kind='wayback', oa_host='web.archive.org',
                      oa_version=?, error=NULL, fetched_at=datetime('now')
                WHERE doi=?""",
            (storage.rel(p), len(raw), p.stat().st_size, r["snapshot_url"],
             "snapshot " + str(r["snapshot_date"] or "?"), r["doi"]))

    if not args.dry_run:
        conn.commit()
    verb = "would merge" if args.dry_run else "merged"
    console.print(f"\n[bold]{verb} {merged}[/bold] of {len(rows)} captures"
                  f"  (rejected {rejected}, unreadable {unreadable})")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
