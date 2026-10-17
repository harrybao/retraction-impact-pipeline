# Data quality report - Retraction Watch CSV

Generated 2026-08-25 by `scripts/clean_data.py`.
Source: `retraction_watch.csv`

## Volumes

| | |
|---|---|
| Rows in the raw file | 71,944 |
| Blank trailing rows dropped | 246 |
| Records cleaned | 71,698 |

## Flags

Nothing here is silently deleted. Every issue is flagged on the record so
downstream stages can decide, and so the final report can quantify the gap.

| flag | records | % | meaning |
|---|---:|---:|---|
| `flag_same_doi_both_sides` | 13,655 | 19.0% | same DOI in both columns - one page, not two |
| `flag_no_original_doi` | 5,946 | 8.3% | no usable original DOI - that side cannot be fetched |
| `flag_shared_original_doi` | 5,245 | 7.3% | original DOI shared with another record - usually an Expression of concern later upgraded to a Retraction; kept separate on purpose |
| `flag_no_retraction_doi` | 4,669 | 6.5% | no usable retraction DOI - that side cannot be fetched |
| `flag_nothing_to_fetch` | 3,283 | 4.6% | neither DOI usable - excluded from the crawl entirely |
| `flag_unparsed_retraction_date` | 0 | 0.0% | retraction date could not be parsed |
| `flag_unparsed_original_date` | 0 | 0.0% | publication date could not be parsed |
| `flag_retraction_before_publication` | 0 | 0.0% | notice predates the paper - impossible |
| `flag_no_reason` | 0 | 0.0% | no retraction reason recorded |
| `flag_no_subject` | 0 | 0.0% | no subject area recorded |

## Reading this file

**Use `rwpipe.clean.read_clean()`, or pass `dtypes.json` to pandas.**

CSV carries no types. `original_doi_prefix` and `retraction_doi_prefix` look
numeric, so a plain `read_csv` turns them into floats and silently drops a
trailing zero: `10.3390` (MDPI) becomes `10.339`, `10.1080` (Taylor & Francis)
becomes `10.108`. 82 prefixes covering 4,781 pages are affected, and three
pairs collide outright - `10.1113`/`10.11130`, `10.1556`/`10.15560`,
`10.1557`/`10.15570`. That corruption is invisible until an analysis
silently counts the wrong publisher.

## Normalisation applied

- **Dates** parsed to ISO. The file mixes `4/10/2026 0:00` with
  `6/24/1756 12:00:00 AM`; both are handled. The 18th-century dates are
  genuine (*Treatise upon Electricity*, 1753), not corruption.
- **DOIs** lowercased, resolver prefixes stripped, validated against
  `10.<registrant>/<suffix>`. `unavailable`, `0` and blanks become null.
- **PubMed IDs** - `0` and blank both mean absent, normalised to null.
- **List fields** (Subject, Reason, Author, Country, Institution,
  ArticleType) split on `;`. Every Subject and Reason cell carries a
  trailing `;`, so a naive split leaves an empty tail on all rows.
- **Subject codes** separated from labels: `(BLS) Biochemistry` becomes
  code `BLS`, label `Biochemistry`.

## Deliberately not done

Records sharing an `OriginalPaperDOI` are **not** merged. The dominant
pattern is an `Expression of concern` later followed by a `Retraction` on
the same paper - the update lifecycle this project studies. Merging would
destroy the signal, so they are flagged instead.

## Long-format tables

Semicolon-packed cells cannot be grouped or counted. `subjects.csv`,
`reasons.csv`, `countries.csv` and `authors.csv` give one row per
(record, item) so Stage 3 can aggregate directly.