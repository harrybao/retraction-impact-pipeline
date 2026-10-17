"""Is each stored page actually the document it is filed under?

    python scripts/verify_archives.py --dry-run       # judge every page, change nothing
    python scripts/verify_archives.py --dry-run --limit 500 # sample
    python scripts/verify_archives.py --apply              # changes documents.usable

Until now a capture was accepted on **size**: over 50 KB of HTML, over 40 KB of
MHTML, not a known challenge page. That test cannot tell one article from
another, and it has already been fooled once - 297 Elsevier waypoint pages
rendered at almost exactly the 50 KB threshold and were filed as articles while
the stored archive contained only "Subject: Redirecting".

A teammate's crawler verified differently: it checked that the DOI appears in
the captured file and that the page title resembles the record title. That is
a test of *identity* rather than of *bulk*, and this script applied it to every
stored page: a page counted when it carried its DOI anywhere **or** its title
matched.

**Since 13 October 2026 the rule is `rwpipe.identity.judge()`**, the one
`merge_wayback.py` applies to Internet Archive copies, so a page is judged the
same way whichever route brought it in. "The DOI appears anywhere" proved what
was *requested*, not what came back: an Archive copy repeats the requested
address 13-14 times in its toolbar and MHTML headers, and a live capture in its
own headers, so the old rule accepted SAGE "Error | Sage" pages and notices
saved in place of their article (review of 7 October: among 10,133 pages
accepted on the DOI alone with a label-only title, 21 declare another DOI and
10 are error pages). `identity.py` states the rule and why; `--dry-run`
measures what it changes before anything is written:

    python scripts/verify_archives.py --dry-run
        -> runs/checks/identity-recheck-<date>.csv, one row per stored page:
           old usable flag and verdict, new verdict, the evidence that decided
           it, and the DOI the document declares.

The dry run opens the database read-only and writes only that file. With
explicit --apply, `documents.usable` is set from the verdict (`ok` is usable, anything else
is not) and `archive_checks` records why.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import sqlite3
import sys
from collections import Counter
from datetime import date
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console

from rwpipe import config, db, fetch, identity, storage

console = Console()


def readable(path: Path) -> str:
    """Decoded text of a capture, with MHTML soft line breaks removed.

    Kept for `ab_title_rule.py` and `recheck_title_rule.py`, which import it.
    The verifier itself reads with `identity.read_capture()`, as the Archive
    merge does, and `identity.judge()` removes the soft breaks it needs to."""
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


COLUMNS = ("doi", "source_kind", "capture_format", "host", "doc_role",
           "old_usable", "old_verdict", "new_verdict", "new_usable", "changed",
           "reason", "declared_doi", "declared_relation",
           "old_title_similarity", "title_similarity", "page_title")
COLUMNS = (*COLUMNS, "matched_role")


def relation(declared: str | None, doi: str, pairs: dict, known: set) -> str:
    """How the DOI a page declares relates to the one it is filed under.

    `record-pair`: the other DOI of a record that holds the requested one - the
    notice saved in place of its paper, or the paper in place of its notice.
    `in-dataset`: another DOI Retraction Watch lists (a neighbouring article).
    `not-in-dataset`: a DOI the dataset does not know. Recorded for review;
    the verdict does not use it."""
    if not declared:
        return ""
    if " " in declared:
        return "several"
    want = identity.norm_doi(doi)
    if declared == want:
        return "own"
    if declared in pairs.get(want, ()):
        return "record-pair"
    return "in-dataset" if declared in known else "not-in-dataset"


def check(task: tuple) -> dict:
    """Judge one stored page. Runs in a worker process: everything it needs
    comes in the task, so no worker opens the database."""
    doi, rel_path, titles, journal, papers, aliases, role = task
    text = identity.read_capture(storage.resolve(rel_path))
    v = identity.judge(text, doi, titles, journal, papers, aliases, kind=role)
    title = identity.page_title(text) if text else ""
    sim = v.similarity
    if sim is None and title and titles:
        sim = max(fetch.compare_titles(title, t)[1] for t in titles)
    return {"verdict": v.verdict, "reason": v.reason, "declared": v.declared,
            "similarity": None if sim is None else round(sim, 3),
            "page_title": title[:200],
            "matched_role": v.matched_role,
            "doi_found": int(fetch.contains_doi(text, doi)) if text else 0,
            "role_ok": int(fetch.role_matches(text, role)) if text else 0}


def connect_read_only() -> sqlite3.Connection:
    # Immutable reads cannot update SQLite's WAL shared-memory file. Refuse a
    # live WAL rather than silently omitting committed rows from the snapshot.
    wal = Path(str(config.DB_PATH) + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise RuntimeError("dry run needs a checkpointed database (non-empty WAL)")
    conn = sqlite3.connect(config.DB_PATH.resolve().as_uri() + "?mode=ro&immutable=1",
                           uri=True, timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def has_table(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (name,)).fetchone() is not None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    # A merged batch is verified on its own. The unfiltered run re-derives
    # `usable` for every stored archive, and can move documents the batch
    # never touched.
    ap.add_argument("--source-kind", default=None,
                    help="only documents with this source_kind, e.g. teammate-tandf")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true", help="explicitly apply identity decisions to the database")
    mode.add_argument("--dry-run", action="store_true",
                    help="judge read-only and write the comparison CSV; change nothing")
    ap.add_argument("--out", type=Path, default=None,
                    help="where --dry-run writes its CSV "
                         "(default data/reports/identity-recheck-<today>.csv)")
    # 96,514 pages, 147 GB uncompressed: about 23 ms each on one core
    # (sample of 324, 13 October 2026), so 37 minutes serially.
    ap.add_argument("--workers", type=int, default=1)
    args = ap.parse_args()

    if args.dry_run:
        conn = connect_read_only()
    else:
        conn = db.connect()
        # Results go to a table, not just the screen. The first run of this
        # printed a summary and eight examples and kept nothing, so
        # identifying the 208 failures afterwards meant decompressing all
        # 69,090 archives a second time.
        conn.execute("""CREATE TABLE IF NOT EXISTS archive_checks (
            doi TEXT PRIMARY KEY, host TEXT, gz_bytes INTEGER,
            doi_found INTEGER, title_similarity REAL, page_title TEXT,
            doc_role TEXT, role_ok INTEGER,
            verdict TEXT, checked_at TEXT)""")
        for col, decl in (("doc_role", "TEXT"), ("role_ok", "INTEGER"),
                          ("reason", "TEXT"), ("declared_doi", "TEXT")):
            try:
                conn.execute(f"ALTER TABLE archive_checks ADD COLUMN {col} {decl}")
            except Exception:                              # noqa: BLE001
                pass
        if "matched_role" not in {r[1] for r in conn.execute("PRAGMA table_info(archive_checks)")}:
            conn.execute("ALTER TABLE archive_checks ADD COLUMN matched_role TEXT")
        conn.commit()

    old = ("a.verdict AS old_verdict, a.title_similarity AS old_sim"
           if has_table(conn, "archive_checks") else "NULL AS old_verdict, NULL AS old_sim")
    join = "LEFT JOIN archive_checks a ON a.doi = d.doi" if "a.verdict" in old else ""
    sql = f"""SELECT d.doi, d.mhtml_path, d.capture_format, d.source_kind, d.gz_bytes,
                     d.final_host, d.usable, {old}
                FROM documents d {join}
               WHERE d.status = 'done' AND d.mhtml_path IS NOT NULL
                 AND (:kind IS NULL OR d.source_kind = :kind)
               ORDER BY d.shard, d.doi"""
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    rows = conn.execute(sql, {"kind": args.source_kind}).fetchall()

    # Titles, journal and paper count per DOI, read once rather than three
    # subqueries per page. Requested roles also constrain reviewed aliases;
    # a notice substitution must not verify an original-paper capture.
    records, aliases = identity.load_context(conn)
    notices = {r[0].strip().lower() for r in conn.execute(
        "SELECT DISTINCT retraction_doi FROM records WHERE retraction_doi IS NOT NULL")
        if r[0] and r[0].strip()}
    originals = {r[0].strip().lower() for r in conn.execute(
        "SELECT DISTINCT original_doi FROM records WHERE original_doi IS NOT NULL") if r[0] and r[0].strip()}
    pairs: dict[str, set[str]] = {}
    for o, n in conn.execute("SELECT original_doi, retraction_doi FROM records"):
        o, n = identity.norm_doi(o), identity.norm_doi(n)
        if o and n and o != n:
            pairs.setdefault(o, set()).add(n)
            pairs.setdefault(n, set()).add(o)
    known = set(pairs) | {identity.norm_doi(d) for d in records}

    def task(r) -> tuple:
        key = r["doi"].lower()
        titles, journal, papers = records.get(key, ([], None, 1))
        role = "both" if key in notices and key in originals else "retraction" if key in notices else "original"
        return (r["doi"], r["mhtml_path"], titles, journal, papers,
                tuple(aliases.get(key, ())), role)

    verb = "judging (dry run, nothing is written)" if args.dry_run else "verifying"
    console.print(f"[bold]{verb} {len(rows):,} stored pages[/bold]\n")

    out = fh = writer = None
    if args.dry_run:
        out = args.out or config.ROOT / "runs/checks" / f"identity-recheck-{date.today():%Y-%m-%d}.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        fh = out.open("w", encoding="utf-8", newline="")
        writer = csv.writer(fh)
        writer.writerow(COLUMNS)

    tasks = (task(r) for r in rows)
    pool = Pool(args.workers) if args.workers > 1 else None
    results = pool.imap(check, tasks, chunksize=16) if pool else map(check, tasks)

    verdicts: Counter = Counter()
    flips: Counter = Counter()
    usable_after = 0
    try:
        for i, (r, res) in enumerate(zip(rows, results), 1):
            new_usable = int(res["verdict"] == "ok")
            usable_after += new_usable
            verdicts[(res["verdict"], res["reason"])] += 1
            old_usable = int(r["usable"] or 0)
            if old_usable != new_usable:
                flips[(old_usable, new_usable, res["verdict"], res["reason"],
                       r["source_kind"] or "crawler")] += 1
            key = r["doi"].lower()
            role = "both" if key in notices and key in originals else "retraction" if key in notices else "original"
            if writer:
                rel = relation(res["declared"], r["doi"], pairs, known)
                writer.writerow((r["doi"], r["source_kind"] or "", r["capture_format"] or "",
                                 r["final_host"] or "", role, old_usable,
                                 r["old_verdict"] or "", res["verdict"], new_usable,
                                 int(old_usable != new_usable), res["reason"],
                                 res["declared"] or "", rel,
                                 "" if r["old_sim"] is None else r["old_sim"],
                                 "" if res["similarity"] is None else res["similarity"],
                                 res["page_title"], res["matched_role"] or ""))
            else:
                conn.execute(
                    """INSERT OR REPLACE INTO archive_checks
                       (doi, host, gz_bytes, doi_found, title_similarity, page_title,
                        doc_role, role_ok, verdict, reason, declared_doi, matched_role, checked_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'))""",
                    (r["doi"], r["final_host"], r["gz_bytes"], res["doi_found"],
                     res["similarity"], res["page_title"], role, res["role_ok"],
                     res["verdict"], res["reason"], res["declared"], res["matched_role"]))
                # And act on the verdict: `documents.usable` is the flag Stage 2
                # selects on and every coverage figure is built from.
                conn.execute("UPDATE documents SET usable = ? WHERE doi = ?",
                             (new_usable, r["doi"]))
                # Commit often and briefly. Holding one transaction across
                # hundreds of gzip reads locks the database for minutes at a
                # time, and every other process in the pipeline is a writer too.
                if i % 25 == 0:
                    conn.commit()
            if i % 5000 == 0:
                console.print(f"[dim]  {i:,}/{len(rows):,}  usable {usable_after:,}[/dim]")
    finally:
        if pool:
            pool.close()
            pool.join()
        if fh:
            fh.close()
        if not args.dry_run:
            conn.commit()

    n = len(rows) or 1
    console.print(f"\n[bold]{len(rows):,} pages[/bold]")
    for (v, why), c in sorted(verdicts.items(), key=lambda x: (x[0][0] != "ok", -x[1])):
        console.print(f"  {v:<14} {why:<24} {c:>7,}  {c / n * 100:5.1f}%")
    lost = sum(c for (o, nw, *_), c in flips.items() if o > nw)
    gained = sum(c for (o, nw, *_), c in flips.items() if o < nw)
    console.print(f"\n  usable -> not usable: {lost:,}    not usable -> usable: {gained:,}")
    for (o, nw, v, why, kind), c in sorted(flips.items(), key=lambda x: -x[1])[:20]:
        console.print(f"    {o}->{nw}  {v:<14} {why:<24} {kind:<20} {c:>6,}")
    total = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] or 1
    usable_now = conn.execute("SELECT COUNT(*) FROM documents WHERE usable = 1").fetchone()[0]
    if args.dry_run:
        after = usable_now - sum(int(r["usable"] or 0) for r in rows) + usable_after
        console.print(f"\n  usable documents: {usable_now:,} ({usable_now / total * 100:.2f}%) now, "
                      f"{after:,} ({after / total * 100:.2f}%) under this rule, of {total:,}")
        console.print(f"  written: {out}")
    else:
        console.print(f"\n  usable documents: {usable_now:,} of {total:,} "
                      f"({usable_now / total * 100:.2f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
