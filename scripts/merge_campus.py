"""Merge the campus-network collection into the pipeline.

    python scripts/merge_campus.py --from "E:/5703 capstone/CAMPUS-KIT 2/CAMPUS-KIT" --dry-run
    python scripts/merge_campus.py --from "E:/5703 capstone/CAMPUS-KIT 2/CAMPUS-KIT"

Five publishers - Oxford University Press, Portland Press, AIP, AACR and Karger
- answer a University of Sydney address and refuse a residential one.
`COLLECT-CAMPUS.command` fetched their pages with curl on the campus network,
so each file is the HTML the server returned, stored here as capture_format
'html' (not MHTML: curl does not render, and these publishers serve the
article in the HTML itself).

**The DOI comes from the collector's logs, never from the filename.** Files
are named by the DOI with both "/" and ":" turned into "_", which cannot be
reversed. `campus-logs/<host>.tsv` records the DOI each file was fetched for.

**Identity is checked again here** rather than trusted from the collector:
the DOI must appear in the file, the file must not be a CDN error page
(fetch.ORIGIN_ERROR), and its <title> must not be a challenge or not-found
page. The challenge test looks at the title only, deliberately. Matched
against the raw HTML - as merge_sage.py does, where the files are MHTML -
the same kind of pattern flagged 7 of 10 sampled OUP articles, because OUP
embeds a CAPTCHA script for its login form. That is the reCAPTCHA mistake of
2026-09-10 (16,454 Wiley captures wrongly rejected) waiting in a new place. On
the titles of the same sample: 0.

**'thin' files are merged too.** The collector called a page thin when it had
fewer than ten <p> tags. Measured on a sample, most are retraction notice
pages, which are short by nature - 20 of 20 thin files from Portland Press and
AACR were graded notice-only by the extractor - and the rest are abstract
pages. They are the right document; Stage 2 grades how much text they hold.

A document that is already usable is never overwritten. One that is 'done'
but unusable is: its earlier capture was rejected, and a verified campus
capture replaces it. verify_archives.py re-derives `usable` from every stored
file afterwards, so the flag set here is not the last word.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console

from rwpipe import config, db, fetch, storage

console = Console()

TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
TITLE_REFUSAL = re.compile(
    r"just a moment|attention required|checking your browser|are you a robot|"
    r"verify you are human|captcha|access denied|request rejected|"
    r"page not found|404 not found|not found", re.I)
PROMOTABLE = {"pending", "failed", "running", "blocked", None}
REPORT = config.DATA / "reports" / "campus-merge.csv"


def load_logs(root: Path) -> dict[str, tuple[str, str, str]]:
    """file name -> (doi, host, kind), from the collector's per-host logs."""
    out: dict[str, tuple[str, str, str]] = {}
    for log in sorted((root / "campus-logs").glob("*.tsv")):
        with log.open(encoding="utf-8", errors="replace", newline="") as fh:
            for r in csv.DictReader(fh, delimiter="\t"):
                f = (r.get("file") or "").strip()
                if f:
                    out[f] = ((r.get("doi") or "").strip(),
                              (r.get("host") or "").strip(),
                              (r.get("kind") or "").strip())
    return out


def load_urls(root: Path) -> dict[str, str]:
    """doi -> the page URL the collector requested, if the worklist is here."""
    p = root / "campus-worklist.tsv"
    if not p.exists():
        return {}
    with p.open(encoding="utf-8", errors="replace", newline="") as fh:
        return {(r.get("doi") or "").strip().lower(): (r.get("page_url") or "").strip()
                for r in csv.DictReader(fh, delimiter="\t")}


def judge(text: str, doi: str) -> tuple[str, str]:
    """(verdict, title). 'ok' means every identity check passed."""
    head = text[:200_000]
    t = TITLE.search(head)
    title = re.sub(r"\s+", " ", t.group(1)).strip()[:200] if t else ""
    if fetch.ORIGIN_ERROR.search(head):
        return "cdn-error-page", title
    if TITLE_REFUSAL.search(title):
        return "refusal-title", title
    if not fetch.contains_doi(text, doi):
        return "no-doi-in-file", title
    return "ok", title


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", required=True,
                    help="the CAMPUS-KIT folder holding collected-campus/ and campus-logs/")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = Path(args.src)
    files_dir = root / "collected-campus"
    if not files_dir.is_dir():
        console.print(f"[red]no collected-campus/ under {root}[/red]")
        return 1
    logs, urls = load_logs(root), load_urls(root)

    conn = db.connect()
    db.init(conn)
    conn.execute("PRAGMA busy_timeout = 15000")
    docs = {r[0].lower(): r for r in
            conn.execute("SELECT doi, status, usable, doi_prefix FROM documents")}

    files = sorted(p for p in files_dir.iterdir()
                   if p.is_file() and p.name.endswith(".html"))
    mode = "dry run" if args.dry_run else "[red]APPLY[/red]"
    console.print(f"[bold]{len(files):,} files - {mode}[/bold]  "
                  f"({len(logs):,} named in the logs)\n")

    stats: Counter = Counter()
    rows = []
    merged = 0
    for i, p in enumerate(files, 1):
        label = "thin" if p.name.endswith(".thin.html") else "article"
        rec = {"file": p.name, "doi": "", "host": "", "label": label,
               "verdict": "", "outcome": "", "title": ""}
        m = logs.get(p.name)
        if not m or not m[0]:
            stats["refused: no log row for the file"] += 1
            rec["outcome"] = "no-log-row"
            rows.append(rec)
            continue
        doi, host, _kind = m
        rec["doi"], rec["host"] = doi, host
        text = p.read_bytes().decode("utf-8", "ignore")
        verdict, title = judge(text, doi)
        rec["verdict"], rec["title"] = verdict, title
        if verdict != "ok":
            stats[f"refused: {verdict}"] += 1
            rec["outcome"] = "refused"
            rows.append(rec)
            continue

        row = docs.get(doi.lower())
        if not row:
            stats["refused: DOI not in documents"] += 1
            rec["outcome"] = "not-in-documents"
            rows.append(rec)
            continue
        real_doi, status, usable, prefix = row
        if usable == 1:
            stats["left alone: already usable"] += 1
            rec["outcome"] = "already-usable"
            rows.append(rec)
            continue
        if not (status in PROMOTABLE or status == "done"):
            stats[f"left alone: status {status}"] += 1
            rec["outcome"] = f"status-{status}"
            rows.append(rec)
            continue

        replaces = " (replaces a rejected capture)" if status == "done" else ""
        stats[f"merge: {label}{replaces}"] += 1
        if not args.dry_run:
            path, raw_n, gz_n = storage.write_capture(
                real_doi, prefix or real_doi.split("/", 1)[0], text, fmt="html")
            conn.execute(
                """UPDATE documents
                      SET status='done', usable=1, http_status=200,
                          mhtml_path=?, mhtml_bytes=?, gz_bytes=?,
                          capture_format='html', source_kind='campus-network',
                          final_url=?, final_host=?, error=NULL,
                          attempts=COALESCE(attempts, 0) + 1,
                          fetched_at=datetime('now')
                    WHERE doi=?""",
                (storage.rel(path), raw_n, gz_n,
                 urls.get(real_doi.lower()) or f"https://{host}/", host, real_doi))
            if i % 100 == 0:
                conn.commit()
        merged += 1
        rec["outcome"] = "would merge" if args.dry_run else "merged"
        rows.append(rec)

    if not args.dry_run:
        conn.commit()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with REPORT.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["file"])
        w.writeheader()
        w.writerows(rows)

    console.print(f"[bold]{'would merge' if args.dry_run else 'merged'} "
                  f"{merged:,}[/bold] of {len(files):,}")
    for k, v in sorted(stats.items()):
        console.print(f"   {k:<48}{v:>6,}")
    total = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    ok = conn.execute("SELECT COUNT(*) FROM documents WHERE usable=1").fetchone()[0]
    after = ok if not args.dry_run else ok + merged
    console.print(f"\ncoverage {'would be' if args.dry_run else 'now'} "
                  f"{after:,}/{total:,} ({after / total * 100:.2f}%)")
    console.print(f"[dim]per-file detail: {REPORT}[/dim]")
    if not args.dry_run:
        console.print("next: python scripts/verify_archives.py, then "
                      "python scripts/extract_text.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
