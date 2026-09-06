"""Stage 2 extraction: the cases that were wrong in a shipped version.

Each test here corresponds to a defect found in real captures, not to a
hypothetical. The notice extractor went through four versions and every one of
them looked correct until measured against a page:

* v1 ran from the first cue to the last, so a Nature notice began correctly
  and continued 8,000 characters into the reference list, because "retracted"
  appears there too.
* v2 gave the structural selectors a free pass - they returned whatever the
  matched element held, up to 8,000 characters, and `div[class*='retract']`
  matches a container that on Dovepress wraps the whole article.
* v3 stopped at "References" - except `_clean` deleted that heading as junk
  before `_narrow` could use it, so the stop condition could never fire.
* v4 kept the heading and then dropped it again with a `len > 25` filter,
  because "References" is ten characters.

Three of those four are the same mistake: a rule that reads a marker, and
another rule upstream that removes it first.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from rwpipe import extract


def page(body_html: str) -> str:
    return f"<html><head><title>T</title></head><body>{body_html}</body></html>"


# --- the wording publishers actually use ---------------------------------
@pytest.mark.parametrize("text", [
    # Dovepress. Matched none of the first cue list: the request clause is
    # reversed ("at the author's request", not "at the request of the author")
    # and it is "and Publisher", never "the publisher".
    "At the author's request, we, the Editors and Publisher of OncoTargets "
    "and Therapy, have retracted the following article.",
    # the same, with the typographic apostrophe the site actually serves
    "At the author’s request, we, the Editors and Publisher have retracted it.",
    "This article has been retracted at the request of the Co-Editors in Chief.",
    "Retraction Note: Correlation between body mass index and outcome",
    "Retraction Notice to: Calcium and SOL Protease",
    "The Editors and Publisher of the journal have retracted this paper.",
    "This article has been withdrawn by the authors.",
    "Expression of Concern: results could not be reproduced",
    "The paper was retracted due to image duplication.",
])
def test_cue_matches_real_wording(text):
    assert extract.NOTICE_CUE.search(text)


@pytest.mark.parametrize("text", [
    "This article describes a retracted-gene assay in mice.",
    "Skip to main content Journals Why Publish With Us?",
    "References",
])
def test_cue_does_not_fire_on_ordinary_text(text):
    assert not extract.NOTICE_CUE.search(text)


# --- the notice must stop at the reference list --------------------------
def test_notice_stops_at_references():
    """The reason this test exists: 'References' was being stripped as junk
    before the stop condition could see it, twice, in two different filters."""
    html = page(
        "<p>This article has been retracted at the request of the editors.</p>"
        "<p>The authors could not provide the original data.</p>"
        "<p>References</p>"
        "<p>Smith J. A retracted old citation that must not be swallowed, "
        "of considerable length so it is not filtered for brevity.</p>"
    )
    e = extract.extract(html, kind="retraction")
    assert "could not provide the original data" in e.notice
    assert "must not be swallowed" not in e.notice


def test_notice_is_not_the_navigation_bar():
    """Walking raw DOM blocks put the site menu first: a <nav> div has no <p>
    children, so it qualified as a paragraph and led the notice."""
    html = page(
        "<div><a>Skip to main content</a><a>Journals</a><a>Why Publish With Us?</a>"
        "<a>Editorial Policies</a><a>Author Guidelines</a></div>"
        "<p>This article has been retracted because the images were duplicated.</p>"
    )
    e = extract.extract(html, kind="retraction")
    assert "Skip to main content" not in e.notice
    assert "images were duplicated" in e.notice


def test_reason_beyond_the_first_few_fragments_is_kept():
    """A fixed window of eight lines counted headings and bylines, so the
    sentence carrying the reason fell outside it."""
    filler = "".join(f"<p>Author {i} affiliation line of sufficient length here.</p>"
                     for i in range(6))
    html = page(
        "<p>This article has been retracted.</p>" + filler +
        "<p>The authors were unable to explain the image duplication or "
        "provide original data.</p>"
    )
    e = extract.extract(html, kind="retraction")
    assert "image duplication" in e.notice


# --- a standalone notice page --------------------------------------------
def test_standalone_notice_page_yields_a_notice():
    """A page that IS the notice has no banner to find. Requiring one returned
    an empty notice for a 4,244-character page whose whole text was the
    reason."""
    html = page(
        "<p>Following publication, the authors raised concerns regarding the "
        "duplication of images within the study and were unable to explain "
        "the image duplication, provide original data or verify the validity "
        "of the reported findings.</p>"
    )
    e = extract.extract(html, kind="retraction")
    assert e.notice, "a notice page must not come back empty"
    assert e.notice_method in ("cue", "body-is-notice")


def test_article_page_without_a_notice_stays_empty():
    """`body-is-notice` must not fire for an ordinary article, or every paper
    would appear to carry a withdrawal statement."""
    html = page("<p>" + "An ordinary methods paragraph. " * 40 + "</p>")
    e = extract.extract(html, kind="original")
    assert e.notice == ""
    assert e.notice_method == "none"


# --- the method is recorded so a bad rule can be found later --------------
def test_notice_method_is_recorded():
    html = page("<p>This article has been retracted for image duplication.</p>")
    e = extract.extract(html, kind="retraction")
    assert e.notice_method and e.notice_method != "none"


def test_notice_is_capped():
    """Unbounded capture is how a notice became 8,000 characters of reference
    list."""
    long_tail = "".join(f"<p>Paragraph {i} of a very long article body indeed.</p>"
                        for i in range(400))
    html = page("<p>This article has been retracted.</p>" + long_tail)
    e = extract.extract(html, kind="retraction")
    assert len(e.notice) <= 3000


# --- the article that is shipped as a JavaScript string -------------------
LT, GT = "\\u003c", "\\u003e"      # the six literal characters, not a tag


def test_body_escaped_inside_a_script_is_recovered():
    r"""Cureus serves the paper as a JS string with `\u003cp\u003e` for tags.
    Stripping scripts - which every extractor must do - takes the article with
    them, leaving 781 characters of navigation menu that passes every test for
    a successful capture: HTTP 200, six figures of bytes, right DOI, right
    title. 223 files were nearly written off as refusals on that basis.

    The first version of this test wrote "\u003c" in a plain string, where
    Python reads it as the character '<' - so it replaced '<' with '<', the
    fixture held no escapes at all, and the test failed against a working fix.
    Hence LT and GT, and the assertion that the fixture trips the threshold.
    """
    body = "<h3>Introduction</h3>" + "".join(
        "<p>Placenta previa is a serious obstetric complication, paragraph "
        "%d, of enough length to survive any brevity filter.</p>" % i
        for i in range(15))
    escaped = body.replace("<", LT).replace(">", GT)
    assert escaped.count(LT) >= 20, "fixture must trip the threshold"
    html = page('<script>window.article = "' + escaped + '";</script>'
                "<p>Home Specialties Browse Articles</p>")
    e = extract.extract(html, kind="original")
    assert "Placenta previa" in e.body
    assert len(e.body) > 200


def test_ordinary_page_is_not_touched_by_the_unescaper():
    """The transformation runs only on an unmistakable signal. Forty random
    captures from the archive contain zero escaped angle brackets; a page with
    a stray one must come through byte for byte."""
    html = page("<p>A method described as x " + LT + " y in the text.</p>"
                + "<p>" + "Ordinary body paragraph. " * 30 + "</p>")
    assert extract._unwrap_escaped_markup(html) == html


# --- publisher furniture ---------------------------------------------------
def test_furniture_line_is_removed_but_the_article_is_not():
    """The measurement that shaped this: "Similar content being viewed by
    others" sits at a median of 14% into the text on Springer and Nature pages -
    between the abstract and the body, not at the end. Truncating from the first
    marker would have emptied the body of 7,204 documents while reporting them
    cleaned."""
    body = ("The data indicated the importance of targeting the tumour "
            "microenvironment by blocking epigenetic mechanisms.\n"
            "Similar content being viewed by others\n"
            "Main\n"
            "The dynamic plasticity of metastases is largely dependent on "
            "epigenetic mechanisms and opportunistic adaptations.")
    out = extract._strip_boilerplate(body, extract._KEEP_BODY)
    assert "Similar content being viewed by others" not in out
    assert "Main\n" not in out + "\n"
    assert "dynamic plasticity of metastases" in out, "the article must survive"
    assert "tumour microenvironment" in out


def test_tail_of_furniture_is_truncated():
    """Springer closes a notice with a recommendation rail. Measured at a median
    of 93% into the text, so everything from it is furniture."""
    notice = ("The editor was not able to obtain current email addresses for "
              "the authors, and has retracted this article.\n"
              "Discover the latest articles, books and news in related "
              "subjects, suggested using machine learning.\n"
              "Cancer therapeutic resistance\n"
              "Non-Coding RNA Role in Cancer Biology")
    out = extract._strip_boilerplate(notice, extract._KEEP_NOTICE)
    assert "retracted this article" in out
    assert "machine learning" not in out
    assert "Non-Coding RNA" not in out


def test_a_notice_is_never_stripped_to_nothing():
    """The guard. If the patterns match nearly everything they have matched
    something they should not have, and the original is kept - a short notice
    is the document that says why a paper was withdrawn, and losing it costs
    more than leaving a cookie line in it."""
    notice = "We use cookies\nAccept all cookies\nManage consent preferences"
    out = extract._strip_boilerplate(notice, extract._KEEP_NOTICE)
    assert out == notice


def test_prose_about_cookies_survives():
    """The patterns anchor to a whole line, so an article that discusses
    cookies keeps its sentences."""
    body = ("We use cookies\n"
            + "This study examined how browser cookies are used to track "
              "readers across publisher websites, and what that means for "
              "reader privacy in scholarly publishing. " * 4)
    out = extract._strip_boilerplate(body, extract._KEEP_BODY)
    assert "browser cookies are used to track" in out
    assert not out.startswith("We use cookies")


def test_wolters_kluwer_footer_is_cut():
    """283 of the 285 documents that still carried a cookie banner after v5
    were one host. Ovid closes the article with the Wolters Kluwer footer and
    a consent panel, and it survived two rules by one word each: the copyright
    line reads "Copyright (c) 2026", with the year not immediately after the
    word, and the rights sentence reads "including those for text and data
    mining" where Wiley writes "including rights for". On the 285 it removed
    49% of the stored body and left one marker; on 300 controls it removed
    0.24% and emptied nothing."""
    body = ("This retrospective study of 412 patients examined postoperative "
            "recovery times following arthroscopic repair. " * 3
            + "\nCustomer Service\nSubmit a Service Request\nOpen Access Policy\n"
              "Manage Cookie Preferences\nYour California Privacy Choices\n"
              "Copyright \u00a9 2026 Wolters Kluwer N.V. and/or its subsidiaries "
              "or third-party licensors. All rights reserved, including those "
              "for text and data mining and artificial intelligence training.\n"
              "Your Privacy\n"
              "To give you the best possible experience we use cookies and "
              "similar technologies.\nPrivacy Preference Center\n")
    out = extract._strip_boilerplate(body, extract._KEEP_BODY)
    assert "arthroscopic repair" in out, "the article must survive"
    assert "we use cookies" not in out.lower()
    assert "Wolters Kluwer" not in out
    assert "California Privacy" not in out


def test_copyright_line_with_a_symbol_before_the_year():
    """"Copyright (c) 2026 X" was missed because the pattern demanded the year
    immediately after the word. Both spellings are footers."""
    for line in ("Copyright \u00a9 2026 Elsevier B.V.", "Copyright 2026 Elsevier B.V.",
                 "\u00a9 2026 Elsevier B.V."):
        body = ("The mechanism described here depends on substrate "
                "concentration, and was tested across four cell lines. " * 3
                + "\n" + line)
        out = extract._strip_boilerplate(body, extract._KEEP_BODY)
        assert "substrate" in out
        assert "Elsevier" not in out, line
