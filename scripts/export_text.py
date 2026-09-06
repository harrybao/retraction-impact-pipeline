"""Stage 2 out of the database and into files people can download.

    python scripts/export_text.py

Until now the extracted text existed only inside `data/interim/pipeline.sqlite`,
a 4 GB file that is gitignored and that nobody outside this machine has. The
share bundle carried the crawl and the engagement data and none of the text,
so "Stage 2 is done" was not a statement anyone else could check.

Three files, because one would be the wrong size for every use:

    stage2_index.csv        one row per document, no body text (~12 MB)
                            browsable on GitHub: what was extracted, how well,
                            from which host, under which extractor version.

    stage2_notices.csv.gz   the retraction notices (~91 MB raw, ~25 MB gz)
                            what Stage 3 compares the news coverage against.

    stage2_fulltext.jsonl.gz  everything, one JSON object per line
                            (~850 MB raw). JSONL rather than CSV because
                            article bodies contain every delimiter a CSV can
                            use, and a 700 MB CSV that silently mis-parses one
                            row in ten thousand is worse than no file.

`body` is deliberately absent from the two small files. It is 738 MB of the
853 MB, and most questions - how many notices did we get, from whom, at what
quality - do not need it.
"""
from __future__ import annotations

import csv
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console

from rwpipe import config, db

console = Console()

OUT = config.DATA / "clean"

INDEX_COLS = ["doi", "doc_role", "host", "capture_format", "quality",
              "extractor_version", "title", "abstract_chars", "body_chars",
              "notice_chars", "notice_method", "references_n", "method",
              "source_path", "extracted_at", "error"]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    conn = db.connect()
    conn.row_factory = None

    rows = conn.execute("""SELECT doi, doc_role, host, capture_format, quality,
                                  extractor_version, title, abstract, body,
                                  notice, notice_method, references_n, method,
                                  source_path, extracted_at, error
                           FROM document_text ORDER BY doi""")

    idx_p = OUT / "stage2_index.csv"
    not_p = OUT / "stage2_notices.csv.gz"
    txt_p = OUT / "stage2_fulltext.jsonl.gz"

    n = with_notice = with_body = 0
    with idx_p.open("w", encoding="utf-8", newline="") as fi, \
         gzip.open(not_p, "wt", encoding="utf-8", newline="") as fn, \
         gzip.open(txt_p, "wt", encoding="utf-8") as ft:
        wi = csv.writer(fi); wi.writerow(INDEX_COLS)
        wn = csv.writer(fn)
        wn.writerow(["doi", "doc_role", "host", "quality", "notice_method",
                     "notice_chars", "notice"])
        for (doi, role, host, fmt, qual, ver, title, abstract, body, notice,
             nmethod, refs, method, path, at, err) in rows:
            n += 1
            body = body or ""
            notice = notice or ""
            wi.writerow([doi, role, host, fmt, qual, ver, title,
                         len(abstract or ""), len(body), len(notice),
                         nmethod, refs, method, path, at, err])
            if notice:
                with_notice += 1
                wn.writerow([doi, role, host, qual, nmethod, len(notice), notice])
            if body:
                with_body += 1
            ft.write(json.dumps({
                "doi": doi, "doc_role": role, "host": host, "quality": qual,
                "extractor_version": ver, "title": title,
                "abstract": abstract or "", "body": body, "notice": notice,
                "notice_method": nmethod, "references_n": refs,
                "method": method, "source_path": path, "extracted_at": at,
            }, ensure_ascii=False) + "\n")

    console.print(f"[bold]{n:,} documents[/bold]  "
                  f"{with_body:,} with a body, {with_notice:,} with a notice")
    for p in (idx_p, not_p, txt_p):
        console.print(f"  {p.name:<26} {p.stat().st_size/1e6:>8.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
