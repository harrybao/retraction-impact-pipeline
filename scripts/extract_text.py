"""Stage 2 - turn the archived pages into queryable text.

    python scripts/extract_text.py --limit 200     # sample first
    python scripts/extract_text.py                 # all of them
    python scripts/extract_text.py --redo          # re-extract everything

Reads only archives that passed identity verification (`usable = 1`). The
10,245 that did not are publisher refusal pages and wrong-article captures;
they are well-formed HTML and would extract into plausible text saying nothing
about the paper they are filed under.

Resumable: a DOI already in `document_text` is skipped.

Quality is recorded per document rather than inferred later:

    full         a body long enough to analyse
    notice-only  no article body, but the withdrawal statement was found -
                 which for a retraction notice is the whole point, and those
                 are two paragraphs by nature
    thin         something was extracted, too little to use
    failed       the archive could not be parsed at all
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console

from rwpipe import config, db, extract, mhtml

console = Console()

MIN_BODY = 400          # below this an "article" is not an article

# Bump when the extraction rules change. Stored per row so a fix can be
# re-applied to the documents it affects instead of all of them.
# 4: the extractor can now see an article that was shipped as a JavaScript
# string. Cureus sends 334 KB in which the whole paper is escaped as
# <p> inside a script; stripping scripts left 781 characters of
# navigation menu, which is indistinguishable from a refusal. 223 captures were
# nearly written off on that basis.
#
# Everything without that signal extracts byte-identically, so this re-run is
# expected to change almost nothing - and that is the point of running it. Forty
# random captures were sampled by hand and none carried the signal; forty is not
# evidence about seventy thousand, and the only way to find the rest is to look
# at all of them.
# 5: publisher furniture is stripped from the body and the notice. 16,433 rows
# (23.4%) carried cookie consent panels, related-article rails, IEEE customer
# service numbers and copyright footers inside the extracted text. Stage 3 has
# to compare a retraction notice against what the news said about the paper,
# and a notice with two thousand characters of cookie policy in it is not a
# notice. Measured on a 150-document sample: contamination 27% -> 4%, at a cost
# of 0.35% of the body text.
#
# This time the previous version's rows are not overwritten blind - v4 was, and
# the two versions can no longer be compared document by document.
# 6: the login form comes out of the notice, and the Wolters Kluwer footer
# out of the body. Wiley prints its site footer and a
# password-reset flow immediately after the retraction statement, and the walk
# forward from the cue carried both: 2,115 notices contained a password reset,
# 1,638 a sign-in prompt, 764 an institutional-login block.
#
# The footer was found by asking a different question than "did the sample
# look clean": count one unambiguous string across the whole table. 285
# documents still carried "we use cookies", 283 of them from one host, and it
# had survived two existing rules by one word each - "Copyright (c) 2026" puts
# a symbol where the pattern wanted the year, and Ovid writes "including those
# for text and data mining" where Wiley writes "including rights for". On the
# 285 the fix removes 49% of the body; on 300 controls, 0.24%, emptying none.
# Measurements in data/reports/ovid-footer-fix.csv.
#
# What is NOT fixed, deliberately: 5,560 notices still sit at exactly the
# 3,000-character budget, cut by the limit rather than by a boundary. A
# shape-based stop for the related-articles rails that fill them was tried and
# reverted - measured on twelve, it emptied three notices completely and cut a
# fourth to 32 characters while leaving six still capped. Truncation that is
# visible in the data beats a rule that removes more signal than noise.
EXTRACTOR_VERSION = 6


def targets(conn, limit, redo):
    sql = """SELECT d.doi, d.final_host, d.capture_format, d.mhtml_path,
                    -- 13,655 records give the same DOI in both columns, so
                    -- one page IS the article and its own notice. Calling it
                    -- 'retraction' threw away half of what it is, and Stage 3
                    -- needs to know which documents carry both.
                    CASE
                      WHEN EXISTS(SELECT 1 FROM records r WHERE r.original_doi   = d.doi)
                       AND EXISTS(SELECT 1 FROM records r WHERE r.retraction_doi = d.doi)
                        THEN 'both'
                      WHEN EXISTS(SELECT 1 FROM records r WHERE r.retraction_doi = d.doi)
                        THEN 'retraction'
                      ELSE 'original' END AS doc_role
             FROM documents d
             WHERE d.usable = 1 AND d.mhtml_path IS NOT NULL"""
    if not redo:
        # Skip only what succeeded under the current rules. Skipping every DOI
        # already present preserved `failed` and `thin` rows for good, and kept
        # notices extracted by a version whose selector swallowed whole
        # articles - a resume that protects bad output is not a resume.
        sql += f""" AND d.doi NOT IN (
                        SELECT doi FROM document_text
                        WHERE quality IN ('full','notice-only')
                          AND extractor_version >= {EXTRACTOR_VERSION})"""
    sql += " ORDER BY d.shard"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


def classify(e, role: str) -> str:
    """What kind of document came out.

    Body length was tested first, so any retraction notice longer than 400
    characters was labelled `full` - of 3,287 retraction documents only 7 were
    reaching `notice-only`, and the grade that exists specifically to mark
    "this is the withdrawal statement, not an article" was doing nothing.

    What separates them is not length but proportion: a notice page is mostly
    notice, an article that carries a retraction banner is mostly article.
    """
    if e.error or not (e.body or e.notice):
        return "failed"

    notice_led = (
        e.notice
        and role in ("retraction", "both")
        and (len(e.body) < MIN_BODY or len(e.notice) >= 0.5 * len(e.body))
    )
    if notice_led:
        return "notice-only"
    if len(e.body) >= MIN_BODY:
        return "full"
    if len(e.notice) >= 40:
        return "notice-only"
    return "thin"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--redo", action="store_true")
    args = ap.parse_args()

    conn = db.connect()
    db.init(conn)
    rows = targets(conn, args.limit, args.redo)
    if not rows:
        console.print("[green]nothing to extract[/green]")
        return 0

    console.print(f"[bold]extracting {len(rows):,} documents[/bold]\n")
    stats: Counter = Counter()
    started = time.time()

    for i, r in enumerate(rows, 1):
        doc = mhtml.read(config.DATA / r["mhtml_path"])
        if not doc.ok:
            e = extract.Extracted(error=doc.error or "unreadable")
        else:
            e = extract.extract(doc.html, kind=r["doc_role"])

        q = classify(e, r["doc_role"])
        stats[q] += 1
        conn.execute(
            """INSERT OR REPLACE INTO document_text
               (doi, doc_role, host, capture_format, source_path, page_title,
                title, abstract, body, notice, notice_method, references_n,
                body_chars, method, mime_parts, charset, quality,
                extractor_version, error, extracted_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'))""",
            (r["doi"], r["doc_role"], r["final_host"], r["capture_format"],
             r["mhtml_path"], doc.subject, e.title, e.abstract, e.body,
             e.notice, e.notice_method, e.references, len(e.body), e.method,
             doc.parts, doc.charset, q, EXTRACTOR_VERSION, e.error))

        if i % 50 == 0:
            conn.commit()
        if i % 1000 == 0:
            el = max(time.time() - started, 1)
            console.print(f"[dim]{i:>7,}/{len(rows):,}[/dim] "
                          f"full {stats['full']:,}  notice {stats['notice-only']:,}  "
                          f"thin {stats['thin']:,}  failed {stats['failed']:,}  "
                          f"| {i/el*3600:,.0f}/h")
    conn.commit()

    n = sum(stats.values()) or 1
    console.print(f"\n[bold]{n:,} documents[/bold]")
    for k in ("full", "notice-only", "thin", "failed"):
        console.print(f"  {k:<12} {stats[k]:>7,}  ({stats[k]/n*100:.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
