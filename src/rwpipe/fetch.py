"""Render a publisher page with a real browser and capture it as MHTML.

Why MHTML and not raw HTML: the client asked for the *rendered* page. Publisher
platforms build the article body with JavaScript, so `requests.get` returns a
shell. MHTML additionally inlines the CSS/images into a single archive, so the
captured page stays readable years later - which matters for a dataset whose
whole point is studying content that publishers keep changing or removing.

Capture goes through the Chrome DevTools Protocol (Page.captureSnapshot);
Playwright has no first-class MHTML API, and this is Chromium-only.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
import time
from dataclasses import dataclass, asdict
from urllib.parse import urlparse

from playwright.async_api import Page, BrowserContext, Error as PWError

from . import config

# --- bot-wall detection -------------------------------------------------
# Publishers do NOT reliably signal a block with an HTTP error. Springer and
# Nature return 200 OK with a full-looking page titled "Client Challenge"
# carrying a CAPTCHA; IOP hands off to Radware (validate.perfdrive.com). So
# classification is content-based, never status-based - a status-only check
# silently files thousands of CAPTCHA pages as valid articles.
_BLOCK_TITLE = re.compile(
    r"just a moment|attention required|access denied|are you a robot|"
    r"verify you are human|request could not be satisfied|forbidden|"
    r"security check|blocked|client challenge|captcha|challenge validation|"
    r"one moment, please|checking your browser|page not found|error 4\d\d",
    re.I,
)
_BLOCK_BODY = re.compile(
    r"cf-browser-verification|cf_chl|px-captcha|_Incapsula_|recaptcha|"
    r"enable javascript and cookies to continue|unusual traffic|"
    r"enter the characters seen in the image|download audio captcha|"
    r"your request has been blocked|automated access",
    re.I,
)
# Hosts that are never an article: bot-management vendors and SSO shims.
_BLOCK_HOST = re.compile(
    r"(^|\.)perfdrive\.com$|(^|\.)validate\.|(^|\.)captcha|"
    r"^idp\.|(^|\.)datadome\.co$|(^|\.)geo\.captcha-delivery\.com$",
    re.I,
)
# A rendered publisher article page is heavy. Anything this small is an
# interstitial, a challenge, or a redirect shim - not content.
MIN_ARTICLE_HTML_BYTES = 50_000

# The captured MHTML must also be substantial. These are two different
# observations of the page, taken moments apart: page.content() is read first,
# the CDP snapshot after. A JS redirect landing between them passes the HTML
# check on the destination while the snapshot still holds the interstitial.
# 297 Elsevier captures were stored that way - 1.6 KB files whose entire
# content was "Subject: Redirecting" - and nothing flagged them, because the
# only size check ran against the other observation.
MIN_ARTICLE_MHTML_BYTES = 40_000

# Hosts that are a redirect stop, never a destination. Landing here means the
# JS hop has not finished yet.
_INTERSTITIAL_HOST = re.compile(r"^linkinghub\.elsevier\.com$|^doi\.org$", re.I)


# "Retraction Note: X" is the notice. "RETRACTED ARTICLE: X" is the article,
# flagged as retracted. Both begin with the same five letters and they are not
# the same document - a check that conflates them accepts the article whenever
# the notice was requested, which is precisely the substitution publishers and
# resolvers make. The adjective form is excluded explicitly.
NOTICE_TITLE = re.compile(
    r"^\s*(retraction|withdrawal|expression of concern|corrigend\w*|errat\w*|"
    r"notice of|withdrawn)\b", re.I)
# Only the explicit form. "RETRACTED ARTICLE: X" names which document this
# is; a bare "Retracted: X" does not - Wiley titles the notice that way
# and Elsevier titles the article that way, and no rule on the prefix
# alone can separate them.
ARTICLE_FLAG = re.compile(r"^\s*retracted\s+article\b", re.I)


def page_title_of(capture: str) -> str:
    m = re.search(r"<title[^>]*>(.{0,400}?)</title>", capture, re.I | re.S)
    if not m:
        return ""
    t = re.sub(r"<[^>]+>", " ", m.group(1))
    return re.sub(r"\s+", " ", t).strip()


def _norm_title(s: str) -> str:
    s = re.sub(r"^\s*(retracted article|retracted|retraction(?: note| notice)?|"
               r"withdrawn|expression of concern|correction|corrigendum|erratum)"
               r"\s*[:\-]?\s*", "", s or "", flags=re.I)
    s = re.sub(r"\s*[|\-–—]\s*[^|]{0,80}$", "", s)   # trailing journal name
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s.lower())).strip()


def title_matches(capture: str, expected: str, floor: float = 0.55) -> tuple[bool, float]:
    """Does the page claim to be the paper we asked for?

    The DOI check catches a page that is not this paper at all. This catches
    the subtler case: a page carrying the right DOI somewhere in its markup -
    a citation, a reference list, a "related articles" panel - while actually
    being something else.

    Titles are compared after stripping the retraction prefix and any trailing
    journal name, because the publisher writes "RETRACTED ARTICLE: X | Journal"
    where Retraction Watch holds "X".
    """
    if not expected:
        return True, 1.0                    # nothing to compare against
    page = page_title_of(capture)
    if not page:
        return True, 1.0                    # no title element; DOI check stands alone
    r = SequenceMatcher(None, _norm_title(expected)[:300], _norm_title(page)[:300]).ratio()
    return r >= floor, r


def role_matches(capture: str, kind: str) -> bool:
    """Advisory only: does a retraction-notice page read like a notice?

    Publishers do not agree on the wording. Wiley and Hindawi title the
    **notice** "Retracted: <original title>"; Elsevier and Springer title the
    **article** "RETRACTED ARTICLE: <title>". The same prefix therefore means
    opposite things depending on who published it, and a rule built on the
    prefix alone rejects correct Wiley notices - measured directly, at a title
    similarity of 1.00 with the DOI present in the file.

    So this is recorded, never decisive. A capture carrying the requested DOI
    and a matching title is that document; a naming convention cannot outvote
    two pieces of direct evidence.
    """
    if kind != "retraction":
        return True
    title = page_title_of(capture)
    if not title:
        return True
    if NOTICE_TITLE.match(title):
        return True
    # "RETRACTED ARTICLE:" with no other notice wording is the article page.
    return not ARTICLE_FLAG.match(title)


def contains_doi(capture: str, doi: str) -> bool:
    """Is this capture actually the paper it was requested for?

    Size cannot answer that, and for a month this project assumed it could.
    ScienceDirect returns HTTP 200 with 875 KB of scripts and stylesheets
    wrapped around the sentence "There was a problem providing the content you
    requested" - every size and status check passes and no article is present.
    All 10,049 such captures were later found to contain no article at all.
    Separately, 228 pages were archived under a DOI the page does not carry,
    because the resolver landed on a neighbouring article.

    Two decoding details, each of which invents failures when missed:

    * MHTML is quoted-printable, so a DOI can be split across lines by a soft
      break (`=

`). Those are removed before matching.
    * The same DOI appears as `10.1016/J.FOO` and `10.1016/j.foo`, and slashes
      are percent-encoded inside URLs. Matching is case-insensitive and tries
      the encoded form.

    A false negative here costs one refetch; a false positive costs a page that
    looks archived and is not. The check is deliberately generous.
    """
    if not doi or not capture:
        return False
    soft = ("=" + chr(13) + chr(10), "=" + chr(10))   # quoted-printable line breaks
    flat = capture
    for br in soft:
        flat = flat.replace(br, "")
    flat = flat.lower()
    d = doi.lower()
    return d in flat or d.replace("/", "%2f") in flat


def classify(status, host, title, body, min_bytes: int | None = None):
    """Return (blocked, reason). Order matters: cheapest, surest checks first.

    `min_bytes` overrides the article-size floor for callers fetching a
    population the floor was not measured on. 50 KB was calibrated on
    commercial publisher pages, where most of the bytes are navigation,
    scripts and recommendation rails; a repository serves the same article
    with none of that. Measured on fourteen open-access pages the floor got it
    backwards - academicjournals.org at 39-48 KB with 7-12 paragraphs and up to
    3,300 characters of prose was rejected, while ajol.info at 65 KB with four
    paragraphs and an abstract only was accepted. Every other check here is
    unchanged, including the one that matters most: a capture must still carry
    the DOI it is filed under, which is tested by the caller.
    """
    if status in (401, 403, 429, 503):
        return True, f"http {status}"
    if host and _BLOCK_HOST.search(host):
        return True, f"bot-management host: {host}"
    if title and _BLOCK_TITLE.search(title):
        return True, f"challenge title: {title[:60]}"
    m = _BLOCK_BODY.search(body[:200_000])
    if m:
        return True, f"challenge markup: {m.group(0)[:40]}"
    floor = MIN_ARTICLE_HTML_BYTES if min_bytes is None else min_bytes
    if len(body.encode("utf-8", "ignore")) < floor:
        return True, f"body too small ({len(body)//1024} KB) - interstitial"
    return False, None


@dataclass
class FetchResult:
    doi: str
    ok: bool
    final_url: str | None = None
    final_host: str | None = None
    http_status: int | None = None
    title: str | None = None
    blocked: bool = False
    block_reason: str | None = None
    mhtml_bytes: int | None = None
    html_bytes: int | None = None
    elapsed_ms: int | None = None
    capture_format: str | None = None
    title_similarity: float | None = None
    role_ok: bool | None = None
    error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


async def _read_settled(page: Page, settle_ms: int, tries: int = 3):
    """Read title and HTML from a page that may still be navigating.

    A redirect landing between `goto` and these reads destroys the execution
    context mid-call ("Execution context was destroyed"), losing the page even
    though nothing was wrong with it. Rather than treat that as a fetch failure,
    wait for the new document and read again - the navigation that interrupted
    us is usually the one that reaches the article.
    """
    last: Exception | None = None
    for _ in range(tries):
        try:
            title = (await page.title() or "").strip()
            body = await page.content()
            return title, body
        except PWError as exc:
            if "context was destroyed" not in str(exc) and "navigating" not in str(exc):
                raise
            last = exc
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=15_000)
            except PWError:
                pass
            await page.wait_for_timeout(settle_ms)
    raise last if last else PWError("unreachable")


async def capture_mhtml(page: Page, context: BrowserContext) -> str:
    """Serialise the fully rendered page to an MHTML string via CDP."""
    cdp = await context.new_cdp_session(page)
    try:
        snap = await cdp.send("Page.captureSnapshot", {"format": "mhtml"})
        return snap["data"]
    finally:
        await cdp.detach()


async def fetch_page(
    context: BrowserContext,
    doi: str,
    *,
    settle_ms: int = 2500,
    want_mhtml: bool = True,
    capture: str = "mhtml",
    expect_title: str | None = None,
    doc_role: str = "original",
    url: str | None = None,
    min_bytes: int | None = None,
) -> tuple[FetchResult, str | None]:
    """Resolve a DOI through doi.org, render it, and snapshot it.

    Returns (result, mhtml_or_None). Never raises for ordinary network or
    bot-wall failures - those are reported on the result so the caller can
    record them in the frontier and move on.

    `url` overrides the resolver for one case: an open-access copy of the same
    paper held somewhere the publisher does not control - PubMed Central, a
    repository. The DOI still resolves to the publisher's page, which is the
    page we cannot fetch, so the address has to be given rather than derived.
    Everything downstream is unchanged, identity checks included: the capture
    must still carry the DOI it is filed under, wherever it came from.
    """
    url = url or f"https://doi.org/{doi}"
    res = FetchResult(doi=doi, ok=False)
    page = await context.new_page()
    started = time.perf_counter()
    try:
        resp = await page.goto(
            url, wait_until="domcontentloaded", timeout=config.NAV_TIMEOUT_MS
        )
        # let client-side rendering and redirect chains settle
        await page.wait_for_timeout(settle_ms)

        # Still sitting on a redirect stop: the JS hop has not landed. Wait for
        # the URL to actually change rather than snapshotting the waypoint.
        if _INTERSTITIAL_HOST.search(urlparse(page.url).netloc or ""):
            try:
                await page.wait_for_url(
                    lambda u: not _INTERSTITIAL_HOST.search(urlparse(u).netloc or ""),
                    timeout=15_000)
                await page.wait_for_timeout(settle_ms)
            except PWError:
                pass          # never left the waypoint; classify() will catch it

        title, body = await _read_settled(page, settle_ms)

        # read the URL *after* settling: a redirect during the read means the
        # page recorded here should be where we ended up, not where we started
        res.http_status = resp.status if resp else None
        res.final_url = page.url
        res.final_host = urlparse(page.url).netloc or None
        res.title = title[:300]
        res.html_bytes = len(body.encode("utf-8", "ignore"))

        # classify bot walls before declaring success
        res.blocked, res.block_reason = classify(
            res.http_status, res.final_host, res.title, body, min_bytes
        )

        mhtml = None
        if want_mhtml and not res.blocked:
            if capture == "html":
                # WebKit has no CDP, so no MHTML. Keep the rendered HTML: it
                # already passed the 50 KB article floor in classify() above,
                # so no second size check is needed here.
                mhtml = body
                res.mhtml_bytes = res.html_bytes
                res.capture_format = "html"
            else:
                mhtml = await capture_mhtml(page, context)
                res.mhtml_bytes = len(mhtml.encode("utf-8", "ignore"))
                res.capture_format = "mhtml"
                # Verify the artefact we are actually keeping, not just the one
                # we inspected. A tiny snapshot missed the article.
                if res.mhtml_bytes < (MIN_ARTICLE_MHTML_BYTES
                                      if min_bytes is None
                                      else min_bytes * 8 // 10):
                    res.blocked = True
                    res.block_reason = (
                        f"capture too small ({res.mhtml_bytes // 1024} KB) - "
                        f"snapshot did not reach the article")
                    mhtml = None

        # Identity, last. Everything above establishes that *something* came
        # back; this establishes that it is the right document.
        #
        # Three checks, of differing authority. A missing DOI is decisive - the
        # file is not this paper. A title that does not match is decisive only
        # when the DOI is also missing or when the mismatch is severe, because
        # a page can carry the DOI in a reference list while being another
        # article. Role is recorded and never decisive: Wiley titles a notice
        # "Retracted: X" and Elsevier titles the article that way, so the same
        # wording means opposite things by publisher.
        if mhtml is not None and doi:
            has_doi = contains_doi(mhtml, doi)
            title_ok, sim = title_matches(mhtml, expect_title or "")
            res.title_similarity = round(sim, 3)
            res.role_ok = role_matches(mhtml, doc_role)

            if not has_doi:
                res.blocked = True
                res.block_reason = (
                    f"identity check failed: capture is "
                    f"{res.mhtml_bytes // 1024} KB but does not contain {doi}")
                mhtml = None
            elif expect_title and not title_ok and sim < 0.30:
                # DOI present but the page is plainly a different paper. 228
                # captures were filed under a DOI whose article they did not
                # contain, several of them the neighbouring article in the same
                # issue - which cites the one we asked for.
                res.blocked = True
                res.block_reason = (
                    f"identity check failed: contains {doi} but the page title "
                    f"matches the record at only {sim:.2f}")
                mhtml = None

        res.ok = not res.blocked and bool(res.final_url)
        return res, mhtml

    except PWError as exc:
        res.error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
        return res, None
    except Exception as exc:  # noqa: BLE001 - probe must never abort the run
        res.error = f"{type(exc).__name__}: {str(exc)[:200]}"
        return res, None
    finally:
        res.elapsed_ms = int((time.perf_counter() - started) * 1000)
        await page.close()
