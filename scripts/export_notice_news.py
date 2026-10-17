"""Export the retraction-notice attention tables for the R analysis.

    python scripts/export_notice_news.py

Writes to data/clean/:
    altmetric_notice.csv        one row per notice: attention, and how many
                                papers it retracts
    altmetric_notice_posts.csv.gz  one row per dated mention of a notice
    plumx_notice.csv            PlumX on the same notice DOIs (counts only:
                                PlumX gives no dates and no text)
    notice_papers.csv           notice DOI -> original DOI, the mapping the
                                merge needs and the only place it exists

The mapping is exported rather than rebuilt in R because it is the thing that
decides how notice-level coverage becomes paper-level coverage, and it should
be one table that both languages read, not two derivations that can disagree.

`plumx_notice.csv` was added on 2026-10-13: the news coverage over all four
routes (Altmetric and PlumX, paper and notice DOIs; project log 2026-09-27) was
counted from the database by hand and had no producer, because the PlumX
notice counts never reached data/clean. analysis/news_coverage_union.R now
rebuilds it from here.

The database is opened read-only, as scripts/export_tables.py opens it. An
export only reads; it used to open the database through db.connect() and run
db.init() and db.migrate(), which set the journal mode and can create tables
and add columns - so exporting could change the collected database, the file
the handover package lists with its checksum.
"""
from __future__ import annotations

import csv
import gzip
import io
from contextlib import ExitStack
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console

from rwpipe import config

console = Console()
# Through config, so RWPIPE_DATA is honoured (a run in a scratch directory
# exports that directory's database, not the collected one).
DB = config.DB_PATH
OUT = config.DATA / "clean"

def dump(conn, sql: str, path: Path, gz: bool = False) -> int:
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    n = 0
    with ExitStack() as stack:
        if gz:
            raw = stack.enter_context(open(path, "wb"))
            packed = stack.enter_context(gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0))
            fh = stack.enter_context(io.TextIOWrapper(packed, encoding="utf-8", newline=""))
        else:
            fh = stack.enter_context(open(path, "w", encoding="utf-8", newline=""))
        w = csv.writer(fh)
        w.writerow(cols)
        for row in cur:
            w.writerow([row[c] for c in cols])
            n += 1
    console.print(f"  -> {path.name}  ({n:,} rows)")
    return n


def main() -> int:
    if not Path(DB).is_file():
        console.print(f"[red]no database at {DB}[/red]")
        return 1
    wal = Path(str(DB) + "-wal")
    if wal.exists() and wal.stat().st_size:
        console.print("[red]export needs a checkpointed database (non-empty WAL)[/red]")
        return 1
    conn = sqlite3.connect(Path(DB).resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row

    have = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "altmetric_notice" not in have:
        console.print("[red]no altmetric_notice table - run "
                      "collect_altmetric_notices.py first[/red]")
        conn.close()
        return 1

    required = {"records", "altmetric_notice_posts", "plumx_notice"}
    missing = required - have
    if missing:
        console.print(f"[red]no {', '.join(sorted(missing))} table - incomplete notice export; "
                      "run the notice collectors first (collect_plumx.py --notices)[/red]")
        conn.close()
        return 1

    # Preflight every selected column before replacing any prior export.
    selections = {
        "altmetric_notice": "retraction_doi,n_papers,found,altmetric_id,score,news_count,social_count,counts_json,added_on,error,fetched_at,detail_posts,detail_fetched_at",
        "altmetric_notice_posts": "retraction_doi,kind,post_id,title,summary,url,author,author_url,posted_on,country,license",
        "plumx_notice": "retraction_doi,n_papers,found,plumx_id,usage_count,capture_count,mention_count,social_count,citation_count,totals_json,detail_json,error,fetched_at",
        "records": "retraction_doi,original_doi",
    }
    try:
        for table, columns in selections.items():
            conn.execute(f"SELECT {columns} FROM {table} LIMIT 0")
    except sqlite3.Error as exc:
        console.print(f"[red]incomplete notice schema: {exc}; no exports replaced[/red]")
        conn.close()
        return 1
    OUT.mkdir(parents=True, exist_ok=True)

    dump(conn, f"SELECT {selections['altmetric_notice']} FROM altmetric_notice",
         OUT / "altmetric_notice.csv")

    dump(conn, f"SELECT {selections['altmetric_notice_posts']} FROM altmetric_notice_posts",
         OUT / "altmetric_notice_posts.csv.gz", gz=True)

    # PlumX on the notice DOIs: the PlumX columns of data/clean/plumx.csv
    # (without the paper's title, journal and citation fields), keyed on the
    # notice and carrying how many papers it retracts; detail_json carries
    # mention.NEWS_COUNT.
    # A missing table is refused above: partial replacement could leave an old
    # PlumX CSV beside newer Altmetric and mapping files.
    dump(conn, f"SELECT {selections['plumx_notice']} FROM plumx_notice",
         OUT / "plumx_notice.csv")

    # The mapping, with the paper count carried on every row so the merge never
    # has to recompute it and cannot compute it differently.
    cur = conn.execute("""
        SELECT lower(trim(retraction_doi)) AS retraction_doi,
               lower(trim(original_doi))   AS original_doi
        FROM records
        WHERE retraction_doi IS NOT NULL AND trim(retraction_doi) <> ''
          AND original_doi  IS NOT NULL AND trim(original_doi)  <> ''
          AND lower(trim(retraction_doi)) <> lower(trim(original_doi))
        GROUP BY 1, 2""")
    pairs = [(r["retraction_doi"], r["original_doi"]) for r in cur]
    per_notice: dict[str, int] = {}
    for nd, _ in pairs:
        per_notice[nd] = per_notice.get(nd, 0) + 1
    path = OUT / "notice_papers.csv"
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["retraction_doi", "original_doi", "n_papers"])
        for nd, od in pairs:
            w.writerow([nd, od, per_notice[nd]])
    console.print(f"  -> {path.name}  ({len(pairs):,} rows, "
                  f"{len(per_notice):,} notices)")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
