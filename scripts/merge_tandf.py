"""Merge the Taylor & Francis captures the team lead collected.

    python scripts/merge_tandf.py --log tandf_download_log_final.csv --from <mhtml folder> --dry-run
    python scripts/merge_tandf.py --log tandf_download_log_final.csv --from <mhtml folder>

Taylor & Francis refused every route this project measured, from a
residential address and from the University network alike (`probe_results`,
2026-08-31 and 2026-09-01), and answered the text-and-data-mining request with
three conditions - institutional authorisation, a defined date window, and a
static University address - which the Library declined to arrange. The team
lead collected 2,867 pages between 2026-09-09 22:50 and 2026-09-11 16:27: the
log records 2,800 saved and 67 answered 404, at a median 17 s between requests.
**How they were collected is not recorded, but the pages say where from.**
124 of 150 sampled pages (seed 5703) carry "Access provided by University of
Sydney Library": they were rendered under the University's library
entitlement - the route whose systematic use this project ruled out, and for
which the publisher's text-and-data-mining conditions were never met. That has
to be settled before anything is merged. Every document merged from this batch
carries `source_kind='teammate-tandf'`, so the batch can be reported on its
own, or removed as one unit.

**The DOI comes from the log, never from the filename.** Files are named by
the DOI with "/" turned into "_", which cannot be undone for a DOI that already
contains "_" - one in this batch does. Another contains ":", which the
collector's disk could not hold in a file name: the zip carries it as an empty
`10.3843_susdev.15.5`, so that page is reported missing rather than merged.

**The files were written through Windows text mode.** Chrome's MHTML ends every
line CRLF; the collector wrote that text with each LF turned into CRLF, so
every line now ends CR CR LF. The MIME parser takes the first CR as the end of
the headers and finds no parts - the first dry run (2026-09-16) refused all
2,799 files as "no text/html part". `undo_text_mode` recovers the original bytes, and only
where the file shows that pattern and nothing else. A Blink archive is 7-bit
(quoted-printable and base64 bodies), so it holds no bare CR or LF of its own
and the reversal is exact. The repaired bytes are what is checked and stored;
the delivered files are not modified.

**Identity is established again here**, not trusted from the log's `ok`:

    the address   Snapshot-Content-Location, where the file carries it, must be
                  on tandfonline.com and must name the DOI. The SAGE hand-off
                  held 57 doi.org error pages that contain the DOI in their text,
                  because the error echoes it back, so a body search alone
                  would have admitted them.
    the page      not a CDN error page (fetch.ORIGIN_ERROR), no refusal in the
                  <title>, and the DOI present in the decoded document.

The refusal test reads the title only. Matched against the whole page, the same
kind of pattern flagged 15,245 intact Wiley articles on 2026-09-10 and 7 of 10
OUP articles in the campus merge, because publishers embed a CAPTCHA script for
their login forms. "not found" is also narrower than in merge_campus.py: a bare
"not found" is the title of an article about species not found in a survey.

A document that is already usable is never overwritten.

**Extraction is not run from here.** After a merge, `extract_text.py` without
`--redo` extracts exactly the documents that have no successful row at the
current extractor version, which includes every document merged here.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path, PureWindowsPath
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console

from rwpipe import config, db, fetch, mhtml, storage

console = Console()

HOST = "tandfonline.com"
SOURCE_KIND = "teammate-tandf"
TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
TITLE_REFUSAL = re.compile(
    r"just a moment|attention required|checking your browser|are you a robot|"
    r"verify you are human|captcha|access denied|request rejected|"
    r"page not found|404 not found|error 404|^\s*not found\s*$", re.I)
PROMOTABLE = {"pending", "failed", "running", "blocked", None}
DEFAULT_REPORT = config.DATA / "reports" / "tandf-merge.csv"


def load_log(path: Path) -> list[dict[str, str]]:
    """Every row of the log, each with the name of the file it produced.

    Rows without a file are kept, with `name` empty. The first version keyed the
    log on the file name, and the first dry run showed what that costs: a page
    that answered 404 has no file, so all 67 of them left the report without a
    word - 4 rows read from a 5-row log.
    """
    out: list[dict[str, str]] = []
    with path.open(encoding="utf-8-sig", errors="replace", newline="") as fh:
        for r in csv.DictReader(fh):
            row = {k: (v or "").strip() for k, v in r.items()}
            row["name"] = PureWindowsPath(row.get("file", "")).name
            out.append(row)
    return out


def undo_text_mode(raw: bytes) -> tuple[bytes, bool]:
    """(bytes, repaired): undo a Windows text-mode write, only when provable.

    Undone only when every LF is preceded by CR CR and there is no other CR in
    the file - the exact trace of CRLF text written with LF -> CRLF. Any other
    mix of endings is returned unchanged, for the identity checks to judge.
    """
    n = raw.count(b"\r\r\n")
    if n and n == raw.count(b"\r\n") == raw.count(b"\n") and raw.count(b"\r") == 2 * n:
        return raw.replace(b"\r\r\n", b"\r\n"), True
    return raw, False


def judge(path: Path, doi: str) -> tuple[str, str, str]:
    """(verdict, landed_url, title) for a file on disk. 'ok' means every identity check passed."""
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        return f"unreadable: read: {type(exc).__name__}", "", ""
    return judge_bytes(undo_text_mode(raw)[0], doi)


def judge_bytes(raw: bytes, doi: str) -> tuple[str, str, str]:
    """The identity checks on archive bytes already repaired."""
    doc = mhtml.parse(raw)
    if not doc.ok:
        return f"unreadable: {doc.error}", "", ""
    url = unquote(doc.url or "")
    if url:
        host = urlsplit(url).netloc.lower()
        if "doi.org" in host:
            return "never-left-doi.org", url, ""
        if HOST not in host:
            return "landed-on-another-host", url, ""
        if doi.lower() not in url.lower():
            return "url-is-a-different-doi", url, ""
    head = doc.html[:200_000]
    t = TITLE.search(head)
    title = re.sub(r"\s+", " ", t.group(1)).strip()[:200] if t else ""
    if fetch.ORIGIN_ERROR.search(head):
        return "cdn-error-page", url, title
    if TITLE_REFUSAL.search(title):
        return "refusal-title", url, title
    if not fetch.contains_doi(doc.html, doi):
        return "no-doi-in-file", url, title
    return "ok", url, title


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True, help="tandf_download_log_final.csv")
    ap.add_argument("--from", dest="src", required=True, help="the folder of .mhtml files")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", default=str(DEFAULT_REPORT))
    args = ap.parse_args()

    src, log_path, report = Path(args.src), Path(args.log), Path(args.report)
    if not src.is_dir():
        console.print(f"[red]not a directory: {src}[/red]")
        return 1
    if not log_path.is_file():
        console.print(f"[red]no log at {log_path}[/red]")
        return 1

    log = load_log(log_path)
    conn = db.connect()
    db.init(conn)
    conn.execute("PRAGMA busy_timeout = 15000")
    docs = {r[0].lower(): r for r in
            conn.execute("SELECT doi, status, usable, doi_prefix FROM documents")}

    on_disk = {p.name: p for p in src.iterdir() if p.is_file() and p.suffix.lower() == ".mhtml"}
    mode = "dry run" if args.dry_run else "[red]APPLY[/red]"
    console.print(f"[bold]{len(log):,} log rows, {len(on_disk):,} files on disk - {mode}[/bold]\n")

    stats: Counter = Counter()
    rows = []
    merged = 0
    for entry in sorted(log, key=lambda r: (r["name"], r.get("doi", ""))):
        name, doi = entry["name"], entry.get("doi", "")
        rec = {"file": name, "doi": doi, "log_status": entry.get("status", ""),
               "line_endings": "", "verdict": "", "landed_url": "", "title": "", "outcome": ""}
        rows.append(rec)
        if entry.get("status") != "ok":
            stats[f"skipped: log says {entry.get('status') or 'nothing'}"] += 1
            rec["outcome"] = "log-not-ok"
            continue
        path = on_disk.get(name)
        if path is None:
            stats["refused: file named in the log is missing"] += 1
            rec["outcome"] = "file-missing"
            continue

        raw, repaired = undo_text_mode(path.read_bytes())
        if repaired:
            stats["note: CR CR LF line endings repaired"] += 1
            rec["line_endings"] = "CR CR LF repaired"
        verdict, url, title = judge_bytes(raw, doi)
        rec.update(verdict=verdict, landed_url=url, title=title)
        if verdict != "ok":
            stats[f"refused: {verdict}"] += 1
            rec["outcome"] = "refused"
            continue
        if not url:
            stats["note: no Snapshot-Content-Location header"] += 1

        row = docs.get(doi.lower())
        if not row:
            stats["refused: DOI not in documents"] += 1
            rec["outcome"] = "not-in-documents"
            continue
        real_doi, status, usable, prefix = row
        if usable == 1:
            stats["left alone: already usable"] += 1
            rec["outcome"] = "already-usable"
            continue
        if not (status in PROMOTABLE or status == "done"):
            stats[f"left alone: status {status}"] += 1
            rec["outcome"] = f"status-{status}"
            continue

        replaces = " (replaces a rejected capture)" if status == "done" else ""
        stats[f"merge{replaces}"] += 1
        if not args.dry_run:
            text = raw.decode("utf-8", "ignore")
            stored, raw_n, gz_n = storage.write_mhtml(
                real_doi, prefix or real_doi.split("/", 1)[0], text)
            final_url = url or entry.get("url", "")
            conn.execute(
                """UPDATE documents
                      SET status='done', usable=1, http_status=200,
                          mhtml_path=?, mhtml_bytes=?, gz_bytes=?,
                          capture_format='mhtml', source_kind=?,
                          final_url=?, final_host=?, error=NULL,
                          attempts=COALESCE(attempts, 0) + 1,
                          fetched_at=datetime('now')
                    WHERE doi=?""",
                (storage.rel(stored), raw_n, gz_n, SOURCE_KIND, final_url,
                 urlsplit(final_url).netloc.lower() or f"www.{HOST}", real_doi))
            if merged % 100 == 0:
                conn.commit()
        merged += 1
        rec["outcome"] = "would merge" if args.dry_run else "merged"

    if not args.dry_run:
        conn.commit()

    named = {e["name"] for e in log if e["name"]}
    for name in sorted(set(on_disk) - named):
        stats["ignored: file on disk with no log row"] += 1
        rows.append({"file": name, "doi": "", "log_status": "", "line_endings": "",
                     "verdict": "", "landed_url": "", "title": "", "outcome": "no-log-row"})

    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["file"])
        w.writeheader()
        w.writerows(rows)

    console.print(f"[bold]{'would merge' if args.dry_run else 'merged'} {merged:,}[/bold]")
    for k, v in sorted(stats.items()):
        console.print(f"   {k:<52}{v:>6,}")
    total = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    ok = conn.execute("SELECT COUNT(*) FROM documents WHERE usable=1").fetchone()[0]
    after = ok + merged if args.dry_run else ok
    console.print(f"\ncoverage {'would be' if args.dry_run else 'now'} "
                  f"{after:,}/{total:,} ({after / total * 100:.2f}%)")
    console.print(f"[dim]per-file detail: {report}[/dim]")
    if not args.dry_run:
        console.print("next: python scripts/verify_archives.py, then python scripts/extract_text.py "
                      "without --redo - it extracts the documents merged here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
