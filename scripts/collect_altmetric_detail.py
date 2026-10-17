"""Stage 1b - what the media actually said, not how often.

    python scripts/collect_altmetric_detail.py --probe
    python scripts/collect_altmetric_detail.py

The summary endpoint (v1/doi) returns counts: "719 news outlets". Stage 3 has
to judge whether those outlets described the paper accurately, and a count
cannot be judged. The detail endpoint (v1/fetch/doi) returns the posts
themselves - headline, summary, link, outlet, and a timestamp for each one.

Two things follow from per-post timestamps that the summary could not support:

* Attention CAN be split before and after the retraction. The earlier
  collection recorded that as impossible; it was a property of the endpoint we
  chose, not of Altmetric.
* A mention can be traced to its source and read, which is what turns "719
  outlets covered it" into an answer about whether they got it right.

The key was supplied for the detail endpoint, and
we used the summary endpoint for all 62,867 papers regardless.

Scope: the 22,368 papers whose `cited_by_posts_count` is above zero - that is
Altmetric's own count of online documents linking to the work, and it covers
sources beyond any channel list we would think to write.

The remaining 40,499 have **no record within the sources Altmetric tracks and
successfully matched to an identifier**. That is not the same as "never
noticed": Altmetric observes the outlets it monitors, not the internet, and a
mention it cannot match to a DOI does not exist as far as this API is
concerned. The distinction belongs in anything reported from this data.

A further 7 rows failed with a connection error during the summary pass.
Nothing was learned about them, so they are re-asked rather than skipped.

What Full Access returns varies by source: for X it is post IDs rather than
text, and some news and policy items are licence-restricted. This collects
whatever a source is permitted to return - URL, date, summary, author, or an
ID - and does not assume every mention arrives with its text.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx
from rich.console import Console

from rwpipe import altmetric, config, db

console = Console()

ENDPOINT = "https://api.altmetric.com/v1/fetch/doi/"
HEADERS = {
    "User-Agent": f"COMP5703-CS43 student project (mailto:{config.POLITE_CONTACT})",
    "Accept": "application/json",
}

# No channel whitelist. One was written here and another in the target query,
# and the query's version silently dropped 1,793 papers. A list of source
# types is a guess about what Altmetric tracks, made by someone who does not
# maintain that list; whatever comes back under `posts` is stored under its own
# name, and an unfamiliar kind is data rather than an error.

TARGETS = """
SELECT a.doi FROM altmetric a
LEFT JOIN altmetric_detail d ON d.doi = a.doi
WHERE a.found = 1
  AND d.doi IS NULL
  AND json_extract(a.counts_json,'$.cited_by_posts_count') > 0
ORDER BY json_extract(a.counts_json,'$.cited_by_msm_count') DESC, a.score DESC
"""

# `cited_by_posts_count` is Altmetric's own count of online documents linking
# to the work, and it spans sources beyond any list we would think to write.
# An earlier version of this query enumerated seven channels - news, twitter,
# blogs, reddit, facebook, policy, wikipedia - and missed 1,793 papers whose
# mentions were on Bluesky, in videos, podcasts, Weibo, Q&A sites or peer
# review platforms. Every one of them had cited_by_posts_count > 0. Naming the
# channels was a second manual narrowing, immediately after deciding not to
# narrow by hand.

RETRY_ERRORS = """
SELECT doi FROM altmetric
WHERE found = 0 AND error IS NOT NULL AND error <> ''
"""


# post_id() and author_of() live in rwpipe.altmetric so this collector and
# the notice collector cannot drift into disagreeing about what identifies
# a post - two answers to that means the same mention stored twice.
post_id = altmetric.post_id
author_of = altmetric.author_of


def store(conn, doi, data, nbytes):
    seen = Counter()
    for kind, items in (data.get("posts") or {}).items():
        if not isinstance(items, list):
            continue
        for p in items:
            if not isinstance(p, dict):
                continue
            name, url = author_of(p)
            conn.execute(
                """INSERT OR REPLACE INTO altmetric_posts
                   (doi, kind, post_id, title, summary, url, author,
                    author_url, posted_on, country, license, raw)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (doi, kind, post_id(p), p.get("title"), p.get("summary"),
                 p.get("url"), name, url, p.get("posted_on"),
                 p.get("country"), p.get("license"),
                 json.dumps(p, ensure_ascii=False)[:20000]))
            seen[kind] += 1
    conn.execute(
        """INSERT OR REPLACE INTO altmetric_detail
           (doi, posts_total, kinds, bytes, error, fetched_at)
           VALUES (?,?,?,?,?, datetime('now'))""",
        (doi, sum(seen.values()), ",".join(sorted(seen)), nbytes, None))
    return seen


async def run(conn, dois, key, rate, exclude=()):
    stats = Counter()
    started = time.time()
    async with httpx.AsyncClient() as client:
        for i, doi in enumerate(dois, 1):
            try:
                params = {"key": key}
                if exclude:
                    params["exclude_sources"] = ",".join(exclude)
                r = await client.get(ENDPOINT + doi, params=params,
                                     headers=HEADERS, timeout=120)
            except Exception as exc:                       # noqa: BLE001
                stats["error"] += 1
                conn.execute(
                    """INSERT OR REPLACE INTO altmetric_detail
                       (doi, posts_total, kinds, bytes, error, fetched_at)
                       VALUES (?,?,?,?,?, datetime('now'))""",
                    (doi, None, None, None, type(exc).__name__))
                conn.commit()
                await asyncio.sleep(rate)
                continue

            # Altmetric meters three things separately: 3,600 requests an
            # hour, 86,400 a day, and a balance of X post IDs. Reporting the
            # headers means a stop can say which limit was reached instead of
            # "it refused us".
            for h, label in (("X-HourlyRateLimit-Remaining", "hourly"),
                             ("X-DailyRateLimit-Remaining", "daily"),
                             ("X-TweetIdBalance-Remaining", "X post ids")):
                v = r.headers.get(h)
                if v is not None:
                    stats["quota_" + label] = int(v) if str(v).isdigit() else 0

            if r.status_code in (429, 403):
                console.print(f"[red]service refusing: HTTP {r.status_code}[/red]")
                for k, v in stats.items():
                    if k.startswith("quota_"):
                        console.print(f"  {k[6:]:<12} {v:,} remaining")
                console.print("[yellow]stopping to protect the client's key; "
                              "progress is saved[/yellow]")
                break

            if r.status_code == 404:
                # Altmetric has a summary for this DOI but no post detail.
                # Stored as a result, not skipped, so it is never re-requested.
                stats["no-detail"] += 1
                conn.execute(
                    """INSERT OR REPLACE INTO altmetric_detail
                       (doi, posts_total, kinds, bytes, error, fetched_at)
                       VALUES (?,0,NULL,0,NULL, datetime('now'))""", (doi,))
                conn.commit()
                await asyncio.sleep(rate)
                continue

            if r.status_code != 200:
                stats["http" + str(r.status_code)] += 1
                await asyncio.sleep(rate)
                continue

            try:
                data = r.json()
            except Exception:                              # noqa: BLE001
                stats["bad-json"] += 1
                await asyncio.sleep(rate)
                continue

            seen = store(conn, doi, data, len(r.content))
            conn.commit()
            stats["ok"] += 1
            stats["posts"] += sum(seen.values())
            stats["news"] += seen.get("news", 0)

            if i % 100 == 0:
                el = max(time.time() - started, 1)
                console.print(f"[dim]{i:>6,}/{len(dois):,}[/dim] "
                              f"posts {stats['posts']:,}  news {stats['news']:,}  "
                              f"no-detail {stats['no-detail']:,}  "
                              f"| {i / el * 3600:,.0f}/h")
            await asyncio.sleep(rate)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--rate", type=float, default=1.0,
                    help="seconds between requests; this is the client's key")
    ap.add_argument("--probe", action="store_true")
    # The key's separate allowance of X post ids (1,500,000) was spent by the
    # first pass; with it at 0, any DOI with tweets is refused with 429 while
    # the hourly and daily quotas are untouched. Leaving X out returns every
    # other source and spends none of it (see collect_altmetric_notice_detail.py).
    ap.add_argument("--exclude-sources", default="",
                    help="comma-separated Altmetric sources to leave out, e.g. twitter")
    args = ap.parse_args()

    conn = db.connect()
    db.init(conn)
    # The 7 rows whose summary fetch died with a ConnectError are not
    # "no record" - nothing was ever learned about them. Re-ask before
    # deciding anything, and never let a failed request become a finding.
    retry = [r["doi"] for r in conn.execute(RETRY_ERRORS)]
    if retry:
        console.print(f"[yellow]{len(retry)} DOIs errored during the summary "
                      f"pass and will be re-asked first[/yellow]")

    sql = TARGETS + (f" LIMIT {int(args.limit)}" if args.limit else "")
    dois = retry + [r["doi"] for r in conn.execute(sql)]

    if args.probe:
        console.print(f"{len(dois):,} papers with at least one post to fetch")
        return 0
    if not dois:
        console.print("[green]nothing left[/green]")
        return 0

    console.print(f"[bold]Altmetric detail: {len(dois):,} papers[/bold] at "
                  f"{args.rate}s apart - about {len(dois) * args.rate / 3600:.1f} h")
    exclude = tuple(x for x in args.exclude_sources.split(",") if x)
    stats = asyncio.run(run(conn, dois, altmetric.load_key(), args.rate, exclude))
    console.print(f"\n[bold]{stats['ok']:,} papers, {stats['posts']:,} posts[/bold]")
    for k, v in stats.most_common():
        if k not in ("ok", "posts"):
            console.print(f"  {k:<12} {v:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
