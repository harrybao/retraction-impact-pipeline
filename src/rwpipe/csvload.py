"""Stage 0 - load and validate the Retraction Watch CSV.

The client's rule: if a DOI column is empty / 'unavailable' / 0 there is no
page to fetch, so that side is skipped. A record with neither DOI usable is
dropped from the crawl frontier entirely.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

from . import config

# A DOI is 10.<registrant>/<suffix>. The registrant prefix is our routing key:
# it identifies the publisher far more reliably than the free-text Publisher
# column (1,713 distinct spellings in this dataset).
DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
_NULLISH = {"", "0", "unavailable", "none", "n/a", "na", "nan", "-"}

COLUMNS = [
    "Record ID", "Title", "Subject", "Institution", "Journal", "Publisher",
    "Country", "Author", "URLS", "ArticleType", "RetractionDate",
    "RetractionDOI", "RetractionPubMedID", "OriginalPaperDate",
    "OriginalPaperDOI", "OriginalPaperPubMedID", "RetractionNature",
    "Reason", "Paywalled", "Notes",
]


def clean_doi(value) -> str | None:
    """Normalise a DOI cell, or return None if it is not usable.

    Handles the shapes seen in this dataset: bare DOIs, full https://doi.org/
    URLs, the literals 'unavailable'/'Unavailable', 0, and blanks.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()
    if s.lower() in _NULLISH:
        return None
    # strip a resolver prefix if the cell holds a URL rather than a bare DOI
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.I)
    s = s.strip().rstrip(".;,")
    if not DOI_RE.match(s):
        return None
    return s.lower()


def doi_prefix(doi: str) -> str:
    return doi.split("/", 1)[0]


@dataclass(frozen=True)
class PageTask:
    """One page to render: a record's original article or its retraction notice."""
    record_id: int
    kind: str          # 'original' | 'retraction'
    doi: str
    prefix: str

    @property
    def url(self) -> str:
        return f"https://doi.org/{self.doi}"


def load_records(csv_path=None) -> pd.DataFrame:
    """Read the raw CSV and attach cleaned DOI columns."""
    path = csv_path or config.RAW_CSV
    df = pd.read_csv(path, dtype=str, keep_default_na=False, low_memory=False)
    # the file has a trailing comma on every row -> one unnamed empty column
    df = df.loc[:, [c for c in df.columns if not c.startswith("Unnamed")]]

    df["original_doi"] = df["OriginalPaperDOI"].map(clean_doi)
    df["retraction_doi"] = df["RetractionDOI"].map(clean_doi)
    df["Record ID"] = pd.to_numeric(df["Record ID"], errors="coerce")
    df = df.dropna(subset=["Record ID"])
    df["Record ID"] = df["Record ID"].astype(int)
    return df


def build_tasks(df: pd.DataFrame) -> list[PageTask]:
    """Expand records into the crawl frontier, skipping unusable DOIs."""
    tasks: list[PageTask] = []
    for rid, odoi, rdoi in zip(df["Record ID"], df["original_doi"], df["retraction_doi"]):
        for kind, doi in (("original", odoi), ("retraction", rdoi)):
            if doi:
                tasks.append(PageTask(int(rid), kind, doi, doi_prefix(doi)))
    return tasks
