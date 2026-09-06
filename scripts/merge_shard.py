"""Merge a teammate's crawl results into this machine's dataset.

    python scripts/merge_shard.py --from D:/from-alice --who alice
    python scripts/merge_shard.py --from D:/from-alice --who alice --dry-run

A teammate hands over a folder containing:
    pipeline.sqlite     their frontier database
    mhtml/              their archived pages

File paths are content-addressed - the same DOI hashes to the same relative
path on every machine - so archives merge by copy with no collisions and no
renaming. The database merge only ever *promotes* a document: a row already
`done` here is never overwritten by a teammate's failure.
"""
from __future__ import annotations

import argparse
import gzip
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console

from rwpipe import config, db, fetch, storage

console = Console()

# Fields copied across when a teammate's fetch supersedes ours.
# capture_format was missing, so a merged WebKit HTML capture arrived labelled
# as nothing and downstream could not tell it from MHTML.
FIELDS = ("status", "attempts", "final_url", "final_host", "http_status",
          "mhtml_path", "mhtml_bytes", "gz_bytes", "capture_format",
          "elapsed_ms", "error", "fetched_at")

# `blocked` belongs here. A refusal is a statement about one attempt from one
# address at one moment, not a property of the page: a teammate's run recovered
# 1,357 pages this project had recorded as blocked, mostly Springer and Nature
# challenges that are intermittent. Excluding `blocked` meant their successes
# could never be merged, and the same pages would stay lost after every future
# exchange.
PROMOTABLE = {"pending", "failed", "running", "blocked", None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", required=True,
                    help="folder holding the teammate's pipeline.sqlite and mhtml/")
    ap.add_argument("--who", default="teammate", help="name, for the log")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = Path(args.src)
    src_db, src_mhtml = src / "pipeline.sqlite", src / "mhtml"
    for p in (src_db, src_mhtml):
        if not p.exists():
            console.print(f"[red]missing: {p}[/red]")
            return 1

    their = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    their.row_factory = sqlite3.Row
    ours = db.connect()
    db.init(ours)

    done_rows = their.execute(
        "SELECT * FROM documents WHERE status='done' AND mhtml_path IS NOT NULL"
    ).fetchall()
    blocked_rows = their.execute(
        "SELECT * FROM documents WHERE status IN ('blocked','failed')").fetchall()
    console.print(f"[bold]{args.who}[/bold] brings {len(done_rows):,} archived "
                  f"pages and {len(blocked_rows):,} recorded failures")

    copied = promoted = skipped_have = missing_file = failed_identity = 0
    # Same DOI, a file already here, different bytes. Reported rather
    # than resolved: neither copy can be assumed to be the better one.
    collisions: list[tuple[str, int, int]] = []
    for r in done_rows:
        mine = ours.execute(
            "SELECT status FROM documents WHERE doi=?", (r["doi"],)).fetchone()
        if mine and mine["status"] == "done":
            skipped_have += 1
            continue
        if mine and mine["status"] not in PROMOTABLE:
            skipped_have += 1
            continue

        # content-addressed: identical relative path under either data root
        their_file = storage.resolve(r["mhtml_path"], data_dir=src)
        if not their_file.exists():
            missing_file += 1
            continue
        # Their `done` is not evidence on its own - ours was not either. This
        # project filed 10,049 Elsevier refusal pages as successes because the
        # only test was size, and a merge that trusts a status code imports
        # someone else's version of that mistake. Check the file carries the
        # DOI it is filed under before letting it overwrite anything.
        try:
            text = gzip.open(their_file, "rt", encoding="utf-8", errors="ignore").read()
        except Exception:                                  # noqa: BLE001
            text = ""
        if r["status"] == "done" and not fetch.contains_doi(text, r["doi"]):
            failed_identity += 1
            continue

        our_file = storage.resolve(r["mhtml_path"])
        if not args.dry_run:
            our_file.parent.mkdir(parents=True, exist_ok=True)
            if not our_file.exists():
                shutil.copy2(their_file, our_file)
                copied += 1
            elif our_file.stat().st_size != their_file.stat().st_size:
                # A file already here under the same name, with different
                # content. Copying would destroy a capture we verified; writing
                # their row without copying is worse - the database would then
                # carry their byte counts, their fetched_at and their final_url
                # while the file on disk is ours. One such mismatch is already
                # in the dataset (10.1245/s10434-024-16763-y), created by this
                # branch doing exactly that.
                #
                # Neither side is safe to guess at, so the row is left alone and
                # counted. A collision between two verified captures of the same
                # DOI is a thing to look at, not a thing to resolve silently.
                collisions.append((r["doi"], our_file.stat().st_size,
                                   their_file.stat().st_size))
                continue
            ours.execute(
                f"UPDATE documents SET {','.join(f + '=?' for f in FIELDS)} "
                "WHERE doi=?",
                [r[f] for f in FIELDS] + [r["doi"]])
        promoted += 1

    # failures are data too: keep a cause for every page nobody could fetch
    recorded = 0
    for r in blocked_rows:
        mine = ours.execute(
            "SELECT status FROM documents WHERE doi=?", (r["doi"],)).fetchone()
        if not mine or mine["status"] == "done":
            continue
        if mine["status"] in ("blocked", "failed") and not r["error"]:
            continue        # nothing more informative to add
        if not args.dry_run:
            ours.execute(
                f"UPDATE documents SET {','.join(f + '=?' for f in FIELDS)} "
                "WHERE doi=?",
                [r[f] for f in FIELDS] + [r["doi"]])
        recorded += 1

    if not args.dry_run:
        ours.commit()

    console.print(f"\n[bold]{'would merge' if args.dry_run else 'merged'}[/bold]")
    console.print(f"  archives promoted   : {promoted:,}")
    console.print(f"  files copied        : {copied:,}")
    console.print(f"  already had         : {skipped_have:,}")
    console.print(f"  failures recorded   : {recorded:,}")
    if missing_file:
        console.print(f"  [yellow]listed but file absent: {missing_file:,}[/yellow]")
    if failed_identity:
        console.print(f"  [yellow]rejected, file does not contain its DOI: "
                      f"{failed_identity:,}[/yellow]")
    if collisions:
        console.print(f"  [red]name collisions, left untouched: {len(collisions):,}[/red]")
        for doi, ours_n, theirs_n in collisions[:5]:
            console.print(f"      {doi}  ours {ours_n:,}B  theirs {theirs_n:,}B")

    console.print("\n[bold]frontier after merge[/bold]")
    for s, n in sorted(db.counts_by_status(ours).items()):
        console.print(f"  {s:<8} {n:>8,}")

    their.close()
    ours.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
