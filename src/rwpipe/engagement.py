"""Stage 1b - downstream engagement, from sources that need no licence.

The original plan was Altmetric and PlumX. Both are now closed to us: Altmetric
returns 403 with "As of 10 November 2025, all users need an API key", and the
Crossref Event Data endpoint no longer resolves. The client's answer to the
access question was to sort it out ourselves, so this measures the engagement
that is still openly available:

    OpenAlex    per-year citation counts, which is exactly the shape needed for
                the project's central question - does a retracted paper keep
                being cited *after* the notice?
    Wikipedia   which encyclopedia articles still cite the retracted DOI. Small
                in volume but high in signal: Wikipedia is read far more than
                the papers it cites.

This is narrower than "social and news media" and the report must say so. What
it is not is a weaker version of the same question - "is it still being cited
after retraction" is answerable completely and reproducibly here, where the
social-media version would have been a licensed, partial snapshot.

OpenAlex accepts up to 50 DOIs per request, so the whole corpus costs about
1,400 calls rather than 71,698.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import httpx

from . import config

OPENALEX = "https://api.openalex.org/works"
WIKI = "https://en.wikipedia.org/w/api.php"
BATCH = 50                                   # OpenAlex per-request DOI limit

HEADERS = {
    "User-Agent": f"COMP5703-CS43 student project (mailto:{config.POLITE_CONTACT})",
    "Accept": "application/json",
}


@dataclass
class Engagement:
    doi: str
    openalex_id: str | None = None
    cited_by_count: int | None = None
    counts_by_year: dict[int, int] = field(default_factory=dict)
    referenced_works_count: int | None = None
    is_oa: bool | None = None
    work_type: str | None = None
    wikipedia_count: int | None = None
    wikipedia_pages: str | None = None
    error: str | None = None
    _retraction_year: int | None = None

    @property
    def window_covers(self) -> bool:
        """Do the yearly figures actually cover the period being compared?

        `counts_by_year` is a **rolling recent window**, not the paper's full
        citation history. In this corpus the earliest year available clusters
        hard at 2012 and the widest span is 15 years. For a paper retracted
        before that window opens, every pre-retraction citation is missing,
        `citations_before` comes out 0 by construction, and the paper looks
        like it gained citations after retraction when nothing of the sort has
        been measured. 8,220 papers were counted that way before this check
        existed, and the resulting figure was published.

        So a before/after comparison is only meaningful when the window opens
        at or before the retraction **and** the yearly figures account for the
        whole `cited_by_count`. Anything else is measuring the window.
        """
        if not self.counts_by_year or not self._retraction_year:
            return False
        return (min(self.counts_by_year) <= self._retraction_year
                and (self.cited_by_count or 0) <= sum(self.counts_by_year.values()))

    def split_by_year(self, retraction_year: int | None) -> tuple[int, int, int]:
        """Citations before / during / after the retraction year.

        OpenAlex reports per year, not per month, so the retraction year itself
        cannot be assigned to either side - it is returned separately rather
        than silently folded into one of them.

        **These are counts within the visible window, not the full history.**
        Check `window_covers` before comparing before against after; see that
        property for what goes wrong otherwise.
        """
        self._retraction_year = retraction_year
        if not retraction_year or not self.counts_by_year:
            return 0, 0, 0
        before = sum(n for y, n in self.counts_by_year.items() if y < retraction_year)
        during = self.counts_by_year.get(retraction_year, 0)
        after = sum(n for y, n in self.counts_by_year.items() if y > retraction_year)
        return before, during, after


def _norm_doi(url: str | None) -> str | None:
    if not url:
        return None
    return url.replace("https://doi.org/", "").lower()


async def openalex_batch(client: httpx.AsyncClient, dois: list[str],
                         max_retries: int = 5) -> dict[str, Engagement]:
    """One request for up to 50 DOIs.

    Retries on 429. The first full run treated a rate-limit response as "this
    DOI does not exist", which is a very different claim: 23,917 papers were
    written off as absent from OpenAlex when they had simply been throttled.
    Genuinely absent DOIs turned out to be 365 - half a percent, not 39%.
    """
    out: dict[str, Engagement] = {}
    if not dois:
        return out
    filt = "doi:" + "|".join(dois)
    r = None
    for attempt in range(max_retries):
        try:
            r = await client.get(
                OPENALEX,
                params={"filter": filt, "per-page": BATCH,
                        "select": "id,doi,cited_by_count,counts_by_year,"
                                  "referenced_works_count,open_access,type",
                        "mailto": config.POLITE_CONTACT},
                headers=HEADERS, timeout=60)
        except Exception as exc:                      # noqa: BLE001
            if attempt == max_retries - 1:
                return {d: Engagement(doi=d, error=f"{type(exc).__name__}") for d in dois}
            await asyncio.sleep(2 ** attempt)
            continue
        if r.status_code == 429:
            # Backoff must outlast the limit that caused it. OpenAlex enforces a
            # daily quota as well as a per-second rate: when the daily one is
            # exhausted, a 60 s ceiling just burns every retry against a wall
            # that will not lift for hours. Measured: a 6-hour retry pass
            # recovered 917 of 23,917 rows, all of it in the first minutes.
            await asyncio.sleep(min(600, 5 * (2 ** attempt)))
            continue
        break
    if r is None or r.status_code != 200:
        code = r.status_code if r is not None else "none"
        return {d: Engagement(doi=d, error=f"openalex http {code}") for d in dois}

    try:
        results = r.json().get("results", [])
    except Exception:                                 # noqa: BLE001
        return {d: Engagement(doi=d, error="openalex bad json") for d in dois}

    for w in results:
        doi = _norm_doi(w.get("doi"))
        if not doi:
            continue
        e = Engagement(doi=doi)
        e.openalex_id = (w.get("id") or "").rsplit("/", 1)[-1] or None
        e.cited_by_count = w.get("cited_by_count")
        e.counts_by_year = {c["year"]: c["cited_by_count"]
                            for c in (w.get("counts_by_year") or [])
                            if c.get("year") is not None}
        e.referenced_works_count = w.get("referenced_works_count")
        e.is_oa = (w.get("open_access") or {}).get("is_oa")
        e.work_type = w.get("type")
        out[doi] = e

    # DOIs OpenAlex has no record of still deserve a row, so the gap is visible
    for d in dois:
        out.setdefault(d, Engagement(doi=d, error="not in openalex"))
    return out


async def wikipedia_citations(client: httpx.AsyncClient,
                              doi: str) -> tuple[int, str | None] | None:
    """English Wikipedia articles citing this DOI, or **None if the search
    failed**.

    This returned `(0, None)` for both "searched, found nothing" and "the
    request was refused", and the caller wrote that 0 straight into
    `engagement.wikipedia_count`. Measured against the later multilingual
    sweep, 121 papers had been recorded as absent from Wikipedia when a working
    search finds them - roughly a fifth of the English total.

    English only, by design; the multilingual sweep lives in
    `scripts/collect_wikipedia.py` and writes to `wikipedia_cites`.
    """
    for attempt in range(3):
        try:
            r = await client.get(
                WIKI,
                params={"action": "query", "list": "search",
                        "srsearch": f'insource:"{doi}"', "srlimit": 20,
                        "srnamespace": 0, "format": "json"},
                headers=HEADERS, timeout=30)
        except Exception:                             # noqa: BLE001
            await asyncio.sleep(2 * (attempt + 1))
            continue
        if r.status_code in (429, 503):
            await asyncio.sleep(5 * (attempt + 1))
            continue
        if r.status_code != 200:
            return None
        try:
            d = r.json()
        except Exception:                             # noqa: BLE001
            return None
        if "error" in d:
            await asyncio.sleep(2 * (attempt + 1))
            continue
        hits = d.get("query", {}).get("search", [])
        titles = [h.get("title", "") for h in hits if h.get("title")]
        if not titles:
            return 0, None
        return len(titles), json.dumps(titles[:20], ensure_ascii=False)
    return None
