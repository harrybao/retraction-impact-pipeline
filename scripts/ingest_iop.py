"""Bring IOP's SFTP delivery into the pipeline.

    python scripts/ingest_iop.py            # dry run: say what would be written
    python scripts/ingest_iop.py --apply    # write it

Run check_iop_delivery.py first. This assumes the delivery is complete and
re-checks each file only for its own integrity.

What each zip becomes:

* **The XML**, gzipped into the archive through storage.write_capture, exactly
  as the Elsevier API and PMC routes store theirs. Stage 2 routes a document
  by its content, not its label, so a JATS file reaches the JATS parser
  whatever it is called. The label records which test it passed: `jats` if the
  extractor's own JATS test accepts it, `iopxml` if not - so a schema the
  parser has never seen shows up in `documents` rather than being handed
  silently to the HTML path and read as a web page.

* **The PDF**, under data/pdf/, recorded in `pdf_files`. The pipeline has never
  read a PDF. Where IOP had only metadata XML, the PDF is the only copy of the
  article, and keeping it means that question can be answered later instead
  of being lost now. data/pdf/ is gitignored for the same reason data/mhtml/
  is: publisher copyright, supplied under the TDM terms of the University's IOP
  licence via CAUL, for processing and not for redistribution.

* **The `documents` row**, updated the way collect_elsevier.py updates it.

Identity is the DOI in the XML, never the filename. A file under one of the
two DOIs IOP corrected on 2026-09-09 is filed under the DOI we asked for - ask
at corrected_doi, file under doi. A DOI that is not in `documents` is refused
rather than inserted: nothing enters the corpus that the source data did not
name. Documents already usable (26 of the 2,595 requested) are left alone and
counted, not overwritten.

`usable` is set here as the Elsevier route sets it, and verify_archives.py is
run afterwards over every stored capture anyway - it re-derives the flag from
the file itself, so this is not the last word on any document.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console

from rwpipe import config, db, extract, storage
from rwpipe.iop import NAME_DOI, unescape, xml_doi

console = Console()

DEFAULT_DIR = Path(r"E:\5703 capstone\iop-delivery")
HOST = "iopp-public-transfer-server.cld.iop.org"
REMOTE = f"sftp://{HOST}/<account>/<account>files/"  # account folder withheld in the public copy
PDF_DIR = config.DATA / "pdf"
REPORT = config.DATA / "reports" / "iop-ingest.csv"
BODY = re.compile(r"<body[\s>]")


def pdf_path(doi: str, prefix: str) -> Path:
    """Same sharding as storage.page_path, so a PDF sits beside its XML's
    shard and is found the same way."""
    h = hashlib.sha1(doi.encode("utf-8")).hexdigest()
    return PDF_DIR / prefix.replace("/", "_") / h[:2] / f"{h}.pdf"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    ap.add_argument("--apply", action="store_true",
                    help="write to the archive and database (default: dry run)")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    conn = db.connect()
    # Only when applying. A dry run that creates a table is not a dry run, and
    # the first one did exactly that before this was moved.
    if args.apply:
        conn.execute("""CREATE TABLE IF NOT EXISTS pdf_files (
            doi TEXT PRIMARY KEY, path TEXT, bytes INTEGER,
            source TEXT, stored_at TEXT)""")
        conn.commit()
    back = {c.lower(): d for d, c in
            conn.execute("SELECT doi, corrected_doi FROM doi_corrections")}
    docs = {r[0].lower(): (r[0], r[1], r[2]) for r in
            conn.execute("SELECT doi, doi_prefix, usable FROM documents")}

    zips = sorted(args.dir.glob("*.zip"))
    if args.limit:
        zips = zips[: args.limit]
    mode = "[red]APPLY[/red]" if args.apply else "dry run"
    console.print(f"[bold]{len(zips):,} zips - {mode}[/bold]\n")

    stats: Counter = Counter()
    rows = []
    for i, p in enumerate(zips, 1):
        rec = {"file": p.name, "doi": "", "outcome": "", "fmt": "",
               "fulltext": "", "pdf_bytes": 0, "note": ""}
        try:
            with zipfile.ZipFile(p) as z:
                bad = z.testzip()
                if bad:
                    raise zipfile.BadZipFile(f"CRC failure in {bad}")
                names = [n for n in z.namelist() if not n.endswith("/")]
                xmls = [n for n in names if n.lower().endswith(".xml")]
                pdfs = [n for n in names if n.lower().endswith(".pdf")]
                if len(xmls) != 1:
                    raise ValueError(f"{len(xmls)} xml members, expected 1")
                xml = z.read(xmls[0]).decode("utf-8", "replace")
                pdf = z.read(pdfs[0]) if len(pdfs) == 1 else b""
        except Exception as exc:                           # noqa: BLE001
            rec["outcome"] = "unreadable"
            rec["note"] = f"{type(exc).__name__}: {exc}"[:200]
            stats["refused: zip unreadable"] += 1
            rows.append(rec)
            continue

        d, how = xml_doi(xml)
        if not d:
            m = NAME_DOI.match(p.name)
            d = unescape(m.group(1)) if m else ""
            rec["note"] = "no DOI in the XML; filename used"
        # A corrected DOI is filed under the DOI we originally asked for.
        if d in back:
            rec["note"] = (rec["note"] + "; " if rec["note"] else "") + \
                f"IOP-corrected DOI {d}, filed under the original"
            d = back[d].lower()

        hit = docs.get(d)
        if not hit:
            rec["doi"], rec["outcome"] = d, "not-in-documents"
            stats["refused: DOI not in documents"] += 1
            rows.append(rec)
            continue
        doi, prefix, usable = hit
        prefix = prefix or doi.split("/", 1)[0]
        rec["doi"] = doi
        if usable == 1:
            rec["outcome"] = "already-usable"
            stats["left alone: already usable"] += 1
            rows.append(rec)
            continue

        jats = bool(extract._JATS_MARK.match(xml.lstrip()[:120])
                    and "<article-title" in xml[:400_000])
        fmt = "jats" if jats else "iopxml"
        full = bool(BODY.search(xml))
        rec["fmt"], rec["fulltext"], rec["pdf_bytes"] = fmt, int(full), len(pdf)
        stats[f"ingest: {fmt}, {'full text' if full else 'metadata only'}"] += 1

        if args.apply:
            path, raw_n, gz_n = storage.write_capture(doi, prefix, xml, fmt=fmt)
            conn.execute(
                """UPDATE documents
                      SET status='done', usable=1, http_status=200,
                          mhtml_path=?, mhtml_bytes=?, gz_bytes=?,
                          capture_format=?, source_kind='iop-sftp',
                          final_host=?, final_url=?, error=NULL,
                          attempts=COALESCE(attempts, 0) + 1,
                          fetched_at=datetime('now')
                    WHERE doi=?""",
                (storage.rel(path), raw_n, gz_n, fmt, HOST, REMOTE + p.name, doi))
            if pdf:
                pp = pdf_path(doi, prefix)
                pp.parent.mkdir(parents=True, exist_ok=True)
                tmp = pp.with_suffix(".part")
                tmp.write_bytes(pdf)
                tmp.replace(pp)
                conn.execute(
                    "INSERT OR REPLACE INTO pdf_files VALUES (?,?,?,?,datetime('now'))",
                    (doi, pp.relative_to(config.DATA).as_posix(), len(pdf), "iop-sftp"))
            if i % 50 == 0:
                conn.commit()
        rec["outcome"] = "ingested" if args.apply else "would ingest"
        rows.append(rec)
        if i % 500 == 0:
            console.print(f"[dim]  {i:,}/{len(zips):,}[/dim]")

    conn.commit()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with REPORT.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["file"])
        w.writeheader()
        w.writerows(rows)

    for k, n in sorted(stats.items()):
        console.print(f"  {k:<44}{n:>6,}")
    console.print(f"\n[dim]per-file detail: {REPORT}[/dim]")
    if args.apply:
        console.print("\nnext: python scripts/verify_archives.py, then "
                      "python scripts/extract_text.py")
    else:
        console.print("\n[dim]dry run - nothing written. Re-run with --apply.[/dim]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
