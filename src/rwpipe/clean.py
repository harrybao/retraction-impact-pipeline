"""Stage 0b - normalise the Retraction Watch CSV into an analysis-ready form.

This cleans the *metadata*. Cleaning the scraped pages is a separate, later
stage that cannot run until Stage 1 has downloaded them.

What this deliberately does NOT do is collapse records that share an
OriginalPaperDOI. 2,360 DOIs are shared by 5,245 records, and the common
pattern is `Expression of concern` followed later by `Retraction` on the same
paper - the update lifecycle this project exists to study. Merging them would
destroy the signal. They are flagged instead.
"""
from __future__ import annotations

import re

import pandas as pd

# Retraction Watch mixes two date shapes in the same column:
#   4/10/2026 0:00          (the common one)
#   6/24/1756 12:00:00 AM   (rare, and the dates really are 18th century)
_DATE_FORMATS = ("%m/%d/%Y %H:%M", "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y")

# Subjects are coded like "(BLS) Biochemistry" - BLS/HSC/PHY/SOC/ENV/BUS.
_SUBJECT_RE = re.compile(r"^\((?P<code>[A-Z]{2,4})\)\s*(?P<label>.+)$")

# Columns that LOOK numeric but must stay text. A DOI prefix written bare to
# CSV round-trips through float on read: 10.3390 (MDPI) becomes 10.339 and
# 10.1080 (T&F) becomes 10.108. 82 prefixes covering 4,781 pages are affected,
# and three pairs collide outright - 10.1113 with 10.11130, 10.1556 with
# 10.15560, 10.1557 with 10.15570. Always read this file through read_clean().
TEXT_COLUMNS = (
    "original_doi", "retraction_doi",
    "original_doi_prefix", "retraction_doi_prefix",
    "title", "journal", "publisher", "retraction_nature", "paywalled",
    "notes", "urls",
)

LIST_COLUMNS = {
    "Subject": "subject",
    "Reason": "reason",
    "Author": "author",
    "Country": "country",
    "Institution": "institution",
    "ArticleType": "article_type",
}


def parse_date(value) -> pd.Timestamp | None:
    """Parse one date cell, tolerating both formats present in the file."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return pd.to_datetime(s, format=fmt)
        except (ValueError, TypeError):
            continue
    return pd.to_datetime(s, errors="coerce")


def split_list(value) -> list[str]:
    """Split a semicolon-delimited cell.

    Every Subject and Reason cell in the file carries a trailing ';', so a
    naive split leaves an empty tail on all 71,698 rows.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    parts = (p.strip() for p in str(value).split(";"))
    return [p for p in parts if p]


def split_subject(item: str) -> tuple[str | None, str]:
    """'(BLS) Biochemistry' -> ('BLS', 'Biochemistry')."""
    m = _SUBJECT_RE.match(item.strip())
    return (m.group("code"), m.group("label").strip()) if m else (None, item.strip())


def clean_records(df: pd.DataFrame) -> pd.DataFrame:
    """Return one tidy row per record, with quality flags attached."""
    out = pd.DataFrame(index=df.index)
    out["record_id"] = df["Record ID"].astype(int)
    out["title"] = df["Title"].astype(str).str.strip()
    out["journal"] = df["Journal"].astype(str).str.strip().str.rstrip(";").str.strip()
    out["publisher"] = df["Publisher"].astype(str).str.strip()
    out["retraction_nature"] = df["RetractionNature"].astype(str).str.strip()
    out["paywalled"] = (
        df["Paywalled"].astype(str).str.strip().replace({"": "Unknown"})
    )
    out["notes"] = df["Notes"].astype(str).str.strip()
    out["urls"] = df["URLS"].astype(str).str.strip()

    out["original_doi"] = df["original_doi"]
    out["retraction_doi"] = df["retraction_doi"]
    out["original_doi_prefix"] = df["original_doi"].str.split("/").str[0]
    out["retraction_doi_prefix"] = df["retraction_doi"].str.split("/").str[0]

    out["retraction_date"] = df["RetractionDate"].map(parse_date)
    out["original_date"] = df["OriginalPaperDate"].map(parse_date)
    out["days_to_retraction"] = (
        out["retraction_date"] - out["original_date"]
    ).dt.days
    out["retraction_year"] = out["retraction_date"].dt.year
    out["original_year"] = out["original_date"].dt.year

    # PubMed IDs use 0 and '' interchangeably for "absent"
    for src, dst in (("OriginalPaperPubMedID", "original_pmid"),
                     ("RetractionPubMedID", "retraction_pmid")):
        n = pd.to_numeric(df[src], errors="coerce")
        out[dst] = n.where(n > 0).astype("Int64")

    for src, dst in LIST_COLUMNS.items():
        out[dst + "_list"] = df[src].map(split_list)
        out["n_" + dst] = out[dst + "_list"].map(len)

    # --- quality flags: describe the data, never silently drop it ----------
    out["flag_no_original_doi"] = out["original_doi"].isna()
    out["flag_no_retraction_doi"] = out["retraction_doi"].isna()
    out["flag_same_doi_both_sides"] = (
        out["original_doi"].notna() & (out["original_doi"] == out["retraction_doi"])
    )
    out["flag_unparsed_retraction_date"] = out["retraction_date"].isna()
    out["flag_unparsed_original_date"] = out["original_date"].isna()
    # A notice dated before the paper it retracts is impossible; -1 tolerates
    # same-day and timezone slop.
    out["flag_retraction_before_publication"] = out["days_to_retraction"] < -1
    out["flag_no_reason"] = out["n_reason"] == 0
    out["flag_no_subject"] = out["n_subject"] == 0
    out["flag_nothing_to_fetch"] = (
        out["flag_no_original_doi"] & out["flag_no_retraction_doi"]
    )
    shared = out["original_doi"].dropna().duplicated(keep=False)
    out["flag_shared_original_doi"] = shared.reindex(out.index, fill_value=False)

    return out


def explode_list(clean: pd.DataFrame, field: str) -> pd.DataFrame:
    """Long-format table: one row per (record, list item).

    Semicolon-packed cells cannot be grouped or counted; this is the shape
    Stage 3 needs to ask "which subjects retract most" or "which reasons
    co-occur".
    """
    col = field + "_list"
    ex = clean[["record_id", col]].explode(col).dropna(subset=[col])
    ex = ex.rename(columns={col: field})
    ex = ex[ex[field].astype(str).str.strip() != ""]
    if field == "subject":
        codes = ex["subject"].map(split_subject)
        ex["subject_code"] = codes.map(lambda t: t[0])
        ex["subject_label"] = codes.map(lambda t: t[1])
    return ex.reset_index(drop=True)


def quality_summary(clean: pd.DataFrame) -> dict[str, int]:
    flags = [c for c in clean.columns if c.startswith("flag_")]
    return {f: int(clean[f].sum()) for f in flags}


def read_clean(path=None):
    """Read records_clean.csv with the dtypes that keep DOI prefixes intact."""
    import pandas as _pd

    from . import config
    target = path or (config.DATA / "clean" / "records_clean.csv")
    df = _pd.read_csv(
        target,
        dtype={c: "string" for c in TEXT_COLUMNS},
        parse_dates=["retraction_date", "original_date"],
        low_memory=False,
    )
    return df
