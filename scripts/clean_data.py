"""Stage 0b - produce the cleaned, analysis-ready dataset.

    python scripts/clean_data.py

Writes to data/clean/:
    records_clean.csv     one tidy row per record, typed, with quality flags
    subjects.csv          long format: one row per (record, subject)
    reasons.csv           long format: one row per (record, retraction reason)
    countries.csv         long format: one row per (record, country)
    authors.csv           long format: one row per (record, author)
    data_quality.md       what was found and what was done about it
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console

from rwpipe import config
from rwpipe.clean import (TEXT_COLUMNS, clean_records, explode_list,
                          quality_summary)
from rwpipe.csvload import load_records

console = Console()
OUT = config.DATA / "clean"

FLAG_NOTES = {
    "flag_no_original_doi": "no usable original DOI - that side cannot be fetched",
    "flag_no_retraction_doi": "no usable retraction DOI - that side cannot be fetched",
    "flag_nothing_to_fetch": "neither DOI usable - excluded from the crawl entirely",
    "flag_same_doi_both_sides": "same DOI in both columns - one page, not two",
    "flag_shared_original_doi": "original DOI shared with another record - usually an "
                               "Expression of concern later upgraded to a Retraction; "
                               "kept separate on purpose",
    "flag_unparsed_retraction_date": "retraction date could not be parsed",
    "flag_unparsed_original_date": "publication date could not be parsed",
    "flag_retraction_before_publication": "notice predates the paper - impossible",
    "flag_no_reason": "no retraction reason recorded",
    "flag_no_subject": "no subject area recorded",
}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    console.print(f"[bold]reading[/bold] {config.RAW_CSV}")
    raw = load_records()
    console.print(f"  {len(raw):,} records after dropping blank rows")

    clean = clean_records(raw)
    console.print(f"  {len(clean):,} records cleaned, {len(clean.columns)} columns")

    # list columns are Python lists - flatten to strings for the CSV, and keep
    # the authoritative long-format tables alongside
    flat = clean.copy()
    for col in [c for c in flat.columns if c.endswith("_list")]:
        flat[col] = flat[col].map(lambda xs: "; ".join(xs))
    flat.to_csv(OUT / "records_clean.csv", index=False, encoding="utf-8")
    console.print(f"  -> records_clean.csv")

    # CSV carries no types, and several columns look numeric but are not.
    # Ship the dtypes next to the data so nobody has to know that.
    import json
    (OUT / "dtypes.json").write_text(
        json.dumps({c: "string" for c in TEXT_COLUMNS}, indent=2), encoding="utf-8")
    console.print(f"  -> dtypes.json")

    for field, fname in (("subject", "subjects"), ("reason", "reasons"),
                         ("country", "countries"), ("author", "authors")):
        ex = explode_list(clean, field)
        ex.to_csv(OUT / f"{fname}.csv", index=False, encoding="utf-8")
        console.print(f"  -> {fname}.csv  ({len(ex):,} rows)")

    flags = quality_summary(clean)
    console.print("\n[bold]quality flags[/bold]")
    for k, v in sorted(flags.items(), key=lambda kv: -kv[1]):
        console.print(f"  {v:>7,}  {k}")

    # --- report -----------------------------------------------------------
    n = len(clean)
    lines = [
        "# Data quality report - Retraction Watch CSV",
        "",
        f"Generated {date.today().isoformat()} by `scripts/clean_data.py`.",
        f"Source: `{config.RAW_CSV.name}`",
        "",
        "## Volumes",
        "",
        "| | |",
        "|---|---|",
        "| Rows in the raw file | 71,944 |",
        "| Blank trailing rows dropped | 246 |",
        f"| Records cleaned | {n:,} |",
        "",
        "## Flags",
        "",
        "Nothing here is silently deleted. Every issue is flagged on the record so",
        "downstream stages can decide, and so the final report can quantify the gap.",
        "",
        "| flag | records | % | meaning |",
        "|---|---:|---:|---|",
    ]
    for k, v in sorted(flags.items(), key=lambda kv: -kv[1]):
        lines.append(f"| `{k}` | {v:,} | {v / n * 100:.1f}% | {FLAG_NOTES.get(k, '')} |")

    lines += [
        "",
        "## Reading this file",
        "",
        "**Use `rwpipe.clean.read_clean()`, or pass `dtypes.json` to pandas.**",
        "",
        "CSV carries no types. `original_doi_prefix` and `retraction_doi_prefix` look",
        "numeric, so a plain `read_csv` turns them into floats and silently drops a",
        "trailing zero: `10.3390` (MDPI) becomes `10.339`, `10.1080` (Taylor & Francis)",
        "becomes `10.108`. 82 prefixes covering 4,781 pages are affected, and three",
        "pairs collide outright - `10.1113`/`10.11130`, `10.1556`/`10.15560`,",
        "`10.1557`/`10.15570`. That corruption is invisible until an analysis",
        "silently counts the wrong publisher.",
        "",
        "## Normalisation applied",
        "",
        "- **Dates** parsed to ISO. The file mixes `4/10/2026 0:00` with",
        "  `6/24/1756 12:00:00 AM`; both are handled. The 18th-century dates are",
        "  genuine (*Treatise upon Electricity*, 1753), not corruption.",
        "- **DOIs** lowercased, resolver prefixes stripped, validated against",
        "  `10.<registrant>/<suffix>`. `unavailable`, `0` and blanks become null.",
        "- **PubMed IDs** - `0` and blank both mean absent, normalised to null.",
        "- **List fields** (Subject, Reason, Author, Country, Institution,",
        "  ArticleType) split on `;`. Every Subject and Reason cell carries a",
        "  trailing `;`, so a naive split leaves an empty tail on all rows.",
        "- **Subject codes** separated from labels: `(BLS) Biochemistry` becomes",
        "  code `BLS`, label `Biochemistry`.",
        "",
        "## Deliberately not done",
        "",
        "Records sharing an `OriginalPaperDOI` are **not** merged. The dominant",
        "pattern is an `Expression of concern` later followed by a `Retraction` on",
        "the same paper - the update lifecycle this project studies. Merging would",
        "destroy the signal, so they are flagged instead.",
        "",
        "## Long-format tables",
        "",
        "Semicolon-packed cells cannot be grouped or counted. `subjects.csv`,",
        "`reasons.csv`, `countries.csv` and `authors.csv` give one row per",
        "(record, item) so Stage 3 can aggregate directly.",
    ]
    (OUT / "data_quality.md").write_text("\n".join(lines), encoding="utf-8")
    console.print(f"\n  -> data_quality.md")
    console.print(f"\n[bold]written to[/bold] {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
