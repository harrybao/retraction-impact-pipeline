"""Merge a teammate's SAGE captures, which arrive as loose files rather than a shard.

    python scripts/merge_sage.py --from "E:/5703 capstone/sage_mhtml" --dry-run
    python scripts/merge_sage.py --from "E:/5703 capstone/sage_mhtml"

`merge_shard.py` expects a teammate's own frontier database beside their
archive. This hand-off is 3,413 bare `.mhtml` files named `<record_id>_<kind>`,
so the DOI has to be recovered from our own `pages` table and every claim about
the file has to be established here rather than trusted from a sibling row.

Two independent identity checks, because they fail differently:

    the address   Blink writes `Snapshot-Content-Location` into the MHTML
                  header - the URL the browser actually landed on. This is the
                  stronger check and it catches the case a body search cannot:
                  57 files never left doi.org and hold its "Error: DOI Not
                  Found" page, which *contains the DOI* because the error text
                  echoes it back. `fetch.contains_doi` passes those.

    the content   an address can be right while the page is a challenge or an
                  interstitial. 85 files are Cloudflare challenges served from
                  the correct URL.

Measured over all 3,413: 3,205 pass both, 135 pass the address check only,
0 pass the content check only. That last zero is the useful one - it says the
content check never admitted anything the stricter check would have refused.

A row already `done` here is never overwritten, same rule as merge_shard.py.

Bulk retraction notices are counted but not excluded. 40% of the retraction
pages are a single notice listing dozens of retracted titles, filed under one
DOI - the page is genuine and belongs in the archive, but its body is mostly
about other papers, so it is written to a report for Stage 2 to handle rather
than silently mixed in.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console

from rwpipe import config, db, extract, fetch, mhtml, storage

console = Console()

NAME = re.compile(r"(\d+)_(original|retraction)\.mhtml$", re.I)
LOCATION = re.compile(r"Snapshot-Content-Location:\s*(\S+)")
TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)

# Refusal shapes. Each is a page that returns 200 and looks like a document.
REFUSALS = [
    ("doi-not-found", re.compile(r"Error:\s*DOI Not Found", re.I)),
    ("challenge", re.compile(r"Just a moment|cf-chl|Checking your browser|captcha", re.I)),
    ("access-denied", re.compile(r"Access Denied|Request Rejected|403 Forbidden", re.I)),
    ("not-found", re.compile(r"Page Not Found|404 Not Found", re.I)),
]

# A bulk notice repeats the retraction marker once per listed paper.
BULK_MARK = re.compile(r"RETRACTED:", re.I)

PROMOTABLE = {"pending", "failed", "running", "blocked", None}
REPORTS = config.REPORTS          # honours RWPIPE_DATA


def doi_for(conn) -> dict[tuple[int, str], str]:
    return {(r[0], r[1]): r[2] for r in
            conn.execute("SELECT record_id, kind, doi FROM pages")}


def judge(path: Path, want: str) -> tuple[str, str, str, str]:
    """(verdict, landed_url, title, html). Verdict 'ok' means both checks passed."""
    head = path.open("rb").read(2048).decode("utf-8", "replace").replace("\r", "")
    loc = LOCATION.search(head)
    url = unquote(loc.group(1)) if loc else ""
    if not url:
        return "no-location-header", "", "", ""
    host = urlsplit(url).netloc.lower()
    if "doi.org" in host:
        return "never-left-doi.org", url, "", ""
    if want.lower() not in url.lower():
        return "url-is-a-different-doi", url, "", ""

    try:
        html = mhtml.read(path).html
    except Exception as exc:                                # noqa: BLE001
        return f"read-fail:{type(exc).__name__}", url, "", ""
    t = TITLE.search(html)
    title = re.sub(r"\s+", " ", t.group(1)).strip()[:200] if t else ""
    for name, pat in REFUSALS:
        if pat.search(html[:200_000]):
            return name, url, title, ""
    if not fetch.contains_doi(html, want):
        return "no-doi-in-body", url, title, ""
    return "ok", url, title, html


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = Path(args.src)
    if not src.is_dir():
        console.print(f"[red]not a directory: {src}[/red]")
        return 1

    conn = db.connect()
    db.init(conn)
    conn.execute("PRAGMA busy_timeout = 15000")
    dois = doi_for(conn)

    files = sorted(src.glob("*.mhtml"))
    console.print(f"[bold]{len(files):,} files[/bold] in {src}\n")

    stats: Counter = Counter()
    bulk_rows: list[dict] = []
    seen_bulk: set[str] = set()
    merged = 0

    for n, f in enumerate(files, 1):
        m = NAME.match(f.name)
        if not m:
            stats["bad-name"] += 1
            continue
        rid, kind = int(m.group(1)), m.group(2).lower()
        want = dois.get((rid, kind))
        if not want:
            stats["no-page-row"] += 1
            continue

        verdict, url, title, html = judge(f, want)
        stats[verdict] += 1
        if verdict != "ok":
            continue

        # Bulk detection runs before the merge decision and is keyed by DOI.
        # Putting it after the already-done skip meant the duplicates were
        # never examined, and since the bulk notices are exactly the pages that
        # duplicate, the report came out with 13 rows instead of 777.
        #
        # Count the marker in the EXTRACTED body, not the raw MHTML: the raw
        # file carries the publisher's "related articles" rail, which on a SAGE
        # retraction page is itself a list of retracted titles. Counting there
        # called 992 pages bulk against 777 on extracted text.
        if kind == "retraction" and want not in seen_bulk:
            body = (extract.extract(html, kind="retraction").body or "")
            listed = len(BULK_MARK.findall(body))
            if listed >= 5:
                seen_bulk.add(want)
                bulk_rows.append({"doi": want, "record_id": rid, "file": f.name,
                                  "listed_titles": listed, "title": title})

        row = conn.execute(
            "SELECT status, doi_prefix FROM documents WHERE doi = ?",
            (want,)).fetchone()
        if not row:
            stats["not-in-frontier"] += 1
            continue
        if row[0] not in PROMOTABLE:
            stats["already-done"] += 1
            continue

        text = f.read_bytes().decode("utf-8", "ignore")

        if not args.dry_run:
            path, raw_n, gz_n = storage.write_mhtml(want, row[1], text)
            conn.execute(
                """UPDATE documents
                      SET status='done', usable=1, http_status=200,
                          mhtml_path=?, mhtml_bytes=?, gz_bytes=?,
                          capture_format='mhtml', source_kind='teammate-sage',
                          final_url=?, final_host=?, error=NULL,
                          attempts=attempts+1, fetched_at=datetime('now')
                    WHERE doi=?""",
                (storage.rel(path), raw_n, gz_n, url,
                 urlsplit(url).netloc.lower(), want))
        merged += 1

        if n % 400 == 0:
            if not args.dry_run:
                conn.commit()
            console.print(f"  [dim]{n:>5}/{len(files):,}[/dim]  merged {merged:,}")

    if not args.dry_run:
        conn.commit()
        if bulk_rows:
            REPORTS.mkdir(parents=True, exist_ok=True)
            out = REPORTS / "sage-bulk-notices.csv"
            with out.open("w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(bulk_rows[0]))
                w.writeheader()
                w.writerows(bulk_rows)
            console.print(f"\n[dim]bulk notices listed in {out}[/dim]")

    console.print(f"\n[bold]{'would merge' if args.dry_run else 'merged'} "
                  f"{merged:,}[/bold] of {len(files):,}")
    for k, v in stats.most_common():
        mark = "[green]" if k == "ok" else "[dim]"
        close = "[/green]" if k == "ok" else "[/dim]"
        console.print(f"   {mark}{k:<24}{v:>6,}{close}")
    console.print(f"\n  bulk retraction notices among them: {len(bulk_rows):,}")

    total = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    ok = conn.execute("SELECT COUNT(*) FROM documents WHERE usable=1").fetchone()[0]
    console.print(f"\n[bold]coverage now {ok:,}/{total:,} "
                  f"({ok / total * 100:.1f}%)[/bold]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
