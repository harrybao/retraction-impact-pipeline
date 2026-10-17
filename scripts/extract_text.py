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
# 7: four withdrawal wordings the cue list did not know, and a JATS notice
# that lives in the abstract. Found because 182 XML captures extracted to
# nothing - not "thin", nothing - and every one of them turned out to contain
# a real statement: "the publisher regrets that this article has been
# temporarily removed", "this document has been archived because it contains
# outdated information", "withdrawn at the request of the author(s)".
#
# The failures were the visible part. Measured across the whole table, 502
# documents carried one of those phrases and 395 had no notice extracted, so
# the gap was never confined to the documents that failed loudly.
#
# Re-running everything rather than only the affected rows: 9,850 documents
# once sat under version 6 with different rules from the other 60,366, and one
# version number covering two rule sets is the defect this project spent a day
# fixing elsewhere.
#
# v8 (2026-09-08): a body that opens with the site's own interface is emptied
# rather than kept. 148 documents had the interface as their entire body - 70
# Springer cookie notices, every one exactly 631 characters, and 78 Wiley
# citation-manager panels. The first attempt put these in the truncation rule
# and made things worse (a Wiley body went from 766 characters of boilerplate
# to 2,130), because that rule's marker sat at position 0 and the guard against
# emptying a body correctly refused the cut. It is a judgement about what the
# whole body is, so it belongs after the cut, not inside it.
#
# Measured by diffing v8 against the frozen v7: exactly 148 bodies emptied,
# 88,691 byte-identical, nothing else touched. 82 rows also lost their notice
# - all 82 had notice_method='body-is-notice', so the "retraction notice" on
# those documents was the cookie banner itself. Losing them is the fix working,
# not a regression. (An earlier note here claimed 3 of 6 sampled documents
# gained a notice; that was read off new non-zero values without comparing
# them to v7, and the real figure is 0 gained.)
#
# v9 (2026-09-10): the client's own IP address is redacted out of extracted
# text. Publishers echo the visiting address into their page furniture and the
# extractor was storing it as content: 142 documents across 7 hosts carried
# this machine's address, and 77 of those had it inside a *retraction notice* -
# a field published in stage2_notices.csv.gz, which was being uploaded to the
# group's repository when a pre-push scan of the built archives caught it.
#
# The first rule written for this redacted every IPv4 and was wrong. Measuring
# first: 564 documents contain an IPv4-shaped string and only 142 are an
# address. The remaining 422 are Enzyme Commission numbers - 3.2.1.4 is
# cellulase, 1.15.1.1 is superoxide dismutase - in a corpus that is mostly
# biomedical. A blanket rule would have silently deleted enzyme identifiers
# from 422 papers to fix 142, which is the same class of quiet damage this
# project keeps finding. The rule that shipped needs a cue on the line ("IP
# address", "IP-Adresse", "PubFactory") before it treats a match as an
# address, and always redacts the two addresses this project exits from.
#
# The IP was only the tracer. Chasing it found two larger faults in the same
# 142 documents, and v9 carries all three:
#
# * **Consent dialogues were being stored as retraction notices.** 77 Thieme
#   notices sat at the 3,000-character cap; the statement itself was about 250
#   characters and the rest was a OneTrust cookie dialogue plus a
#   related-articles rail carrying *other papers' titles and authors*. Anyone
#   doing entity extraction on the notices would attribute those authors to
#   this retraction. Consent widgets are now removed from the DOM before any
#   text is taken, which drops those four documents from 3,000 characters to
#   409-1,550.
#
#   The first version of that rule also dropped <footer>, <nav> and <ins>. It
#   was A/B-tested against the current extractor on 300 captures before
#   shipping and it emptied 6 real notices and halved 2 more, all Springer or
#   Nature - <ins> means "inserted into a published document", which is what a
#   retraction notice is, and Springer uses it correctly while Thieme uses it
#   for the rail. Narrowed to consent widgets alone the same test is 300/300
#   byte-identical. See test_redaction.py, which keeps those counter-examples.
#
# * **9 captures were error pages counted as articles.** liebertonline.com
#   answered HTTP 200 with a Cloudflare 523, and all 9 were stored with 232
#   identical bytes of "Origin is unreachable" as their body, `usable = 1`,
#   verdict `doi-only`. The DOI is in the URL the error page was served at, so
#   the identity check passed it - exactly the hole that let 10,049
#   ScienceDirect "problem providing the content" captures through. The
#   verifier now tests for an error page before the DOI is allowed to vouch
#   for anything, and coverage falls by those 9.
#
# v10 (2026-09-15): the notice is told apart from the article, and read from
# where the statement begins to where the site's own furniture starts. Every
# rule was measured on the frozen v9 table first, then run against the stored
# v9 rows on the same seeded sample of about 1,590 documents - four rounds,
# until a round changed nothing it did not also explain.
#
# Measured on v9:
# * 5,160 notice documents were graded `full`. A document whose only role is
#   the notice is now `notice-only` or `thin`, never `full`.
# * 5,272 notices (6.4%) sat at the 3,000-character cap, run on through Wiley
#   author lists, Spandidos citation widgets and a Cureus download form. The
#   walk now ends at a known piece of furniture, but only when no cue or reason
#   wording follows it anywhere below: a cue follows "First Published online:"
#   on 357 stored notices.
# * 1,786 documents stated a retraction in the active voice ("the Editors
#   retract this article") and had no notice at all.
# * 14,649 of 59,309 page-text notices (24.7%) began mid-sentence, because the
#   page became text with a line break at every link and italic. Page text is
#   now one line per block, and the <head> is left out: its <title> began
#   17,807 notices (30.0%).
# * 352 Elsevier notice documents with a body had no notice: that route alone
#   had no whole-page fallback. The other way round, 1,489 pages that are also
#   the article had their whole text stored as the notice; now only a page that
#   is purely the notice may stand as its own notice.
#
# What the sample rounds found, and what changed because of them:
# 1. The active-voice cue moved notices to their final paragraph and lost the
#    reason above it (PLOS, Dovepress, Spandidos). v9 had kept those openings
#    only because no cue matched and the whole page became the notice. A
#    look-back now reaches the statement's opening - never so far that the
#    decision itself would fall outside the budget.
# 2. "This article was retracted on 10 April 2026" is a status line Springer and
#    Nature print above the abstract. As a cue it started 79 of 1,590 sampled
#    notices there and walked into the abstract. A dated form counts only with a
#    reason after the date.
# 3. Springer Nature's consent dialog was missing from the consent selectors and
#    sat inside 6,808 v9 notices. Removing it also reaches a body built from the
#    page's own text when the article extractor finds too little: 1 body of the
#    1,590 changed, and the difference is the dialog's own lines.
# 4. Springer Nature's editor's note for an open investigation, and statements
#    that only the body extractor could see, are now read.
#
# Final sample against v9 (data/reports/v10-ab.csv): documents with a statement
# and no notice, 199 of 200 gained one; Elsevier notices with none, 91 of 98;
# capped notices in the capped stratum, 300 -> 197. Known costs: a non-English
# statement on a page that is also the article is no longer kept (the cues are
# English), and a PLOS notice whose issue list is longer than the budget keeps
# its decision rather than its opening.
#
# v11: a <select> dropdown is left out of page text. Diffing the full v10
# table against v9 found 215 of 589 European Review notices starting at a
# category menu whose options include "Expression of concern"; notices
# carrying the statement fell from 422 to 226. data/reports/v11-select-fix-check.csv
# v12: six defects the 2026-09-16 audit of v11 measured, all in the notice.
# A statement in the body is preferred over a page heading that never says the
# paper was withdrawn (IEEE 7,629 documents, 618 elsewhere); a PDF's wrapped
# lines are joined before the cue search (IOP 839); Taylor & Francis' article
# rail and Wiley's figure rail end a notice (777 and 1,231); literal
# backslash-n in page text becomes a line break (Cureus 222); Wiley's chat
# widget alone is not a notice (37); and the cue list gains IOP's long
# parenthesis, Springer's German wording and "retracted as requested".
# v13: v12's unescaping was too broad. A LaTeX command also begins with a
# backslash and a letter, so \nabla, \nu and \neq became "\ abla", "\ u" and
# "\ eq" in the mathematics papers among the 322 bodies v12 rewrote. An escaped
# break is now only one that no lowercase letter follows.
# v14: v13 also treated a lone backslash-r as an escaped break, so \rho,
# \right and \rangle became " ho", " ight" and " angle" in 256 mathematics
# bodies (Springer 171, Nature 70). Only the \r\n pair is an escape now.
# v15 excludes experimental sampling and participant withdrawals as notice cues.
EXTRACTOR_VERSION = 15


def targets(conn, limit, redo):
    columns = {r[1] for r in conn.execute("PRAGMA table_info(archive_checks)")}
    reviewed = "matched_role" in columns
    role = "a.matched_role" if reviewed else "NULL"
    join = "LEFT JOIN archive_checks a ON a.doi=d.doi" if reviewed else ""
    # A shared requested DOI does not prove the saved page supports both roles.
    sql = f"""SELECT d.doi, d.final_host, d.capture_format, d.mhtml_path,
                    COALESCE({role}, CASE
                      WHEN EXISTS(SELECT 1 FROM records r WHERE r.original_doi   = d.doi)
                       AND EXISTS(SELECT 1 FROM records r WHERE r.retraction_doi = d.doi)
                        THEN 'both'
                      WHEN EXISTS(SELECT 1 FROM records r WHERE r.retraction_doi = d.doi)
                        THEN 'retraction'
                      ELSE 'original' END) AS doc_role
             FROM documents d {join}
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

    v10: **except on a document that is only the notice.** Proportion still
    measured the page, and on a notice page everything that is not the
    statement is furniture - a journal menu, an author list, a related-articles
    rail. 5,160 retraction documents were graded `full`, so `full` meant "an
    article to analyse" for most of the table and "a notice with a long page
    around it" for those. A document whose only role is the notice is not an
    article at any length: `notice-only` if a statement was found, `thin` if
    not. Re-grading the stored v9 rows under this rule changes exactly those
    5,160 and nothing else; the 277 of them with no statement were Elsevier
    notices, which get one from the fallback this version adds. A page that is
    both the article and its own notice ('both') still goes by proportion.
    """
    if e.error or not (e.body or e.notice):
        return "failed"

    if role == "retraction":
        return "notice-only" if e.notice else "thin"

    notice_led = (
        e.notice
        and role == "both"
        and (len(e.body) < MIN_BODY or len(e.notice) >= 0.5 * len(e.body))
    )
    if notice_led:
        return "notice-only"
    if len(e.body) >= MIN_BODY:
        return "full"
    if len(e.notice) >= 40:
        return "notice-only"
    return "thin"


def source_format(document, recorded: str | None) -> str | None:
    """A detected PDF must not inherit its mislabeled HTML container type."""
    return "pdf" if (document.charset or "").startswith("pdf-text/") else recorded


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--upgrade", action="store_true", help="explicitly replace text extracted under older rules")
    args = ap.parse_args()

    if config.DB_PATH.is_file() and not args.upgrade:
        import sqlite3
        wal = Path(str(config.DB_PATH) + "-wal")
        if wal.exists() and wal.stat().st_size:
            console.print("[red]A non-empty database WAL prevents a safe version preflight. Close other writers first.[/red]")
            return 1
        with sqlite3.connect(config.DB_PATH.resolve().as_uri() + "?mode=ro&immutable=1", uri=True) as old:
            if old.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='document_text'").fetchone():
                columns = {r[1] for r in old.execute("PRAGMA table_info(document_text)")}
                needs_upgrade = old.execute(
                    "SELECT 1 FROM document_text WHERE COALESCE(extractor_version,0) != ? LIMIT 1",
                    (EXTRACTOR_VERSION,)).fetchone() if "extractor_version" in columns else old.execute(
                        "SELECT 1 FROM document_text LIMIT 1").fetchone()
                if needs_upgrade:
                    console.print("[red]Existing text uses older extraction rules. Back up first and pass --upgrade explicitly.[/red]")
                    return 1
    conn = db.connect()
    db.init(conn)

    # Drop text for documents that are no longer usable, before extracting.
    #
    # `targets()` selects on `usable = 1`, so a document that loses that flag
    # is simply never visited again - and its old row stays in document_text
    # for good, at whatever extractor version was current when it was written.
    # After the v9 run this left 9 orphans: 88,843 text rows against 88,834
    # usable documents, 4 of them still at v8 because they had not been
    # selected since. Those 4 were the reason a redacted address was still
    # present in the corpus after a run whose whole purpose was removing it -
    # the rule had been fixed and the rows it applied to were out of its reach.
    #
    # The same shape as every stale-mapping bug in this pipeline: the writer
    # was updated and nothing was responsible for deletion.
    stale = conn.execute(
        """DELETE FROM document_text
           WHERE doi IN (SELECT t.doi FROM document_text t
                         JOIN documents d ON d.doi = t.doi
                         WHERE COALESCE(d.usable, 0) <> 1)""").rowcount
    if stale:
        conn.commit()
        console.print(f"[yellow]dropped {stale:,} text rows whose document is "
                      f"no longer usable[/yellow]")

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
            # Metadata-only XML with the article in a PDF beside it: take the
            # body from the PDF. Only IOP documents have one - see
            # rwpipe/pdftext.py for why this needed no version bump.
            if not e.error and not (e.body or "").strip():
                from rwpipe import pdftext
                pdftext.fill_from_pdf(conn, e, r["doi"], r["doc_role"])

        q = classify(e, r["doc_role"])
        stats[q] += 1
        conn.execute(
            """INSERT OR REPLACE INTO document_text
               (doi, doc_role, host, capture_format, source_path, page_title,
                title, abstract, body, notice, notice_method, references_n,
                body_chars, method, mime_parts, charset, quality,
                extractor_version, error, extracted_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'))""",
            (r["doi"], r["doc_role"], r["final_host"], source_format(doc, r["capture_format"]),
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
