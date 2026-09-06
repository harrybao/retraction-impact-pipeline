"""Is each archived page actually the paper it claims to be?

    python scripts/verify_archives.py --limit 500     # sample
    python scripts/verify_archives.py                 # all of them

Until now a capture was accepted on **size**: over 50 KB of HTML, over 40 KB of
MHTML, not a known challenge page. That test cannot tell one article from
another, and it has already been fooled once - 297 Elsevier waypoint pages
rendered at almost exactly the 50 KB threshold and were filed as articles while
the stored archive contained only "Subject: Redirecting".

A teammate's crawler verifies differently: it checks that the DOI actually
appears in the captured file and that the page title resembles the record
title. That is a test of *identity* rather than of *bulk*, and it is strictly
better. This applies the same check to the 79,081 pages already archived.

Two decoding details matter, and getting either wrong invents failures:

* MHTML stores the document quoted-printable, so a DOI can be split across
  lines by a soft break (`=\r\n`). Searching the raw bytes for the DOI then
  misses a DOI that is plainly there. Soft breaks are removed before matching.
* The same DOI is written `10.1016/J.FOO` and `10.1016/j.foo`, and publishers
  percent-encode slashes in URLs. Matching is case-insensitive and tries the
  encoded form too.

A page that fails both checks is not automatically wrong - some publishers
render the DOI as an image, and some titles differ legitimately. The output is
a ranked list to inspect, not a verdict.
"""
from __future__ import annotations

import argparse
import gzip
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console

from rwpipe import config, db, fetch

console = Console()

TAG = re.compile(rb"<[^>]{1,4000}>")
WS = re.compile(r"\s+")


def readable(path: Path) -> str:
    """Decoded text of a capture, with MHTML soft line breaks removed."""
    try:
        raw = gzip.open(path, "rb").read()
    except Exception:                                      # noqa: BLE001
        return ""
    # quoted-printable soft breaks: "=\r\n" and "=\n" mean "no break here"
    raw = raw.replace(b"=\r\n", b"").replace(b"=\n", b"")
    try:
        return raw.decode("utf-8", "ignore")
    except Exception:                                      # noqa: BLE001
        return ""


def title_of(text: str) -> str:
    m = re.search(r"<title[^>]*>(.{0,400}?)</title>", text, re.I | re.S)
    if not m:
        return ""
    t = TAG.sub(b" ", m.group(1).encode("utf-8", "ignore")).decode("utf-8", "ignore")
    return WS.sub(" ", t).strip()


def norm(s: str) -> str:
    s = re.sub(r"^\s*(retracted article|retracted|retraction(?: note| notice)?|"
               r"withdrawn|expression of concern|correction|corrigendum)\s*[:\-]?\s*",
               "", s or "", flags=re.I)
    s = re.sub(r"\s*[|\-–—]\s*[^|]{0,80}$", "", s)        # trailing journal name
    return WS.sub(" ", re.sub(r"[^\w\s]", " ", s.lower())).strip()


def has_doi(text: str, doi: str) -> bool:
    low = text.lower()
    d = doi.lower()
    return d in low or d.replace("/", "%2f") in low


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--min-similarity", type=float, default=0.55)
    args = ap.parse_args()

    conn = db.connect()
    # Results go to a table, not just the screen. The first run of this printed
    # a summary and eight examples and kept nothing, so identifying the 208
    # failures afterwards meant decompressing all 69,090 archives a second
    # time. A verification that cannot be queried later is half a verification.
    conn.execute("""CREATE TABLE IF NOT EXISTS archive_checks (
        doi TEXT PRIMARY KEY, host TEXT, gz_bytes INTEGER,
        doi_found INTEGER, title_similarity REAL, page_title TEXT,
        doc_role TEXT, role_ok INTEGER,
        verdict TEXT, checked_at TEXT)""")
    for col, decl in (("doc_role", "TEXT"), ("role_ok", "INTEGER")):
        try:
            conn.execute(f"ALTER TABLE archive_checks ADD COLUMN {col} {decl}")
        except Exception:                                  # noqa: BLE001
            pass
    conn.commit()
    # Two indexed lookups rather than one OR-join. The OR form cannot use an
    # index on either side, so SQLite scans all 71,698 records for each of the
    # 79,139 documents - the query never returns.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rec_odoi ON records(original_doi)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rec_rdoi ON records(retraction_doi)")
    sql = """SELECT d.doi, d.mhtml_path, d.capture_format, d.gz_bytes,
                    d.final_host,
                    COALESCE(
                      (SELECT r.title FROM records r WHERE r.original_doi = d.doi),
                      (SELECT r.title FROM records r WHERE r.retraction_doi = d.doi)
                    ) AS title
                    ,CASE WHEN EXISTS(SELECT 1 FROM records r
                                       WHERE r.retraction_doi = d.doi)
                          THEN 'retraction' ELSE 'original' END AS doc_role
             FROM documents d
             WHERE d.status = 'done' AND d.mhtml_path IS NOT NULL
             ORDER BY d.shard"""
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    rows = conn.execute(sql).fetchall()

    console.print(f"[bold]verifying {len(rows):,} archives[/bold]\n")

    ok = doi_only = title_only = neither = unreadable = 0
    suspects = []
    for i, r in enumerate(rows, 1):
        p = config.DATA / r["mhtml_path"]
        text = readable(p)
        if not text:
            unreadable += 1
            suspects.append((0.0, "unreadable", r["doi"], r["final_host"], ""))
            continue

        # Same three checks the crawler now applies, so an archive verified
        # here and a page accepted there mean the same thing.
        d_ok = fetch.contains_doi(text, r["doi"])
        page_title = fetch.page_title_of(text)
        t_ok, sim = fetch.title_matches(text, r["title"] or "",
                                        floor=args.min_similarity)
        role = r["doc_role"]
        r_ok = fetch.role_matches(text, role)

        # Role is recorded, not enforced: publishers name notices
        # inconsistently and DOI + title are the stronger evidence.
        if d_ok and t_ok:
            ok += 1; verdict = "ok" if r_ok else "ok-role-differs"
        elif d_ok:
            doi_only += 1; verdict = "doi-only"
        elif t_ok:
            title_only += 1; verdict = "title-only"
        else:
            neither += 1; verdict = "suspect"
            suspects.append((sim, "no doi, no title match", r["doi"],
                             r["final_host"], page_title[:70]))

        conn.execute(
            """INSERT OR REPLACE INTO archive_checks
               (doi, host, gz_bytes, doi_found, title_similarity, page_title,
                doc_role, role_ok, verdict, checked_at)
               VALUES (?,?,?,?,?,?,?,?,?, datetime('now'))""",
            (r["doi"], r["final_host"], r["gz_bytes"], int(d_ok),
             round(sim, 3), page_title[:200], role, int(r_ok), verdict))

        # And act on the verdict. This was the missing link in the pipeline:
        # the check ran, wrote its findings to `archive_checks`, and stopped -
        # while `documents.usable`, the flag Stage 2 selects on and every
        # coverage figure is built from, was set once by a throwaway script
        # that never entered the repository. A rebuild from an empty database
        # would therefore have produced 70,000 captures, 0 usable rows, and an
        # empty Stage 2, with nothing in the code to explain why.
        #
        # The rule, recovered from the flags the throwaway script left behind
        # rather than invented here: a capture counts when it carries its own
        # DOI **or** its title matches the paper. Either alone is evidence that
        # this is the right document; requiring both would discard 20,971
        # captures whose publisher does not print the DOI in the page, and
        # requiring only the DOI would discard 74 whose title matches while the
        # DOI appears in an image or a script.
        #
        # Reproduced against the existing flags: doi_found alone gives 69,890,
        # doi_found OR title gives 69,957, and 69,957 is what is in the
        # database. Role is deliberately not part of it - publishers name
        # notices inconsistently and 995 captures differ on role while being
        # unambiguously the right paper.
        conn.execute("UPDATE documents SET usable = ? WHERE doi = ?",
                     (1 if (d_ok or t_ok) else 0, r["doi"]))
        # Commit often and briefly. Holding one transaction across hundreds of
        # gzip reads locks the database for minutes at a time, and every other
        # process in the pipeline is a writer too.
        if i % 25 == 0:
            conn.commit()

        if i % 2000 == 0:
            conn.commit()
            console.print(f"[dim]  {i:,}/{len(rows):,}  ok {ok:,}  "
                          f"suspect {neither:,}[/dim]")

    conn.commit()
    n = len(rows) or 1
    console.print(f"\n[bold]{len(rows):,} archives[/bold]")
    console.print(f"  [green]DOI and title both match : {ok:,} ({ok/n*100:.1f}%)[/green]")
    console.print(f"  DOI matches only         : {doi_only:,} ({doi_only/n*100:.1f}%)")
    console.print(f"  title matches only       : {title_only:,} ({title_only/n*100:.1f}%)")
    console.print(f"  [red]neither matches          : {neither:,} ({neither/n*100:.1f}%)[/red]")
    console.print(f"  unreadable               : {unreadable:,}")

    if suspects:
        console.print("\n[bold]worst offenders by host[/bold]")
        from collections import Counter
        by_host = Counter(s[3] for s in suspects)
        for host, c in by_host.most_common(12):
            console.print(f"  {c:>5}  {host}")
        console.print("\n[dim]examples:[/dim]")
        for sim, why, doi, host, t in suspects[:8]:
            console.print(f"  [dim]{sim:.2f}[/dim] {doi:<34} {host:<26} {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
