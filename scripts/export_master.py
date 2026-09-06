"""Export one master CSV joining every stage, for sharing with the team.

    python scripts/export_master.py

One row per Retraction Watch record, carrying:

  * the cleaned metadata (title, journal, publisher, dates, reasons, subjects)
  * the Stage 1a crawl result for BOTH pages - the original article and its
    retraction notice - each with its own status, host, capture format and
    failure reason
  * the Stage 1b engagement figures, split before / during / after the
    retraction year
  * what the official APIs recovered for pages the crawler could not reach

The two pages are kept as separate column groups rather than collapsed into a
summary. "Did we get this paper" has two different answers - the article and
the notice are different artefacts and frequently have different outcomes, and
the notice is the one that says *why* it was retracted.
"""
from __future__ import annotations

import csv
from datetime import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console

from rwpipe import config, db

console = Console()

COLUMNS = [
    # --- what the paper is -------------------------------------------------
    "record_id", "title", "journal", "publisher", "country",
    "retraction_nature", "reason", "subject",
    "original_date", "retraction_date", "retraction_year", "days_to_retraction",
    # --- the original article page ----------------------------------------
    "original_doi", "orig_status", "orig_usable", "orig_host", "orig_format",
    "orig_size_kb", "orig_file", "orig_error",
    # --- the retraction notice page ---------------------------------------
    "retraction_doi", "notice_status", "notice_usable", "notice_host", "notice_format",
    "notice_size_kb", "notice_file", "notice_error",
    # --- overall ----------------------------------------------------------
    "pages_captured",          # 0, 1 or 2
    # --- Stage 1b engagement ----------------------------------------------
    "cited_by_count", "citations_before", "citations_during", "citations_after",
    # `citation_window_ok` is exported next to the flag it qualifies. Without
    # it a reader cannot tell "not cited afterwards" from "the window does not
    # reach the retraction", and 25,797 rows are the second thing.
    "citation_window_ok", "cited_after_retraction", "wikipedia_count",
    # --- Stage 1b attention: Altmetric, and Wikipedia across editions ------
    # in_altmetric = 0 means Altmetric has no record of the paper, i.e. it
    # drew no traceable public attention. That is a finding, not a gap, and
    # is why the column is 0/1 rather than blank.
    "in_altmetric", "altmetric_score", "news_count", "social_count",
    "wikipedia_all_langs", "wikipedia_editions",
    # --- API fallback for unreachable pages -------------------------------
    "api_sources", "api_abstract_chars", "api_abstract_source",
    "api_update_types", "api_is_oa", "api_pmid", "api_has_tdm_link",
]

SQL = """
SELECT
  r.record_id, r.title, r.journal, r.publisher, r.country,
  r.retraction_nature, r.reason, r.subject,
  r.original_date, r.retraction_date,
  r.original_doi, r.retraction_doi,

  od.status      AS orig_status,
  od.usable      AS orig_usable,
  od.final_host  AS orig_host,
  od.capture_format AS orig_format,
  od.gz_bytes    AS orig_gz,
  od.mhtml_path  AS orig_file,
  od.error       AS orig_error,

  rd.status      AS notice_status,
  rd.usable      AS notice_usable,
  rd.final_host  AS notice_host,
  rd.capture_format AS notice_format,
  rd.gz_bytes    AS notice_gz,
  rd.mhtml_path  AS notice_file,
  rd.error       AS notice_error,

  e.cited_by_count, e.citations_before, e.citations_during,
  e.citations_after, e.wikipedia_count, e.retraction_year,
  -- Whether the yearly figures reach the retraction at all. Exported so a
  -- reader can filter, instead of having to trust a flag that cannot say
  -- "not measured".
  e.window_covers_retraction AS citation_window_ok,

  am.found      AS in_altmetric,
  am.score      AS altmetric_score,
  am.news_count AS news_count,
  am.social_count AS social_count,
  (SELECT COUNT(*)          FROM wikipedia_cites w WHERE w.doi = r.original_doi)
                AS wikipedia_all_langs,
  (SELECT COUNT(DISTINCT w.lang) FROM wikipedia_cites w WHERE w.doi = r.original_doi)
                AS wikipedia_editions,

  a.sources         AS api_sources,
  LENGTH(a.abstract) AS api_abstract_chars,
  a.abstract_source AS api_abstract_source,
  a.update_types    AS api_update_types,
  a.is_oa           AS api_is_oa,
  a.pmid            AS api_pmid,
  a.tdm_links       AS api_tdm

FROM records r
LEFT JOIN documents  od ON od.doi = r.original_doi
LEFT JOIN documents  rd ON rd.doi = r.retraction_doi
LEFT JOIN engagement e   ON e.doi  = r.original_doi
LEFT JOIN api_records a  ON a.doi  = r.original_doi
LEFT JOIN altmetric   am ON am.doi = r.original_doi
ORDER BY r.record_id
"""


def kb(n) -> str:
    return "" if not n else str(round(n / 1024))


def main() -> int:
    out_dir = config.DATA / "clean"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "cs43_master.csv"

    conn = db.connect()
    rows = conn.execute(SQL)

    n = captured2 = captured1 = 0
    # utf-8-sig so Excel opens it with the right encoding on Windows
    with out.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(COLUMNS)
        for r in rows:
            # `status == 'done'` only says the request completed. 10,245 of
            # those files are publisher refusal pages that pass every
            # structural check and contain no article, so counting them here
            # would overstate what Stage 2 can actually read. `usable` is the
            # column that survived identity verification.
            got = (r["orig_usable"] == 1) + (r["notice_usable"] == 1)
            captured2 += got == 2
            captured1 += got == 1

            # Retraction Watch writes MM/DD/YYYY, sometimes with a time.
            # This was previously an `if` that assigned "" in both branches,
            # so the column shipped blank for all 71,698 rows while the header
            # promised a number.
            days = ""
            if r["original_date"] and r["retraction_date"]:
                try:
                    a = datetime.strptime(str(r["original_date"]).split()[0], "%m/%d/%Y")
                    b = datetime.strptime(str(r["retraction_date"]).split()[0], "%m/%d/%Y")
                    days = (b - a).days
                except (ValueError, IndexError):
                    days = ""

            w.writerow([
                r["record_id"], r["title"], r["journal"], r["publisher"], r["country"],
                r["retraction_nature"], r["reason"], r["subject"],
                r["original_date"], r["retraction_date"], r["retraction_year"], days,

                r["original_doi"], r["orig_status"], r["orig_usable"], r["orig_host"], r["orig_format"],
                kb(r["orig_gz"]), r["orig_file"], r["orig_error"],

                r["retraction_doi"], r["notice_status"], r["notice_usable"], r["notice_host"],
                r["notice_format"], kb(r["notice_gz"]), r["notice_file"], r["notice_error"],

                got,

                r["cited_by_count"], r["citations_before"], r["citations_during"],
                r["citations_after"],
                # Blank, not 0, where the window cannot answer the question.
                r["citation_window_ok"],
                ("" if not r["citation_window_ok"]
                 else 1 if (r["citations_after"] or 0) > 0 else 0),
                r["wikipedia_count"],

                r["in_altmetric"], r["altmetric_score"],
                r["news_count"], r["social_count"],
                r["wikipedia_all_langs"], r["wikipedia_editions"],

                r["api_sources"], r["api_abstract_chars"], r["api_abstract_source"],
                r["api_update_types"], r["api_is_oa"], r["api_pmid"],
                1 if r["api_tdm"] else 0,
            ])
            n += 1
    conn.close()

    mb = out.stat().st_size / 1e6
    console.print(f"[bold]{out}[/bold]")
    console.print(f"  {n:,} rows x {len(COLUMNS)} columns   {mb:.0f} MB")
    console.print(f"  both pages captured : {captured2:,} ({captured2/n*100:.0f}%)")
    console.print(f"  one page captured   : {captured1:,} ({captured1/n*100:.0f}%)")
    console.print(f"  neither             : {n-captured2-captured1:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
