"""Stage 1b - Wikipedia citations across language editions.

    python scripts/collect_wikipedia.py --probe        # count the work first
    python scripts/collect_wikipedia.py                # sweep

Why this exists separately from `collect_engagement.py --wikipedia`:

That pass searches **English Wikipedia only**. Altmetric flags 3,174 papers in
this corpus as Wikipedia-cited; the English-only search finds 430. A 60-DOI
sample of the difference found 32% of them cited in other editions - zh 11,
pt 10, ja 9, es 8, it 6, ru 5, fr 4, nl 4, de 3, pl 3 out of 60. So roughly 880
of the 2,744 apparent misses are real citations in a language we never looked
at, and the English figure understates public reach by about a factor of three.

The remaining ~68% are missed for reasons this script cannot fix: an article
citing the paper by PMID or title rather than DOI, or a citation Altmetric
recorded once that has since been edited away. Both are stated as limitations
rather than papered over.

Results go to `wikipedia_cites`, one row per (paper, edition, article).
`engagement.wikipedia_count` is left exactly as it is - English-only and
comparable across the whole corpus - because quietly redefining a column
halfway through a project is how a number ends up meaning two things at once.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx
from rich.console import Console

from rwpipe import config, db

console = Console()

# The editions worth the requests, ranked by what the sample actually found.
# English is included so this table stands on its own.
LANGS = ["en", "zh", "pt", "ja", "es", "it", "ru", "fr", "nl", "de", "pl"]

HEADERS = {
    "User-Agent": f"COMP5703-CS43 student project (mailto:{config.POLITE_CONTACT})",
    "Accept": "application/json",
}


def targets(conn, limit: int | None) -> list[str]:
    """Papers Altmetric says are cited on Wikipedia, not yet swept.

    Gating on Altmetric rather than sweeping all 62,867 keeps this to ~3,000
    papers and eleven editions instead of ~700,000 requests for a result that
    is overwhelmingly zero.
    """
    sql = """SELECT a.doi FROM altmetric a
             LEFT JOIN wikipedia_swept w ON w.doi = a.doi
             WHERE w.doi IS NULL
               AND json_extract(a.counts_json, '$.cited_by_wikipedia_count') > 0
             ORDER BY json_extract(a.counts_json, '$.cited_by_wikipedia_count') DESC"""
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [r["doi"] for r in conn.execute(sql)]


async def search(client: httpx.AsyncClient, lang: str, doi: str,
                 max_retries: int = 4) -> list[str] | None:
    """Articles in this edition citing the DOI. **None means the request
    failed**, which is not the same as an empty list.

    The first version of this returned `[]` on any non-200 or exception. The
    sweep then ran eleven parallel requests per DOI at 0.4 s intervals - about
    27 requests a second - Wikipedia throttled it after roughly 56 papers, and
    every subsequent refusal was written to the database as "cited by nothing".
    3,118 papers were recorded as having no Wikipedia presence without a single
    successful search behind the claim.

    Same failure as the OpenAlex 429 that wrote off 23,917 papers as absent
    from the index. A refusal is not an answer, and the two must never share a
    return value.
    """
    for attempt in range(max_retries):
        try:
            r = await client.get(
                f"https://{lang}.wikipedia.org/w/api.php",
                params={"action": "query", "list": "search",
                        "srsearch": f'insource:"{doi}"', "srlimit": 20,
                        "srnamespace": 0, "format": "json"},
                headers=HEADERS, timeout=30)
        except (httpx.TimeoutException, httpx.TransportError):
            await asyncio.sleep(2 * (attempt + 1))
            continue

        if r.status_code == 200:
            try:
                d = r.json()
            except Exception:                              # noqa: BLE001
                return None
            if "error" in d:                               # API-level refusal
                await asyncio.sleep(2 * (attempt + 1))
                continue
            hits = d.get("query", {}).get("search", [])
            return [h["title"] for h in hits if h.get("title")]

        if r.status_code in (429, 503):
            await asyncio.sleep(5 * (attempt + 1))
            continue
        return None
    return None


async def sweep(conn, dois: list[str], rate: float) -> dict:
    stats = {"checked": 0, "with_cites": 0, "articles": 0, "incomplete": 0}
    per_lang: dict[str, int] = {}
    started = time.time()

    async with httpx.AsyncClient() as client:
        for doi in dois:
            # Editions are queried in small groups rather than all at once.
            # Eleven at a time was what got this throttled.
            results: list[list[str] | None] = []
            for i in range(0, len(LANGS), 3):
                results += await asyncio.gather(
                    *[search(client, l, doi) for l in LANGS[i:i + 3]])
                await asyncio.sleep(0.2)

            if any(x is None for x in results):
                # Some edition never answered. Recording a total now would
                # bake a refusal into the dataset as a finding, so the paper
                # is left unswept and picked up on the next run.
                stats["incomplete"] += 1
                continue

            total = 0
            for lang, titles in zip(LANGS, results):
                for t in titles:
                    conn.execute(
                        """INSERT OR REPLACE INTO wikipedia_cites
                           (doi, lang, title, fetched_at)
                           VALUES (?,?,?, datetime('now'))""", (doi, lang, t))
                if titles:
                    per_lang[lang] = per_lang.get(lang, 0) + 1
                total += len(titles)

            conn.execute(
                """INSERT OR REPLACE INTO wikipedia_swept
                   (doi, langs, total, fetched_at)
                   VALUES (?,?,?, datetime('now'))""",
                (doi, ",".join(LANGS), total))
            conn.commit()

            stats["checked"] += 1
            stats["articles"] += total
            if total:
                stats["with_cites"] += 1

            n = stats["checked"]
            if n % 100 == 0:
                el = max(time.time() - started, 1)
                console.print(
                    f"[dim]{n:>6,}/{len(dois):,}[/dim] "
                    f"有引用 {stats['with_cites']:,}  "
                    f"词条 {stats['articles']:,}  "
                    f"| {n/el*3600:,.0f}/h")
            await asyncio.sleep(rate)
    stats["per_lang"] = per_lang
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--rate", type=float, default=1.0,
                    help="seconds between DOIs; each DOI is one request per "
                         "edition, issued together")
    ap.add_argument("--probe", action="store_true", help="count and exit")
    args = ap.parse_args()

    conn = db.connect()
    db.init(conn)
    dois = targets(conn, args.limit)

    if args.probe:
        console.print(f"{len(dois):,} DOIs to sweep x {len(LANGS)} editions "
                      f"= {len(dois)*len(LANGS):,} requests")
        return 0
    if not dois:
        console.print("[green]nothing left to sweep[/green]")
        return 0

    mins = len(dois) * args.rate / 60
    console.print(f"[bold]Wikipedia: {len(dois):,} DOIs across "
                  f"{len(LANGS)} editions[/bold] — about {mins:.0f} min\n")

    stats = asyncio.run(sweep(conn, dois, args.rate))

    console.print(f"\n[bold]swept {stats['checked']:,}[/bold]")
    console.print(f"  cited somewhere on Wikipedia : {stats['with_cites']:,}")
    console.print(f"  articles citing them         : {stats['articles']:,}")
    if stats["incomplete"]:
        console.print(f"  [yellow]left unswept (an edition refused): "
                      f"{stats['incomplete']:,} — re-run to finish[/yellow]")
    console.print("\n  by edition:")
    for lang, n in sorted(stats["per_lang"].items(), key=lambda x: -x[1]):
        console.print(f"    {lang:>3}  {n:,}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
