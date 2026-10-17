"""Rules added at extractor v9, each of which was wrong on its first attempt.

The three tests here are not "does the feature work" - they are the specific
counter-examples that killed the first version of each rule, kept so that a
later simplification cannot quietly reintroduce the failure.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rwpipe import fetch
from rwpipe import extract
from rwpipe.extract import CONSENT_SEL, _clean, _redact_ips


# The project's real exit addresses are read from the environment and never
# written in code; the tests stand in documentation-range addresses (RFC 5737).
@pytest.fixture(autouse=True)
def _exits(monkeypatch):
    monkeypatch.setattr(extract, "KNOWN_EXITS", ("203.0.113.7", "198.51.100.23"))


# --- 1. IP redaction must not eat Enzyme Commission numbers ---------------
#
# The first rule redacted every IPv4. 564 documents carry an IPv4-shaped
# string and only 142 are an address; the other 422 are EC numbers in a corpus
# that is largely biomedical.

@pytest.mark.parametrize("line", [
    "Your Current IP Address: 203.0.113.7",
    "Powered by PubFactory - [203.0.113.7] - 203.0.113.7",
    "Ray ID: a30edb4 Your IP address: 203.0.113.7 Error reference 523",
    "Your IP address 203.0.113.7 - No institutional access",
    "IP-Adresse: 203.0.113.7",
])
def test_echoed_addresses_are_removed(line):
    assert "203.0.113.7" not in _redact_ips(line)


@pytest.mark.parametrize("line", [
    "cellulase (EC 3.2.1.4) was assayed at pH 5.0",
    "superoxide dismutase EC 1.15.1.1 activity increased",
    "beta-glucosidase, EC 3.2.1.21, and EC 3.2.1.2 were measured",
    "nitric oxide synthase EC 1.14.13.39",
    "classified under 6.4.11.1 and 1.11.1.6",
])
def test_enzyme_numbers_survive(line):
    assert _redact_ips(line) == line


def test_known_exit_removed_without_any_cue():
    """The two addresses this project goes out from are removed wherever they
    appear. Springer prints ours bare in footer navigation with no wording
    around it at all, so a cue-only rule would have missed those 3."""
    bare = "Legal notice Cancel contracts here 203.0.113.7 Not affiliated"
    assert "203.0.113.7" not in _redact_ips(bare)
    assert "198.51.100.23" not in _redact_ips("probe from 198.51.100.23")


def test_redaction_runs_inside_clean():
    assert "203.0.113.7" not in _clean("Your IP address: 203.0.113.7")


# --- 2. Consent widgets go; <ins> stays ----------------------------------
#
# The first version of this rule also dropped <footer>, <nav> and <ins>. On
# 300 sampled captures that emptied 6 real notices and halved 2 more, all
# Springer or Nature: <ins> means "inserted into a published document", which
# is what a retraction notice is. Thieme uses <ins> for a recommendation rail;
# Springer uses it correctly. The rule may not go back to dropping it.

def _strip_consent(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for t in soup.select(CONSENT_SEL):
        t.decompose()
    return soup.get_text(" ", strip=True)


def test_onetrust_dialogue_is_dropped():
    html = """<div><p>This article has been retracted.</p>
    <div id="onetrust-policy"><h2>We Care About Your Privacy</h2>
    <p>List of Partners (vendors)</p></div></div>"""
    out = _strip_consent(html)
    assert "This article has been retracted." in out
    assert "We Care About Your Privacy" not in out


def test_ins_is_not_dropped_because_springer_puts_notices_there():
    html = ('<div><ins>This article has been retracted by the Publisher '
            'because it was published in error.</ins></div>')
    assert "retracted by the Publisher" in _strip_consent(html)


@pytest.mark.parametrize("tag", ["footer", "nav", "aside"])
def test_semantic_furniture_is_not_dropped(tag):
    """Measured as having no benefit and non-zero risk, so they stay."""
    html = f"<div><{tag}>This article has been retracted.</{tag}></div>"
    assert "retracted" in _strip_consent(html)


# --- 3. An error page is not an article, whatever DOI it carries ----------
#
# 9 liebertonline.com captures were stored as articles whose whole body was
# 232 bytes of Cloudflare 523. They passed because the DOI is in the URL the
# error page was served at - the same hole as the 10,049 ScienceDirect
# "problem providing the content" captures.

@pytest.mark.parametrize("body", [
    "Atypon | Please Return Soon The site is under maintenance "
    "Origin is unreachable Error reference number: 523",
    "The origin web server is not reachable. Error reference number: 523",
    "Web server is returning an unknown error Ray ID: abc123",
])
def test_origin_error_pages_are_refused(body):
    assert fetch.ORIGIN_ERROR.search(body) is not None


def test_a_real_retraction_notice_is_not_refused():
    body = ("This article has been retracted at the request of the Editor. "
            "The authors were unable to provide original data.")
    assert fetch.ORIGIN_ERROR.search(body) is None


def test_origin_error_is_not_the_challenge_pattern():
    """The verifier must not reuse _BLOCK_BODY against stored MHTML.

    _BLOCK_BODY is written for a live fetch's rendered text. Matched against a
    stored archive - which carries every embedded script - it flagged 16,454
    captures as error pages and cut usable documents from 88,843 to 72,408.
    15,245 were intact Wiley articles whose only offence is embedding a
    reCAPTCHA widget for the login form: the word is in the file, the
    challenge never happened. Re-scanned with ORIGIN_ERROR the same 16,454
    yield 9 hits, which are the 9 real error pages.
    """
    wiley = ('<script src="https://www.google.com/recaptcha/api.js"></script>'
             '<article>This article has been retracted.</article>')
    assert fetch._BLOCK_BODY.search(wiley) is not None   # why it went wrong
    assert fetch.ORIGIN_ERROR.search(wiley) is None      # why it no longer does
