"""Is IOP's delivery complete, intact, and what is actually in it?

    python scripts/check_iop_delivery.py
    python scripts/check_iop_delivery.py --dir "E:/5703 capstone/iop-delivery"

Run this before telling IOP the download is finished. Steve suspends the SFTP
account when we say so, and a copy that is short by one file, or holds one
truncated zip, is then short for good.

Three questions, each answered from the files rather than from the transfer
log:

1. **Complete?** 2,577 zips totalling 1,286,903,233 bytes, the figures in the
   delivery note of 2026-09-11. Every zip must open and pass its CRC check -
   sftp writes straight to the final filename, so an interrupted transfer
   leaves a file with the right name and the wrong contents.

2. **Whose?** The filename escapes the DOI (`.` -> `__`, `/` -> `_`), but that
   is a hint. The DOI is read from the XML inside, and the two disagreeing is
   reported, not silently resolved. Two DOIs were corrected by IOP on
   2026-09-09; a file arriving under a corrected DOI is mapped back to the DOI
   we asked for, per `doi_corrections`: ask at corrected_doi, file under doi.

3. **What?** Each zip holds one XML - full text where IOP has it, metadata
   where it does not - and one PDF. Which is which decides whether Stage 2 can
   read the article from the XML or has to fall back to the PDF, so it is
   counted across the whole delivery rather than guessed from one sample. The
   same test the extractor uses to route a document to its JATS parser is
   applied here, so "the extractor will take it" is measured, not assumed.

Writes one row per zip to data/reports/iop-delivery-check.csv.
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import sqlite3
import sys
import zipfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console

from rwpipe import config, extract

console = Console()

EXPECT_N = 2_577
EXPECT_BYTES = 1_286_903_233
DEFAULT_DIR = Path(r"E:\5703 capstone\iop-delivery")
IOP_LIST = Path(r"E:\5703 capstone\CAMPUS-KIT\IOP-DOIs-for-TDM.csv")
OUT = config.DATA / "reports" / "iop-delivery-check.csv"

# Identity helpers live in rwpipe.iop, shared with ingest_iop.py, so the
# checker and the ingester cannot come to disagree about which DOI a file is.
from rwpipe.iop import NAME_DOI, root_of, unescape, xml_doi  # noqa: E402


def load_expected(conn) -> tuple[set[str], dict[str, str]]:
    want: set[str] = set()
    if IOP_LIST.exists():
        with IOP_LIST.open(encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                d = (r.get("doi") or "").strip().lower()
                if d:
                    want.add(d)
    back = {c.lower(): d.lower() for d, c in
            conn.execute("SELECT doi, corrected_doi FROM doi_corrections")}
    return want, back


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    ap.add_argument("--sample-extract", type=int, default=25,
                    help="run the real extractor on this many full-text XMLs")
    args = ap.parse_args()

    zips = sorted(args.dir.glob("*.zip"))
    total = sum(p.stat().st_size for p in zips)
    console.print(f"[bold]{len(zips):,} zip files, {total:,} bytes[/bold]  "
                  f"(expected {EXPECT_N:,} and {EXPECT_BYTES:,})")

    conn = sqlite3.connect(f"file:{config.DATA / 'interim' / 'pipeline.sqlite'}"
                           "?mode=ro", uri=True)
    conn.execute("PRAGMA busy_timeout=60000")
    want, corrected_to_orig = load_expected(conn)

    rows = []
    kinds: Counter = Counter()
    schemas: Counter = Counter()
    how_doi: Counter = Counter()
    problems: Counter = Counter()
    seen: set[str] = set()
    sampled = 0
    sample_bodies: list[int] = []

    for i, p in enumerate(zips, 1):
        rec = {"file": p.name, "bytes": p.stat().st_size, "zip_ok": 0,
               "n_xml": 0, "n_pdf": 0, "other_members": "",
               "name_doi": "", "xml_doi": "", "xml_doi_how": "",
               "filed_doi": "", "doi_agree": 0, "in_request": 0,
               "schema": "", "public_id": "", "fulltext": 0,
               "routes_to_jats": 0, "xml_chars": 0, "pdf_bytes": 0,
               "problem": ""}
        m = NAME_DOI.match(p.name)
        rec["name_doi"] = unescape(m.group(1)) if m else ""

        try:
            with zipfile.ZipFile(p) as z:
                bad = z.testzip()
                if bad:
                    raise zipfile.BadZipFile(f"CRC failure in {bad}")
                names = [n for n in z.namelist() if not n.endswith("/")]
                xmls = [n for n in names if n.lower().endswith(".xml")]
                pdfs = [n for n in names if n.lower().endswith(".pdf")]
                rec["n_xml"], rec["n_pdf"] = len(xmls), len(pdfs)
                rec["other_members"] = ";".join(
                    n for n in names if n not in xmls and n not in pdfs)[:200]
                if pdfs:
                    rec["pdf_bytes"] = z.getinfo(pdfs[0]).file_size
                xml = ""
                if xmls:
                    raw = z.read(xmls[0])
                    xml = raw.decode("utf-8", "replace")
            rec["zip_ok"] = 1
        except Exception as exc:                           # noqa: BLE001
            rec["problem"] = f"zip: {type(exc).__name__}: {exc}"[:200]
            problems["zip will not open or fails CRC"] += 1
            rows.append(rec)
            continue

        if rec["n_xml"] != 1 or rec["n_pdf"] != 1:
            problems[f"members: {rec['n_xml']} xml, {rec['n_pdf']} pdf"] += 1
            rec["problem"] = f"expected 1 xml + 1 pdf, got {rec['n_xml']} + {rec['n_pdf']}"

        if xml:
            rec["xml_chars"] = len(xml)
            d, how = xml_doi(xml)
            rec["xml_doi"], rec["xml_doi_how"] = d, how
            how_doi[how] += 1
            schema, pub = root_of(xml)
            rec["schema"], rec["public_id"] = schema, pub
            schemas[schema if not pub else f"{schema}  {pub}"] += 1
            rec["fulltext"] = int(bool(re.search(r"<body[\s>]", xml)))
            kinds["full text" if rec["fulltext"] else "metadata only"] += 1
            rec["routes_to_jats"] = int(bool(
                extract._JATS_MARK.match(xml.lstrip()[:120])
                and "<article-title" in xml[:400_000]))

            # Identity: the XML is the authority, the filename a cross-check.
            ident = d or rec["name_doi"]
            rec["doi_agree"] = int(bool(d) and d == rec["name_doi"])
            if d and rec["name_doi"] and d != rec["name_doi"]:
                problems["filename DOI and XML DOI disagree"] += 1
                rec["problem"] = (rec["problem"] + "; " if rec["problem"] else "") + \
                    "filename and XML DOI disagree"
            if not d:
                problems["no DOI found in the XML"] += 1
            # A corrected DOI is filed under the DOI we originally asked for.
            rec["filed_doi"] = corrected_to_orig.get(ident, ident)
            rec["in_request"] = int(rec["filed_doi"] in want)
            if rec["filed_doi"] in seen:
                problems["same DOI delivered twice"] += 1
            seen.add(rec["filed_doi"])

            if rec["fulltext"] and rec["routes_to_jats"] and sampled < args.sample_extract:
                e = extract.extract(xml, kind="original")
                sample_bodies.append(len(e.body or ""))
                sampled += 1

        rows.append(rec)
        if i % 500 == 0:
            console.print(f"[dim]  {i:,}/{len(zips):,}[/dim]")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["file"])
        w.writeheader()
        w.writerows(rows)

    ok = sum(r["zip_ok"] for r in rows)
    console.print()
    console.print("[bold]1. complete?[/bold]")
    console.print(f"   files      {len(zips):>12,}  {'ok' if len(zips) == EXPECT_N else 'MISMATCH'}")
    console.print(f"   bytes      {total:>12,}  {'ok' if total == EXPECT_BYTES else 'MISMATCH'}")
    console.print(f"   open + CRC {ok:>12,}  {'ok' if ok == len(zips) else 'SOME FAIL'}")

    console.print("[bold]2. whose?[/bold]")
    console.print(f"   DOI read from the XML via: {dict(how_doi)}")
    agree = sum(r["doi_agree"] for r in rows)
    console.print(f"   filename agrees with XML   {agree:,}")
    inreq = sum(r["in_request"] for r in rows)
    console.print(f"   in the list we sent        {inreq:,} of {len(want):,} requested")
    missing = sorted(want - seen)
    extra = sorted(seen - want)
    console.print(f"   requested, not delivered   {len(missing):,}")
    console.print(f"   delivered, not requested   {len(extra):,}")

    console.print("[bold]3. what?[/bold]")
    for k, n in kinds.most_common():
        console.print(f"   {k:<28}{n:>6,}")
    jats = sum(r["routes_to_jats"] for r in rows)
    console.print(f"   extractor routes to JATS   {jats:,} of {sum(kinds.values()):,}")
    console.print("   schemas:")
    for s, n in schemas.most_common(6):
        console.print(f"     {n:>6,}  {s}")
    if sample_bodies:
        sb = sorted(sample_bodies)
        console.print(f"   extractor on {len(sb)} full-text XMLs: body chars "
                      f"min {sb[0]:,}  median {sb[len(sb)//2]:,}  max {sb[-1]:,}")

    if problems:
        console.print("[bold red]problems[/bold red]")
        for k, n in problems.most_common():
            console.print(f"   {n:>6,}  {k}")
    else:
        console.print("[green]no problems[/green]")

    if missing:
        console.print(f"[dim]first missing: {', '.join(missing[:5])}[/dim]")
    console.print(f"\n[dim]per-file detail: {OUT}[/dim]")

    complete = (len(zips) == EXPECT_N and total == EXPECT_BYTES and ok == len(zips))
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
