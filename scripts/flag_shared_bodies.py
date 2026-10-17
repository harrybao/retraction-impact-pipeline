"""Mark the documents whose extracted body is not their own article.

    python scripts/flag_shared_bodies.py --dry-run
    python scripts/flag_shared_bodies.py

12.4% of extracted bodies are byte-identical to at least one other document.
That is mostly not a defect - it is what several publishers do - but it has to
be visible before Stage 3, because a word count, a topic model or an LLM asked
to read "the article" would otherwise see IEEE's standard retraction wording
9,027 times and treat it as 9,027 independent texts.

Four different things produce a shared body, and only the analysis knows which
of them it can tolerate, so they are labelled rather than deleted:

    notice-replaces-article   the publisher removed the paper and left a
                              notice in its place. IEEE does this on every
                              retraction; Elsevier does it with its withdrawal
                              policy text. The article text does not exist
                              anywhere - 0 of these have an open-access copy,
                              a PMC id, or a pre-retraction Wayback snapshot.
    bulk-notice               one notice covering many papers, filed under
                              each of their DOIs. Real content, but it is
                              about other papers as much as this one.
    interface-not-article     the page's own furniture extracted as the
                              article - the same defect fixed in extractor v8,
                              in a variant no rule reaches yet. 41 documents,
                              which does not justify a v9 and a four-hour
                              re-extraction; the label is what Stage 3 needs.
    shared-body               everything else - worth a look before use.

The first pass put 829 documents in `shared-body` and found only 4 bulk
notices. Reading those 829 is what produced the bulk openings and the interface
variant above, and moved 326 of them into a class that names them.

Written to its own table rather than a column on `document_text`, because that
table is rebuilt with INSERT OR REPLACE on every extractor run and a column it
does not know about would be silently emptied. That is the failure this project
has already paid for twice.

Idempotent, and safe to re-run after any extraction.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console

from rwpipe import config, db

console = Console()

MIN_BODY = 200          # below this a repeat is noise, not a shared document

# Wording a publisher uses when the article itself has been taken down.
REPLACED = re.compile(
    r"^\s*(?:"
    r"abstract:\s*retracted"
    r"|this article has been retracted: please see elsevier policy"
    r"|this article has been (?:withdrawn|removed)"
    r"|notice of retraction"
    r"|this temporary expression of concern relates to the above article"
    r")", re.I)

# A bulk notice either lists the papers it covers, or says so in its opening.
# The count-based rule alone found 4 of them; these are the openings the 829
# unclassified documents turned out to have.
BULK = re.compile(r"retracted:|has been retracted at the request", re.I)
BULK_MIN = 5
BULK_OPENING = re.compile(
    r"^\s*(?:"
    r"the following articles have been retracted"
    r"|withdrawn abstracts"
    r"|the editorial office has been made aware"
    r"|the editors?-in-chief have retracted"
    r"|the concern relates to the provenance of this article"
    r")", re.I)

# Interface text that became the body, same class as the 148 fixed in
# extractor v8 but a variant no rule reaches yet. Flagged rather than
# extracted again: 39 documents do not justify a v9 and a four-hour re-run,
# and the label is what Stage 3 actually needs.
INTERFACE = re.compile(
    r"^\s*(?:"
    r"when publishers deposit their content metadata to crossref"
    r"|if you have the appropriate software installed"
    r"|we use essential cookies"
    r")", re.I)

REPORTS = config.REPORTS          # honours RWPIPE_DATA


def classify(body: str) -> str:
    if INTERFACE.match(body):
        return "interface-not-article"
    if REPLACED.match(body):
        return "notice-replaces-article"
    if BULK_OPENING.match(body) or len(BULK.findall(body)) >= BULK_MIN:
        return "bulk-notice"
    return "shared-body"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = db.connect()
    conn.execute("PRAGMA busy_timeout = 30000")

    console.print("[dim]grouping bodies…[/dim]")
    groups: dict[str, list[str]] = defaultdict(list)
    text: dict[str, str] = {}
    for doi, body in conn.execute(
            "SELECT doi, body FROM document_text "
            "WHERE LENGTH(COALESCE(body,'')) >= ?", (MIN_BODY,)):
        h = hashlib.sha1(body.encode("utf-8", "ignore")).hexdigest()
        groups[h].append(doi)
        text.setdefault(h, body)

    shared = {h: d for h, d in groups.items() if len(d) > 1}
    rows = []
    for h, dois in shared.items():
        kind = classify(text[h])
        for doi in dois:
            rows.append((doi, h, len(dois), kind))

    stats = Counter(k for _, _, _, k in rows)
    console.print(f"\n[bold]{len(rows):,} documents[/bold] share a body with at "
                  f"least one other, in {len(shared):,} groups")
    for k, n in stats.most_common():
        console.print(f"   {k:<26}{n:>7,}")

    if args.dry_run:
        console.print("[dim]dry run - nothing written[/dim]")
        return 0

    conn.execute("""CREATE TABLE IF NOT EXISTS shared_bodies (
                        doi TEXT PRIMARY KEY, body_sha1 TEXT, group_size INTEGER,
                        kind TEXT, flagged_at TEXT)""")
    conn.execute("DELETE FROM shared_bodies")
    conn.executemany(
        "INSERT INTO shared_bodies (doi, body_sha1, group_size, kind, flagged_at) "
        "VALUES (?,?,?,?, datetime('now'))", rows)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_shared_kind ON shared_bodies(kind)")
    conn.commit()

    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / "shared-bodies.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["doi", "body_sha1", "group_size", "kind", "host", "doc_role"])
        for doi, h, n, kind in sorted(rows, key=lambda r: (-r[2], r[0])):
            meta = conn.execute(
                "SELECT host, doc_role FROM document_text WHERE doi = ?",
                (doi,)).fetchone()
            w.writerow([doi, h, n, kind, meta[0] if meta else "", meta[1] if meta else ""])
    console.print(f"\n[dim]written to {out}[/dim]")

    console.print("\n[bold]what Stage 3 should exclude from body-text analysis[/bold]")
    n = conn.execute("SELECT COUNT(*) FROM shared_bodies "
                     "WHERE kind = 'notice-replaces-article'").fetchone()[0]
    tot = conn.execute("SELECT COUNT(*) FROM document_text").fetchone()[0]
    console.print(f"   {n:,} of {tot:,} ({n / tot * 100:.1f}%) - the body is the "
                  f"publisher's notice, not the paper")
    console.print("\n   SELECT t.* FROM document_text t")
    console.print("     LEFT JOIN shared_bodies s ON s.doi = t.doi")
    console.print("    WHERE s.kind IS NOT 'notice-replaces-article'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
