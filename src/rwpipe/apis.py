"""Official publisher-sanctioned APIs, used where crawling cannot reach.

Roughly a fifth of the frontier resolves to publishers that refuse automated
access outright - HTTP 403 before the page renders. Browser stealth was tested
and does not help, because the refusal happens at the network layer. What does
work is the channel publishers themselves provide:

    Crossref     the metadata publishers deposit against every DOI, including
                 the retraction relationship (update-to / updated-by) and any
                 text-mining full-text endpoints they register
    Europe PMC   abstracts, and full text for the open-access subset - served
                 from EBI's own servers, so a publisher's bot wall is irrelevant
    OpenAlex     open-access status and alternative locations

None of these return the *rendered publisher page*, so they do not replace the
Stage 1 deliverable. They turn an unreachable page from a blank row into a row
that knows what the paper was, that it was retracted, and often what it claimed.

Measured limitation, worth stating plainly: none of them carry the text of the
retraction notice itself. "Why was it retracted" stays out of reach for pages
the crawler cannot fetch.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

import httpx

from . import config

CROSSREF = "https://api.crossref.org/works"
EUROPEPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
OPENALEX = "https://api.openalex.org/works"

# Sending a contact address puts us in Crossref's and OpenAlex's "polite pool",
# which is both the courteous thing to do and gets more reliable service.
HEADERS = {
    "User-Agent": f"COMP5703-CS43 student project (mailto:{config.POLITE_CONTACT})",
    "Accept": "application/json",
}

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def clean_abstract(text: str | None) -> str | None:
    """Crossref abstracts arrive as JATS XML; Europe PMC's are already plain."""
    if not text:
        return None
    t = _TAG.sub(" ", text)
    t = _WS.sub(" ", t).strip()
    # publishers often prefix the literal word "Abstract"
    t = re.sub(r"^abstract[:\s]+", "", t, flags=re.I).strip()
    return t or None


@dataclass
class Enrichment:
    """What the APIs could tell us about one DOI."""
    doi: str
    sources: list[str] = field(default_factory=list)
    title: str | None = None
    publisher: str | None = None
    journal: str | None = None
    issued: str | None = None
    work_type: str | None = None
    abstract: str | None = None
    abstract_source: str | None = None
    reference_count: int | None = None
    update_types: str | None = None      # e.g. "retraction"
    related_dois: str | None = None      # the DOI(s) on the other side
    is_oa: bool | None = None
    oa_url: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    tdm_links: str | None = None
    error: str | None = None


async def _get(client: httpx.AsyncClient, url: str, **kw) -> httpx.Response | None:
    """One GET with a short retry. Returns None rather than raising."""
    for attempt in range(3):
        try:
            r = await client.get(url, headers=HEADERS, timeout=30, **kw)
            if r.status_code == 429:            # rate limited: wait and retry
                await asyncio.sleep(2 * (attempt + 1))
                continue
            return r
        except (httpx.TimeoutException, httpx.TransportError):
            if attempt == 2:
                return None
            await asyncio.sleep(1 + attempt)
    return None


async def from_crossref(client: httpx.AsyncClient, doi: str, e: Enrichment) -> None:
    r = await _get(client, f"{CROSSREF}/{doi}")
    if r is None or r.status_code != 200:
        return
    try:
        m = r.json()["message"]
    except Exception:                            # noqa: BLE001
        return
    e.sources.append("crossref")
    e.title = (m.get("title") or [None])[0]
    e.publisher = m.get("publisher")
    e.journal = (m.get("container-title") or [None])[0]
    e.work_type = m.get("type")
    e.reference_count = m.get("reference-count")
    if not e.abstract:
        ab = clean_abstract(m.get("abstract"))
        if ab:
            e.abstract, e.abstract_source = ab, "crossref"

    parts = ((m.get("issued") or {}).get("date-parts") or [[None]])[0]
    if parts and parts[0]:
        e.issued = "-".join(f"{p:02d}" if i else str(p)
                            for i, p in enumerate(parts) if p is not None)

    # the retraction relationship, as deposited by the publisher
    rel = (m.get("update-to") or []) + (m.get("updated-by") or [])
    if rel:
        e.update_types = ";".join(sorted({u.get("type", "") for u in rel if u.get("type")}))
        e.related_dois = ";".join(sorted({u.get("DOI", "") for u in rel if u.get("DOI")}))

    tdm = [l.get("URL") for l in (m.get("link") or [])
           if l.get("intended-application") == "text-mining" and l.get("URL")]
    if tdm:
        e.tdm_links = ";".join(tdm[:4])


async def from_europepmc(client: httpx.AsyncClient, doi: str, e: Enrichment) -> None:
    r = await _get(client, f"{EUROPEPMC}/search",
                   params={"query": f"DOI:{doi}", "format": "json", "resultType": "core"})
    if r is None or r.status_code != 200:
        return
    try:
        hits = r.json().get("resultList", {}).get("result", [])
    except Exception:                            # noqa: BLE001
        return
    if not hits:
        return
    h = hits[0]
    e.sources.append("europepmc")
    e.pmid = h.get("pmid")
    e.pmcid = h.get("pmcid")
    # Europe PMC abstracts are plain text and generally fuller than Crossref's
    ab = clean_abstract(h.get("abstractText"))
    if ab and (not e.abstract or len(ab) > len(e.abstract)):
        e.abstract, e.abstract_source = ab, "europepmc"
    e.title = e.title or h.get("title")
    e.journal = e.journal or (h.get("journalInfo") or {}).get("journal", {}).get("title")


async def from_openalex(client: httpx.AsyncClient, doi: str, e: Enrichment) -> None:
    r = await _get(client, f"{OPENALEX}/doi:{doi}")
    if r is None or r.status_code != 200:
        return
    try:
        w = r.json()
    except Exception:                            # noqa: BLE001
        return
    e.sources.append("openalex")
    oa = w.get("open_access") or {}
    e.is_oa = oa.get("is_oa")
    e.oa_url = oa.get("oa_url")


async def enrich(client: httpx.AsyncClient, doi: str) -> Enrichment:
    """Ask all three, tolerating any of them being unavailable."""
    e = Enrichment(doi=doi)
    try:
        await from_crossref(client, doi, e)
        await from_europepmc(client, doi, e)
        await from_openalex(client, doi, e)
    except Exception as exc:                     # noqa: BLE001
        e.error = f"{type(exc).__name__}: {str(exc)[:120]}"
    if not e.sources and not e.error:
        e.error = "no source returned a record"
    return e
