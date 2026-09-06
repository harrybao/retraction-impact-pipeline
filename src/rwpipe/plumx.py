"""PlumX - the second of the two engagement sources the client named.

The client named exactly two in the Week 3 meeting:

    The client, in the week 3 meeting: "Social media news media: Altmetrics,
    PlumX metrics API"

PlumX is not a duplicate of Altmetric. Altmetric counts *attention* - news
articles, tweets, blog posts. PlumX additionally reports **usage**: abstract
views, full-text downloads, library holdings. That distinction matters for this
project, because a retracted paper that is still being *downloaded* is a
different and more direct kind of continued life than one that is still being
tweeted about. Neither source substitutes for the other, which is presumably
why he asked for both.

Access requires an Elsevier API key. Measured 2026-08-30 with no key:

    GET https://api.elsevier.com/analytics/plumx/doi/10.1016/j.jhep.2014.10.009
    401 {"statusCode":"AUTHENTICATION_ERROR","statusText":"No APIKey provided"}

Keys are issued self-service at https://dev.elsevier.com/. Entitlement to the
PlumX Metrics API specifically is tied to the account's institution, so a key
that authenticates may still be refused on this endpoint - `probe()` exists to
tell those two cases apart before a full run is started.

Like the Altmetric collector, this stops rather than grinds when the service
starts refusing us: the key belongs to an institution, not to us.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import httpx

from . import config

ENDPOINT = "https://api.elsevier.com/analytics/plumx/doi/"

# PlumX groups every metric under five categories. Usage and capture are the
# ones Altmetric does not report at all.
CATEGORIES = ["capture", "citation", "mention", "socialMedia", "usage"]


def load_key() -> str:
    """Elsevier API key, from the environment or .env. Never logged."""
    key = os.environ.get("ELSEVIER_API_KEY")
    if key:
        return key
    env = config.ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("ELSEVIER_API_KEY=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip()
    raise SystemExit(
        "ELSEVIER_API_KEY not found. Get one at https://dev.elsevier.com/ and "
        "put it in .env (which is gitignored), or set it in the environment."
    )


def load_insttoken() -> str | None:
    """Optional institutional token.

    Elsevier resolves entitlement by the *requesting IP* being inside the
    subscribing institution's registered range. A script run from a home
    connection is therefore unentitled even when the university subscribes,
    which is exactly the 401 we measured. `X-ELS-Insttoken` is Elsevier's own
    answer to that: a token issued to the institution that carries the
    entitlement off-campus. It has to be requested through the library.
    """
    tok = os.environ.get("ELSEVIER_INSTTOKEN")
    if tok:
        return tok
    env = config.ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("ELSEVIER_INSTTOKEN=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip() or None
    return None


def headers(key: str) -> dict[str, str]:
    h = {
        "X-ELS-APIKey": key,
        "Accept": "application/json",
        "User-Agent": f"COMP5703-CS43 student project (mailto:{config.POLITE_CONTACT})",
    }
    tok = load_insttoken()
    if tok:
        h["X-ELS-Insttoken"] = tok
    return h


@dataclass
class Metrics:
    doi: str
    found: bool = False            # False when PlumX has no record: a real result
    plumx_id: str | None = None
    totals: dict[str, int] = field(default_factory=dict)   # per category
    detail: dict[str, int] = field(default_factory=dict)   # per count_type
    error: str | None = None

    @property
    def usage(self) -> int:
        """Views, downloads, holdings - the signal Altmetric does not carry."""
        return self.totals.get("usage", 0)

    @property
    def captures(self) -> int:
        """Saves into reference managers - a reader intending to cite it."""
        return self.totals.get("capture", 0)

    @property
    def social(self) -> int:
        return self.totals.get("socialMedia", 0)

    @property
    def mentions(self) -> int:
        """News, blogs, comments, reviews."""
        return self.totals.get("mention", 0)


class NotEntitled(RuntimeError):
    """The key authenticates but this endpoint is not licensed to it."""


class QuotaExhausted(RuntimeError):
    """Rate limit or quota - stop, do not grind through someone's allowance."""


def _parse(d: dict, m: Metrics) -> Metrics:
    m.found = True
    m.plumx_id = str(d.get("id") or "") or None
    for cat in d.get("count_categories") or []:
        name = cat.get("name")
        if not name:
            continue
        m.totals[name] = int(cat.get("total") or 0)
        for ct in cat.get("count_types") or []:
            n = ct.get("name")
            if n:
                m.detail[f"{name}.{n}"] = int(ct.get("total") or 0)
    return m


# Elsevier grants this API to any key with an active Scopus subscription and
# resolves entitlement from the *requesting IP*. A 401 here therefore means
# "not entitled from this network", not "bad key" - measured 2026-08-30, the
# same key returned 200 from the Scopus abstract endpoint. Reporting it as a
# key problem would send the next person off to regenerate a working key.
NOT_ENTITLED_HINT = (
    "401 - not entitled from this network. Elsevier documents this API as "
    "available to any key with an active Scopus subscription, and the "
    "University of Sydney holds one, so this is an IP problem rather than a "
    "licensing one: entitlement is resolved from the requesting IP, and a "
    "home connection is outside the university's range. Try, in order: "
    "(1) re-run on the university network or VPN; (2) ask the library for an "
    "institutional token and set ELSEVIER_INSTTOKEN in .env."
)


async def fetch(client: httpx.AsyncClient, doi: str, key: str,
                max_retries: int = 3) -> Metrics:
    m = Metrics(doi=doi)
    for attempt in range(max_retries):
        try:
            r = await client.get(ENDPOINT + doi, headers=headers(key), timeout=30)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if attempt == max_retries - 1:
                m.error = type(exc).__name__
                return m
            continue

        if r.status_code == 404:
            # PlumX has never seen this DOI: "no measurable footprint", which
            # is a finding about the paper, not a failure of the request.
            m.found = False
            return m
        if r.status_code == 200:
            try:
                return _parse(r.json(), m)
            except Exception:                              # noqa: BLE001
                m.error = "bad json"
                return m
        if r.status_code == 401:
            raise NotEntitled(f"{NOT_ENTITLED_HINT} | {r.text[:120]}")
        if r.status_code == 403:
            raise NotEntitled(
                f"403 - authenticated but refused. {r.text[:160]}")
        if r.status_code == 429:
            raise QuotaExhausted(f"http 429: {r.text[:160]}")
        m.error = f"http {r.status_code}"
        return m
    return m


def probe(key: str, doi: str = "10.1016/j.jhep.2014.10.009") -> tuple[bool, str]:
    """One synchronous request, to separate 'no key' from 'not entitled'.

    Worth doing before a 60,000-DOI run: discovering a 401 on request 40,000
    instead of request 1 is the difference between a minute and a wasted
    evening.
    """
    try:
        r = httpx.get(ENDPOINT + doi, headers=headers(key), timeout=30)
    except Exception as exc:                               # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    where = " (institutional token in use)" if load_insttoken() else ""
    if r.status_code == 200:
        return True, f"entitled - PlumX returned metrics{where}"
    if r.status_code == 404:
        return True, (f"entitled - key accepted{where} "
                      "(this DOI simply has no record)")
    if r.status_code == 401:
        return False, NOT_ENTITLED_HINT
    if r.status_code == 403:
        return False, f"403 - authenticated but refused: {r.text[:160]}"
    return False, f"http {r.status_code}: {r.text[:160]}"
