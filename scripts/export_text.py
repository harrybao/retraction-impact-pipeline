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

Two columns say what a row is before anyone filters it:

    source_kind       where the document came from. `teammate-tandf` is the
                      Taylor & Francis hand-off, which can be reported apart or
                      removed as one unit.
    shared_body_kind  `shared_bodies.kind`, empty when the body is the paper's
                      own. `notice-replaces-article` pages carry a real notice:
                      dropping every shared-body row from a notice analysis
                      throws away 10,655 notices, which is what the group's plan
                      of 2026-09-14 would have done.
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
              "source_path", "extracted_at", "error",
              "source_kind", "shared_body_kind"]


def text_rows(conn):
    """Export only the current verified selection, after a complete extraction."""
    if not conn.in_transaction:
        conn.execute("BEGIN")
    missing = conn.execute("""SELECT COUNT(*) FROM documents d
                              LEFT JOIN document_text t ON t.doi=d.doi
                              WHERE d.usable=1 AND t.doi IS NULL""").fetchone()[0]
    if missing:
        raise RuntimeError(f"Stage 2 export refused: {missing} usable documents have no extracted row")
    versions = conn.execute("""SELECT DISTINCT t.extractor_version FROM document_text t
                                JOIN documents d ON d.doi=t.doi WHERE d.usable=1""").fetchall()
    if len(versions) > 1:
        raise RuntimeError("Stage 2 export refused: usable documents have mixed extractor versions")
    return conn.execute("""SELECT t.doi, t.doc_role, t.host, t.capture_format, t.quality,
                                  t.extractor_version, t.title, t.abstract, t.body,
                                  t.notice, t.notice_method, t.references_n, t.method,
                                  t.source_path, t.extracted_at, t.error,
                                  d.source_kind, s.kind
                           FROM document_text t
                           JOIN documents d ON d.doi = t.doi AND d.usable=1
                           LEFT JOIN shared_bodies s ON s.doi = t.doi
                           ORDER BY t.doi""")


def main() -> int:
    conn = db.connect()
    conn.row_factory = None
    rows = text_rows(conn)
    OUT.mkdir(parents=True, exist_ok=True)

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
                     "notice_chars", "source_kind", "shared_body_kind", "notice"])
        for (doi, role, host, fmt, qual, ver, title, abstract, body, notice,
             nmethod, refs, method, path, at, err, kind, shared) in rows:
            n += 1
            body = body or ""
            notice = notice or ""
            kind = kind or ""
            shared = shared or ""
            wi.writerow([doi, role, host, fmt, qual, ver, title,
                         len(abstract or ""), len(body), len(notice),
                         nmethod, refs, method, path, at, err, kind, shared])
            if notice:
                with_notice += 1
                wn.writerow([doi, role, host, qual, nmethod, len(notice),
                             kind, shared, notice])
            if body:
                with_body += 1
            ft.write(json.dumps({
                "doi": doi, "doc_role": role, "host": host, "quality": qual,
                "extractor_version": ver, "title": title,
                "abstract": abstract or "", "body": body, "notice": notice,
                "notice_method": nmethod, "references_n": refs,
                "method": method, "source_path": path, "extracted_at": at,
                "source_kind": kind, "shared_body_kind": shared,
            }, ensure_ascii=False) + "\n")

    console.print(f"[bold]{n:,} documents[/bold]  "
                  f"{with_body:,} with a body, {with_notice:,} with a notice")
    for p in (idx_p, not_p, txt_p):
        console.print(f"  {p.name:<26} {p.stat().st_size/1e6:>8.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
