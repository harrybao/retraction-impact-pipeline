"""What a PlumX response means, as opposed to what it looked like it meant.

Every case here is a measurement taken against the live endpoint on 2026-09-07,
frozen so the conclusion cannot quietly reverse. The class of bug is the one
this project keeps meeting: a value that is correct about the request and wrong
about the paper.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rwpipe import plumx


def _m(doi="10.1234/x"):
    return plumx.Metrics(doi=doi)


# --- what "found" is allowed to mean -----------------------------------------

def test_empty_envelope_is_not_found():
    """The measured response for a DOI PlumX has never heard of.

        GET .../doi/10.9999/definitely-not-a-real-doi-cs43-test
        -> 200, 74 bytes, no count_categories

    It is a 200, so the 404 branch never runs. If `found` is taken from the
    status code, "PlumX holds nothing on this paper" is stored as "this paper
    has zero attention" - and 947 rows had been recorded that way.
    """
    m = plumx._parse({"id_type": "doi",
                      "id_value": "10.1234/x"}, _m())
    assert m.found is False
    assert m.totals == {}


def test_categories_present_is_found():
    m = plumx._parse({"id_type": "doi", "id_value": "10.1234/x",
                      "count_categories": [
                          {"name": "capture", "total": 50,
                           "count_types": [{"name": "READER_COUNT", "total": 50}]}]},
                     _m())
    assert m.found is True
    assert m.captures == 50
    assert m.detail["capture.READER_COUNT"] == 50


# --- identity: the metrics must be about the paper they are filed under ------

def test_a_different_doi_in_the_reply_is_refused():
    """Measured: requesting `10.1080/09645292.2010.545518#.us5z4lssxk0` without
    encoding returns 200 and metrics for `10.1080/09645292.2010.545518`,
    because everything after "#" never leaves the client. It does not fail; it
    answers about another paper. Only the echoed id_value shows it.
    """
    m = plumx._parse({"id_value": "10.1080/09645292.2010.545518",
                      "count_categories": [{"name": "capture", "total": 99,
                                            "count_types": []}]},
                     _m("10.1080/09645292.2010.545518#.us5z4lssxk0"))
    assert m.found is False
    assert "different doi" in (m.error or "")
    assert m.captures == 0


def test_percent_escaped_doi_still_matches_its_echo():
    """14 DOIs in this corpus are stored already-escaped. PlumX echoes them
    decoded, so a literal comparison would report a mismatch on every one.
    """
    doi = "10.1002/(sici)1096-8628(19961230)66:4%3c403::aid-ajmg4%3e3.0.co;2-l"
    echo = "10.1002/(sici)1096-8628(19961230)66:4<403::aid-ajmg4>3.0.co;2-l"
    m = plumx._parse({"id_value": echo,
                      "count_categories": [{"name": "capture", "total": 3,
                                            "count_types": []}]}, _m(doi))
    assert m.error is None
    assert m.found is True


# --- the address ------------------------------------------------------------

def test_url_encodes_what_a_url_cannot_carry():
    """Measured raw: brackets -> HTTP 400, "#" -> another paper's metrics."""
    assert "[" not in plumx._url("10.3157/0013-872x(2007)118[512:ansopb]2.0.co;2")
    assert "#" not in plumx._url("10.1080/09645292.2010.545518#.us5z4lssxk0")


def test_url_leaves_slashes_and_existing_escapes_alone():
    """Both are load-bearing. Encoding "/" makes the path wrong; re-encoding
    "%" turns %3c into %253c and the lookup returns nothing. Measured: with
    safe="/%" all 16 affected DOIs resolve, 15 of them with metrics.
    """
    assert plumx._url("10.1016/j.x.2020.01.001").endswith("10.1016/j.x.2020.01.001")
    assert "%253c" not in plumx._url("10.1002/1096-9098(200007)74:3%3c201::aid-jso8%3e3.0.co;2-5")
