"""The Internet Archive collector: which address it asks about, and how it reads
the answers - without the network.

Both were wrong on 6-7 October 2026. The Springer mass notice's final address
was its sign-in redirect, so the Archive was asked about the sign-in page. And
the availability API said "none" for the SAGE notice that the CDX index lists,
while "no-snapshot" is recorded for good.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

_spec = importlib.util.spec_from_file_location("collect_wayback",
                                               ROOT / "scripts" / "collect_wayback.py")
cw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cw)


# --- which address --------------------------------------------------------------

def test_a_publisher_page_is_asked_about_as_it_is():
    page = "https://journals.sagepub.com/doi/10.1177/10641246251331509"
    assert cw.lookup_urls(page, "10.1177/10641246251331509") == [page]


def test_springer_sign_in_carries_the_article_in_redirect_uri():
    idp = ("https://idp.springer.com/authorize?response_type=cookie&client_id=springerlink"
           "&redirect_uri=https%3A%2F%2Flink.springer.com%2Farticle%2F10.1007%2Fs13277-017-5487-6")
    assert cw.lookup_urls(idp, "10.1007/s13277-017-5487-6") == [
        "https://link.springer.com/article/10.1007/s13277-017-5487-6",
        "https://doi.org/10.1007/s13277-017-5487-6"]


def test_radware_carries_the_article_in_ssc():
    pd = ("https://validate.perfdrive.com/?ssa=abc&ssb=def"
          "&ssc=https%3A%2F%2Fiopscience.iop.org%2Farticle%2F10.1088%2F1674-1056%2Fac785d&ssi=x")
    assert cw.lookup_urls(pd, "10.1088/1674-1056/ac785d")[0] == \
        "https://iopscience.iop.org/article/10.1088/1674-1056/ac785d"


def test_a_relative_return_url_is_resolved_against_the_captcha_page():
    cap = "https://nbpublish.com/captcha.php?return_url=%2Flibrary_read_article.php%3Fid%3D14361"
    assert cw.lookup_urls(cap, "10.7256/x")[0] == \
        "https://nbpublish.com/library_read_article.php?id=14361"


def test_a_cloudflare_token_is_removed_and_a_browser_error_is_skipped():
    cf = "https://karger.com/article/doi/10.1159/1?__cf_chl_tk=abc123"
    assert cw.lookup_urls(cf, "10.1159/1")[0] == "https://karger.com/article/doi/10.1159/1"
    assert cw.lookup_urls("chrome-error://chromewebdata/", "10.1016/j.x") == [
        "https://doi.org/10.1016/j.x"]


# --- reading the Archive's answers ------------------------------------------------

def ask(url, *answers):
    """snapshot_url against a fake Archive answering `answers` in turn:
    (status, body) pairs, the first for availability, the second for CDX."""
    seen = []

    def handler(request):
        seen.append(request)
        status, body = answers[len(seen) - 1]
        text = body if isinstance(body, str) else json.dumps(body)
        return httpx.Response(status, text=text)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await cw.snapshot_url(c, url)

    orig = cw.asyncio.sleep
    cw.asyncio.sleep = lambda s: orig(0)
    try:
        return asyncio.run(go()), seen
    finally:
        cw.asyncio.sleep = orig


NONE = {"archived_snapshots": {}}
SAGE = "https://journals.sagepub.com/doi/10.1177/10641246251331509"


def test_availability_found_needs_no_cdx():
    snap = {"archived_snapshots": {"closest": {"available": True, "status": "200",
            "url": "http://web.archive.org/web/20250802175535/" + SAGE,
            "timestamp": "20250802175535"}}}
    (got, why), seen = ask(SAGE, (200, snap))
    assert why == "ok" and got[1] == "20250802175535" and len(seen) == 1


def test_cdx_finds_what_availability_missed():
    cdx = [["timestamp", "original"], ["20250802175535", SAGE]]
    (got, why), seen = ask(SAGE, (200, NONE), (200, cdx))
    assert why == "ok" and got[0] == "http://web.archive.org/web/20250802175535/" + SAGE
    assert seen[1].url.params.get_list("filter") == ["statuscode:200", "mimetype:text/html"]


def test_the_doi_resolver_is_looked_up_as_a_redirect():
    (_, why), seen = ask("https://doi.org/10.1/x", (200, NONE), (200, []))
    assert why == "no-snapshot"
    assert seen[1].url.params.get_list("filter") == ["statuscode:30[0-9]"]


def test_only_a_cdx_table_can_say_none():
    assert ask(SAGE, (200, NONE), (200, []))[0][1] == "no-snapshot"
    assert ask(SAGE, (200, NONE), (200, [["timestamp", "original"]]))[0][1] == "no-snapshot"
    assert ask(SAGE, (200, NONE), (503, "Temporarily Offline"))[0][1] == "api-error"
    assert ask(SAGE, (200, NONE), (200, ""))[0][1] == "api-error"
    assert ask(SAGE, (200, NONE), (200, {"error": "x"}))[0][1] == "api-error"
    assert ask(SAGE, (200, NONE), (200, "null"))[0][1] == "api-error"
    assert ask(SAGE, (200, NONE), (200, [["timestamp", "original"], ["2020"]]))[0][1] == \
        "api-error"
    assert ask(SAGE, (429, ""))[0][1] == "api-error"


def test_an_availability_answer_that_was_an_error_capture_goes_to_cdx():
    snap = {"archived_snapshots": {"closest": {"available": True, "status": "404",
            "url": "http://web.archive.org/web/2020/" + SAGE, "timestamp": "2020"}}}
    cdx = [["timestamp", "original"], ["20250802175535", SAGE]]
    (got, why), seen = ask(SAGE, (200, snap), (200, cdx))
    assert why == "ok" and got[2] == "cdx" and len(seen) == 2


def test_carried_addresses_with_the_doi_come_first_and_long_ones_are_dropped():
    idp = ("https://idp.example.org/authorize?redirect_uri=https%3A%2F%2Fwww.google.com%2F"
           "&next=https%3A%2F%2Fpublisher.org%2Fdoi%2F10.1%2Fx")
    assert cw.lookup_urls(idp, "10.1/x")[0] == "https://publisher.org/doi/10.1/x"
    huge = "https://validate.perfdrive.com/?ssc=https%3A%2F%2Fx.org%2F" + "a" * 3000
    assert cw.lookup_urls(huge, "10.1/y") == ["https://doi.org/10.1/y"]
