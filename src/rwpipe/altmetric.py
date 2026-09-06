"""Altmetric - the news and social media half of Stage 1b.

The client supplied his own API key for this, so the collector is deliberately
cautious: a low fixed rate, and a hard stop if the service starts refusing us.
Exhausting someone else's quota is not a recoverable mistake.

Two things worth knowing about the responses:

**404 is a result, not a failure.** Altmetric returns 404 for any DOI it has
never seen a mention of, which for a corpus of retracted papers is the common
case. That is the finding "this paper attracted no public attention", and it is
stored as such rather than discarded as an error - otherwise the denominator
quietly becomes "papers that happened to be popular".

**The summary endpoint has no time axis.** It reports totals to date, so unlike
the OpenAlex citation data this cannot be split before and after the retraction.
Reporting a total as though it were post-retraction attention would overstate
the finding, so the split is simply not available here and must be stated as a
limitation.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import httpx

from . import config

ENDPOINT = "https://api.altmetric.com/v1/doi/"

HEADERS = {
    "User-Agent": f"COMP5703-CS43 student project (mailto:{config.POLITE_CONTACT})",
    "Accept": "application/json",
}

# Counts worth keeping. "msm" is mainstream news media and "feeds" is blogs -
# between them they are what the client actually asked for.
COUNT_FIELDS = [
    "cited_by_msm_count",            # news outlets
    "cited_by_tweeters_count",
    "cited_by_accounts_count",
    "cited_by_posts_count",
    "cited_by_feeds_count",          # blogs
    "cited_by_wikipedia_count",
    "cited_by_rdts_count",           # reddit
    "cited_by_fbwalls_count",
    "cited_by_bluesky_count",
    "cited_by_weibo_count",
    "cited_by_videos_count",
    "cited_by_podcasts_count",
    "cited_by_policies_count",       # policy documents
    "cited_by_patents_count",
    "cited_by_peer_review_sites_count",
    "cited_by_qna_count",
    "readers_count",
]


def load_key() -> str:
    """Read the key from the environment, falling back to .env.

    Never logged and never written into any output - it is the client's
    credential, and he asked that it not be shared.
    """
    key = os.environ.get("ALTMETRIC_API_KEY")
    if key:
        return key
    env = config.ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("ALTMETRIC_API_KEY=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip()
    raise SystemExit(
        "ALTMETRIC_API_KEY not found. Put it in .env (which is gitignored) "
        "or set it in the environment."
    )


@dataclass
class Attention:
    doi: str
    found: bool = False              # False when Altmetric has no record at all
    altmetric_id: str | None = None
    score: float | None = None
    counts: dict[str, int] = field(default_factory=dict)
    details_url: str | None = None
    added_on: int | None = None
    error: str | None = None

    @property
    def news(self) -> int:
        return self.counts.get("cited_by_msm_count", 0)

    @property
    def social(self) -> int:
        """Everything a general audience posts, as opposed to news or academia."""
        return sum(self.counts.get(k, 0) for k in (
            "cited_by_tweeters_count", "cited_by_fbwalls_count",
            "cited_by_rdts_count", "cited_by_bluesky_count",
            "cited_by_weibo_count", "cited_by_videos_count"))


class QuotaExhausted(RuntimeError):
    """Raised when the service refuses us repeatedly - stop, do not grind."""


async def fetch(client: httpx.AsyncClient, doi: str, key: str,
                max_retries: int = 3) -> Attention:
    a = Attention(doi=doi)
    for attempt in range(max_retries):
        try:
            r = await client.get(ENDPOINT + doi, params={"key": key},
                                 headers=HEADERS, timeout=30)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if attempt == max_retries - 1:
                a.error = type(exc).__name__
                return a
            continue

        if r.status_code == 404:
            # no mentions anywhere - a real result about this paper
            a.found = False
            return a
        if r.status_code == 200:
            try:
                d = r.json()
            except Exception:                          # noqa: BLE001
                a.error = "bad json"
                return a
            a.found = True
            a.altmetric_id = str(d.get("altmetric_id") or "") or None
            a.score = d.get("score")
            a.details_url = d.get("details_url")
            a.added_on = d.get("added_on")
            a.counts = {k: int(d[k]) for k in COUNT_FIELDS
                        if isinstance(d.get(k), (int, float))}
            return a
        if r.status_code in (429, 403):
            # the key's quota, not a transient blip - the caller must stop
            raise QuotaExhausted(f"http {r.status_code}: {r.text[:120]}")
        a.error = f"http {r.status_code}"
        return a
    return a
