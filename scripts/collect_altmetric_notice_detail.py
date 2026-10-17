"""The dated mentions of a retraction notice.

    python scripts/collect_altmetric_notice_detail.py --limit 100   # sample
    python scripts/collect_altmetric_notice_detail.py               # the rest

Run after collect_altmetric_notices.py. That pass asks "does Altmetric know
this notice, and how much attention did it get"; this one asks for the
individual mentions, with their dates and whatever text the licence permits.
The dates are the point: a mention of a notice is post-retraction by
construction, and it is what the media half of Stage 3 classifies.

Stored in altmetric_notice_posts, keyed on the NOTICE DOI, and not in
altmetric_posts. That table is keyed on original DOIs, and a notice written
into it would be counted by every query that asks how many papers were
mentioned. Keeping them apart is what lets the merge decide how a notice's
coverage attaches to the papers it retracts, rather than having a join decide
it silently - see analysis/notice_news_merge.R for the rule.

Resumable through altmetric_notice.detail_fetched_at. Same key, same gentle
rate, same hard stop when the service starts refusing.
"""
from __future__ import annotations

import argparse
import asyncio
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

# No channel whitelist, for the reason the paper collector records: an earlier
# version of that query enumerated seven channels and missed 1,793 papers whose
# mentions were on Bluesky, in videos, podcasts, Weibo, Q&A or peer review.
# Whatever comes back under `posts` is stored under its own name.
TARGETS = """
SELECT retraction_doi, n_papers FROM altmetric_notice
WHERE found = 1
  AND detail_fetched_at IS NULL
  AND json_extract(counts_json, '$.cited_by_posts_count') > 0
ORDER BY CAST(COALESCE(news_count, 0) AS INTEGER) > 0 DESC, n_papers DESC, news_count DESC
"""
# Notices with news go first: the client's question is how much news there is,
# and the 1,218 news-bearing notices are about twenty minutes of requests, so
# the news total is exact long before the social media behind the other
# notices has finished.


def store(conn, doi: str, data: dict) -> Counter:
    seen: Counter = Counter()
    for kind, items in (data.get("posts") or {}).items():
        if not isinstance(items, list):
            continue
        for p in items:
            if not isinstance(p, dict):
                continue
            name, url = altmetric.author_of(p)
            conn.execute(
                """INSERT OR REPLACE INTO altmetric_notice_posts
                   (retraction_doi, kind, post_id, title, summary, url, author,
                    author_url, posted_on, country, license, raw)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (doi, kind, altmetric.post_id(p), p.get("title"), p.get("summary"),
                 p.get("url"), name, url, p.get("posted_on"), p.get("country"),
                 p.get("license"), json.dumps(p, ensure_ascii=False)[:20000]))
            seen[kind] += 1
    return seen


def mark(conn, doi: str, n_posts, when_null: bool = False) -> None:
    conn.execute(
        """UPDATE altmetric_notice
           SET detail_posts = ?, detail_fetched_at = datetime('now')
           WHERE retraction_doi = ?""", (n_posts, doi))
    conn.commit()


async def run(conn, items, key, rate, exclude=()) -> Counter:
    stats: Counter = Counter()
    started = time.time()
    params = {"key": key}
    if exclude:
        params["exclude_sources"] = ",".join(exclude)
    async with httpx.AsyncClient() as client:
        for doi, n_papers in items:
            try:
                r = await client.get(ENDPOINT + doi, params=params,
                                     headers=HEADERS, timeout=120)
            except Exception as exc:                       # noqa: BLE001
                stats["error"] += 1
                stats[type(exc).__name__] += 1
                await asyncio.sleep(rate)
                continue

            # Altmetric meters three things separately. Reporting the headers
            # means a stop can say which limit was reached.
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
                # A summary exists but no post detail. Recorded, so it is never
                # re-requested.
                stats["no-detail"] += 1
                mark(conn, doi, 0)
                await asyncio.sleep(rate)
                continue

            if r.status_code != 200:
                stats["http" + str(r.status_code)] += 1
                await asyncio.sleep(rate)
                continue

            try:
                data = r.json()
            except Exception:                              # noqa: BLE001
                stats["bad json"] += 1
                await asyncio.sleep(rate)
                continue

            seen = store(conn, doi, data)
            mark(conn, doi, sum(seen.values()))
            stats["total"] += 1
            stats["posts"] += sum(seen.values())
            if seen.get("news"):
                stats["with_news"] += 1
                stats["news_items"] += seen["news"]
                stats["papers_behind_news"] += n_papers

            n = stats["total"] + stats["no-detail"]
            if n % 100 == 0:
                el = max(time.time() - started, 1)
                console.print(
                    f"[dim]{n:>6,}/{len(items):,}[/dim] "
                    f"posts {stats['posts']:,}  news items {stats['news_items']:,}  "
                    f"notices with news {stats['with_news']:,}  "
                    f"| {n/el*3600:,.0f}/h")
            await asyncio.sleep(rate)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--rate", type=float, default=1.0)
    # The key carries a separate allowance of 1,500,000 X post ids, and the
    # paper pass used it up (x-tweetidratelimit-remaining: 0), after which any
    # DOI with tweets is refused with 429 even though the hourly and daily
    # quotas are untouched. Excluding twitter returns every other source and
    # spends none of it. Tweets on notices are still counted by the summary
    # pass; only their dates are lost, and X was agreed as counts and timing.
    ap.add_argument("--exclude-sources", default="",
                    help="comma-separated Altmetric sources to leave out, e.g. twitter")
    args = ap.parse_args()
    exclude = tuple(s for s in args.exclude_sources.split(",") if s)

    key = altmetric.load_key()
    conn = db.connect()
    db.init(conn)
    db.migrate(conn)

    sql = TARGETS + (f" LIMIT {int(args.limit)}" if args.limit else "")
    items = [(r["retraction_doi"], r["n_papers"]) for r in conn.execute(sql)]
    if not items:
        console.print("[green]nothing left to collect[/green]")
        console.print("[dim]run collect_altmetric_notices.py first if the "
                      "summary pass has not finished[/dim]")
        return 0

    hours = len(items) * args.rate / 3600
    console.print(f"[bold]notice detail: {len(items):,} notices[/bold] "
                  f"covering {sum(n for _, n in items):,} papers "
                  f"- about {hours:.1f} hours\n")

    if exclude:
        console.print(f"[dim]leaving out: {', '.join(exclude)}[/dim]")
    stats = asyncio.run(run(conn, items, key, args.rate, exclude))

    console.print(f"\n[bold]detail for {stats['total']:,} notices[/bold] "
                  f"({stats['no-detail']:,} had none)")
    console.print(f"  mentions stored    : {stats['posts']:,}")
    console.print(f"  [bold]notices with news  : {stats['with_news']:,}[/bold]")
    console.print(f"  news items         : {stats['news_items']:,}")
    console.print(f"  errors             : {stats['error']:,}")
    console.print(f"\n  papers whose notice was covered: "
                  f"{stats['papers_behind_news']:,}")
    console.print("[dim]  a paper count, not a coverage count - see "
                  "analysis/notice_news_merge.R[/dim]")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
