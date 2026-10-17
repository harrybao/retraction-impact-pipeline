"""Ask four scholarly aggregators about the documents nothing else can reach.

    python scripts/probe_aggregators.py --n 40

11,506 refused documents currently have `route = none-known`: PubMed Central
does not hold them, OpenAlex knows of no free copy, the campus network does not
help, and the publisher either refused or requires an institutional agreement
the University declined to write. That number is the project's real limit, so
it is worth spending forty requests to find out whether it is actually true.

Four services, all keyless, all sanctioned interfaces rather than scraped
pages. None of them is a way around a block - each is a different organisation
that already holds a copy:

    Europe PMC   a superset of PubMed Central: it indexes preprints, patents
                 and non-US repositories PMC does not, and serves full text as
                 `fullTextXML` for anything in its open-access subset.
    Semantic     Allen Institute. Reports `openAccessPdf` per paper, resolved
    Scholar      across repositories and publisher sites, and is the front door
                 to the S2ORC parsed full-text corpus.
    fatcat       the Internet Archive's scholarly catalogue behind
                 scholar.archive.org: 25M+ preserved papers, with file-level
                 metadata pointing at copies the Archive itself holds. Distinct
                 from the Wayback Machine route already tried, which asked for
                 a snapshot of the publisher's page rather than for the paper.
    OpenAIRE     the EU aggregator, strongest exactly where the others are
                 weakest - European institutional repositories.

What this does NOT include, deliberately: Sci-Hub, LibGen and the tools built
on them (PyPaperBot and similar offer them as a source). They redistribute
copyrighted work without licence. Using them would make the dataset unusable
in a published capstone and is not something this project will do.

Results go to probe_results so the answer never has to be re-derived, and so a
service that changes its mind can be compared against what it did today.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx
from rich.console import Console

from rwpipe import config, db

console = Console()

UA = f"COMP5703-CS43 student project (mailto:{config.POLITE_CONTACT})"
RUN = "aggregators-2026-09-04"

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
S2 = "https://api.semanticscholar.org/graph/v1/paper"
FATCAT = "https://api.fatcat.wiki/v0/release/lookup"
OPENAIRE = "https://api.openaire.eu/search/publications"


def europe_pmc(cl: httpx.Client, doi: str) -> tuple[str, str]:
    r = cl.get(f"{EPMC}/search",
               params={"query": f'DOI:"{doi}"', "format": "json",
                       "resultType": "core", "pageSize": 1})
    if r.status_code != 200:
        return f"http-{r.status_code}", ""
    hits = r.json().get("resultList", {}).get("result", [])
    if not hits:
        return "not-indexed", ""
    h = hits[0]
    if h.get("isOpenAccess") == "Y" and h.get("inEPMC") == "Y":
        return "full-text", f"{EPMC}/{h.get('source')}/{h.get('id')}/fullTextXML"
    if h.get("hasPDF") == "Y":
        return "pdf-only", h.get("fullTextUrlList", {}).get("fullTextUrl", [{}])[0].get("url", "")
    return "metadata-only", ""


def semantic_scholar(cl: httpx.Client, doi: str) -> tuple[str, str]:
    r = cl.get(f"{S2}/DOI:{doi}",
               params={"fields": "isOpenAccess,openAccessPdf,externalIds"})
    if r.status_code == 404:
        return "not-indexed", ""
    if r.status_code != 200:
        return f"http-{r.status_code}", ""
    d = r.json()
    pdf = (d.get("openAccessPdf") or {}).get("url") or ""
    return ("oa-pdf" if pdf else
            "indexed-no-pdf"), pdf


def fatcat(cl: httpx.Client, doi: str) -> tuple[str, str]:
    r = cl.get(FATCAT, params={"doi": doi, "expand": "files"})
    if r.status_code == 404:
        return "not-indexed", ""
    if r.status_code != 200:
        return f"http-{r.status_code}", ""
    files = r.json().get("files") or []
    for f in files:
        for u in (f.get("urls") or []):
            if u.get("rel") in ("webarchive", "archive", "repository", "publisher"):
                return "archived-file", u.get("url", "")
    return "catalogued-no-file", ""


def openaire(cl: httpx.Client, doi: str) -> tuple[str, str]:
    r = cl.get(OPENAIRE, params={"doi": doi, "format": "json", "size": 1})
    if r.status_code != 200:
        return f"http-{r.status_code}", ""
    try:
        res = r.json()["response"]["results"]
    except Exception:                                       # noqa: BLE001
        return "not-indexed", ""
    if not res:
        return "not-indexed", ""
    return "indexed", ""


SERVICES = (("europe-pmc", europe_pmc), ("semantic-scholar", semantic_scholar),
            ("fatcat", fatcat), ("openaire", openaire))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    args = ap.parse_args()

    conn = db.connect()
    db.init(conn)
    # The hardest cases on purpose: refused, no PMC record, no OA location
    # OpenAlex knows about. A sample drawn from the easy end would say nothing.
    dois = [r[0] for r in conn.execute(
        """SELECT d.doi FROM documents d
           LEFT JOIN pmc_ids p ON p.doi = d.doi AND p.pmcid IS NOT NULL
           WHERE d.usable IS NOT 1 AND p.doi IS NULL
             AND d.doi NOT IN (SELECT doi FROM oa_all_locations WHERE is_oa = 1)
           ORDER BY RANDOM() LIMIT ?""", (args.n,))]
    console.print(f"[bold]{len(dois)} documents nothing else can reach[/bold]\n")

    tally: dict[tuple[str, str], int] = {}
    with httpx.Client(timeout=45.0, follow_redirects=True,
                      headers={"User-Agent": UA}) as cl:
        for n, doi in enumerate(dois, 1):
            line = [f"  {doi[:34]:<34}"]
            for name, fn in SERVICES:
                try:
                    verdict, url = fn(cl, doi)
                except Exception as exc:                    # noqa: BLE001
                    verdict, url = f"error-{type(exc).__name__}", ""
                tally[(name, verdict)] = tally.get((name, verdict), 0) + 1
                conn.execute(
                    """INSERT INTO probe_results
                       (run, target, item, verdict, detail, probed_at)
                       VALUES (?,?,?,?,?, datetime('now'))""",
                    (RUN, name, doi, verdict, url[:300]))
                line.append(f"{name[:4]}:{verdict[:14]:<14}")
                time.sleep(0.35)
            console.print("".join(line))
            if n % 10 == 0:
                conn.commit()
    conn.commit()

    console.print("\n[bold]what each service has[/bold]")
    for name, _ in SERVICES:
        console.print(f"\n  {name}")
        for (svc, verdict), c in sorted(tally.items(), key=lambda x: -x[1]):
            if svc == name:
                console.print(f"    {verdict:<20} {c:>4}  "
                              f"({c/len(dois)*100:.0f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
