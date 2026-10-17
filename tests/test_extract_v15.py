"""Extractor v15: "withdrawn at / by / due to" is not a notice on its own.

Until v14, "withdrawn" followed by at, by, following, due to, after or because
was a cue by itself, and that is how a methods section describes sampling and
drop-outs. For example, "About 2-3 mL samples
were withdrawn at an interval of 0.5, 1, 2, 3, 5 and 12 h" was the stored
retraction notice of 10.1016/j.msec.2017.03.154. What the change does to the
stored table is measured by scripts/notice_cue_impact.py (--all --out PATH).

STATEMENTS and METHODS below contain sentences from stored pages, with their
DOIs. The separate structural regression cases are synthetic. The last group
checks the property the change was built on: v15 only
takes cues away from v14, it never finds one where v14 found none.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rwpipe import extract  # noqa: E402

# v14's cue, rebuilt from v15's: the two differ in exactly this alternative.
V14_ALT = r"|(retracted|withdrawn) (at|by|following|due to|after|because)"
V15_ALT = r"|retracted (at|by|following|due to|after|because)" + extract.WITHDRAWN_CUE
assert V15_ALT in extract.NOTICE_CUE.pattern
V14_CUE = re.compile(extract.NOTICE_CUE.pattern.replace(V15_ALT, V14_ALT), re.I)


STATEMENTS = [
    # Wiley, on 161 stored notices
    "The above article, published online on 15 November 2020 in Wiley Online "
    "Library (wileyonlinelibrary.com), has been withdrawn by agreement between "
    "the journal Editor in Chief, Akkattu T. Biju, and Wiley Periodicals, LLC.",
    # Wiley, 10.1002/aisy.201900150 and seven other actual archives: the
    # journal name between subject and verb exceeds the subject-only pattern.
    "The above article from Advanced Intelligent Systems, published online on "
    "10 December 2019 in Wiley Online Library (https://wileyonlinelibrary.com) "
    "has been withdrawn by agreement among the authors, the Journal "
    "Editor-in-Chief, and Wiley-VCH Verlag GmbH & Co. KGaA.",
    # Taylor & Francis, 10.1080/01436597.2017.1369037: genuine withdrawal,
    # not an experimental essay or a participant leaving a study.
    "This Viewpoint essay has been withdrawn at the request of the academic "
    "journal editor, and in agreement with the author of the essay.",
    # Elsevier's withdrawal wording, and the same where the PDF text layer has
    # cut the subject (10.1016/j.isatra.2022.04.022, j.neulet.2020.135228)
    "This article has been withdrawn at the request of the author(s) and/or editor.",
    "Irkutsk 664033, Russia his article has been withdrawn at the request of the "
    "author(s) and/or editor.",
    "0304-3940/cle has been withdrawn at the request of the Editor-in-Chief.",
    # Elsevier, perfect tense with an adverb (10.1016/j.yrtph.2014.01.003)
    "This article has been erroneously withdrawn due to a unique error at Production.",
    # Elsevier, subjunctive after a request (10.1016/s0048-3575(03)00080-4)
    "In Seon Kim has requested that this article be withdrawn because of its "
    "simultaneous publication in another journal.",
    # SAGE's ahead-of-print withdrawal (10.1177/0886109917743445)
    "Ahead of Print article withdrawn by publisher.",
    # Cochrane (CD005176, CD000319)
    "This review is temporarily withdrawn following the authors' attention being "
    "brought to",
    "Withdrawn due to non‐compliance with The Cochrane Collaboration’s Commercial "
    "Sponsorship Policy",
    # J-STAGE (10.3164/jcbn.47_268e, 10.3164/jcbn.09-36)
    "The article published in J. Clin. Biochem. Nutr. 2007, 41(2), 132–138 was "
    "withdrawn by the Editors on August 9, 2010, because it was not original work",
    "This article was withdrawn by the Editorial Committee on April 22, 2014, "
    "because it was constituted a breach",
    # Springer's OnlineFirst formula (10.1007/s11069-011-9890-9, s12032-009-9352-3)
    "This article has been published OnlineFirst, but is withdrawn due to "
    "duplicate publication.",
    "This article has been published OnlineFirst, but is withdrawn at the "
    "author’s request.",
    # Springer, the subject a DOI (10.1007/s12149-011-0501-3)
    "Annals of Nuclear Medicine [DOI:10.1007/s12149-011-0501-3] is withdrawn due "
    "to prior publication.",
    # OUP, the subject a citation (10.1210/jc.2015-1965)
    "(J Clin Endocrinol Metab, Early Release 10 October 2014, doi: "
    "10.1210/jc.2014-2739), is withdrawn by the authors due to updated data.",
    # Thieme (10.1055/s-0044-1779464)
    "Withdrawn at Author's request",
    # Cognizant (10.3727/096504017x15016337254597)
    "THIS ARTICLE WAS WITHDRAWN BY THE PUBLISHERS IN OCTOBER 2020.",
    # Wiley, an earlier version (10.1002/cpt.2171)
    "It was withdrawn due to internal investigations into the origin of the data "
    "used within the article.",
    # J Lipid Res (10.1194/jlr.m005595err)
    "The following three articles were withdrawn by Dr. Stanley Rapoport after "
    "an investigation by the National Institute of Health",
    # Open Forum Infectious Diseases (10.1093/ofid/ofab358)
    "one of the largest studies on which this analysis was based was withdrawn "
    "due to fraudulent data",
    # arXiv's comment line (10.1103/physrevd.84.084020)
    "Withdrawn because of a crucial sign error in eq.(15)",
    # Full-corpus rehearsal: these genuine notices were lost by the first v15
    # draft because a protocol or a citation separated the subject and verb.
    # Cochrane, 10.1002/14651858.cd001356
    "This protocol has been withdrawn because it was published over two years "
    "ago and a draft review has not yet been submitted.",
    # Wiley, 10.1002/mmce.23130
    "The above article, published online on 01 March 2022 in Wiley Online Library "
    "(wileyonlinelibrary.com), has been withdrawn due to an error at the publisher "
    "which caused a duplicate of the article to be published online.",
    # Springer, 10.1007/s00402-014-1963-2
    "This paper, published in Volume 134, Issue 8, pages 1155-1162, DOI "
    "10.1007/s00402-014-1963-2, has been formally withdrawn by the two "
    "corresponding authors on ethical grounds because the article contains "
    "extensive and repeated instances of plagiarism.",
    # OUP, 10.1093/socrel/srz006
    "The article 10.1093/socrel/srz006 has been withdrawn because it is a "
    "duplicate of 10.1093/socrel/srz011. The publisher regrets the error.",
    # OUP, 10.1210/jc.2018-00197
    "The above-named article by Manning PJ, Dixit P, Satthenapalli VR, Katare R, "
    "and Sutherland WHF (J Clin Endocrinol Metab. [published online ahead of "
    "print 21 May 2019]; doi: 10.1210/jc.2018-00197) has been withdrawn by the authors.",
    # Cochrane, 10.1002/14651858.cd008427.pub3
    "This overview has been withdrawn due to the update not meeting the "
    "timelines and expectations of Cochrane and the PaPaS review group.",
]


METHODS = [
    # sampling (10.2147/ijn.s70449, 10.1016/j.msec.2017.03.154)
    "The samples were withdrawn at different time intervals over a period of 1 "
    "month and drug leakage from the formulations was analyzed",
    "About 2– 3 mL samples were withdrawn at an interval of 0.5, 1, 2, 3, 5 and "
    "12 h and filtered prior to collect them in airtight glass vials",
    # blood (10.1186/1472-6882-14-243, 10.1016/j.heliyon.2024.e33810)
    "Rats of the different groups were fasted overnight and the blood was "
    "withdrawn by retro orbital puncture under light and under anesthesia.",
    "The fish blood was withdrawn by cutting the tail, and it was collected in "
    "heparin tubes",
    # instruments (10.3892/etm.2020.8510, 10.1016/j.jss.2016.05.005)
    "The syringe was slowly withdrawn following 10 min.",
    "The suture was withdrawn by a hook on the guide wire through the cannula tunnel.",
    # participants (Cochrane CD005052, 10.2337/dc11-0338, 10.3945/ajcn.114.098616,
    # 10.1001/jamapsychiatry.2014.1054)
    "Three out of the nine RCTs reported mild adverse effects, and two patients "
    "were withdrawn due to the adverse effects.",
    "one participant was withdrawn because of two consecutive HbA 1c levels >8.5%.",
    "3 patients were excluded from the magnesium group [withdrawn due to personal "
    "reasons ( n = 2) and hospitalization ( n = 1)]",
    "95 dropped out and 7 were withdrawn by the staff (excessive substance use, 5; "
    "manic episode, 2).",
    "their participation is voluntary and can be withdrawn at any time during the "
    "data collection process.",
    # a trial in a review, a drug in a discussion (CD002306, 10.7759/cureus.52876)
    "One study (Conley 1999) was withdrawn by the manufacturers of risperidone.",
    "The rotavirus vaccine Rotashield was withdrawn following reports of "
    "intussusception",
    # Scientific Reports' consent statement (10.1038/s41598-021-01698-1)
    "the manuscript is under review by the journal, unless rejected by the "
    "Journal, or withdrawn by the author.",
    # a reference to another paper (10.1007/s00418-020-01907-4)
    "https://doi.org/10.1074/jbc.M112.385922 (now withdrawn by the authors)",
]


@pytest.mark.parametrize("text", STATEMENTS)
def test_a_withdrawal_statement_v14_found_is_still_found(text):
    assert V14_CUE.search(text)
    assert extract.NOTICE_CUE.search(text)


@pytest.mark.parametrize("text", METHODS)
def test_methods_text_is_no_longer_a_cue(text):
    assert V14_CUE.search(text)                  # what v14 stored as a notice
    assert not extract.NOTICE_CUE.search(text)


@pytest.mark.parametrize("text", STATEMENTS + METHODS)
def test_v15_never_finds_a_cue_v14_did_not(text):
    """Every v15 alternative carries v14's phrase, so a v15 match always holds
    a v14 match. It may begin earlier - at the subject ("this article be
    withdrawn") - but never where v14 had nothing."""
    for m in extract.NOTICE_CUE.finditer(text):
        assert V14_CUE.search(m.group(0)), m.group(0)


def test_the_perfect_tense_starts_where_v14_started():
    """The Elsevier and JATS routes start the notice at the match. Elsevier's
    text layer often cuts the subject ("his article has been withdrawn"), and
    there the notice began at "withdrawn" under v14 and still does."""
    text = "0304-3940/cle has been withdrawn at the request of the author(s)."
    assert extract.NOTICE_CUE.search(text).start() == V14_CUE.search(text).start()
    notice, _ = extract._notice_from_text(text)
    assert notice.startswith("withdrawn at the request of the author(s)")


def test_a_subject_moves_the_start_back_to_it():
    """The one way a v15 match differs from v14's on the same text: where the
    subject is a publication, the match starts at it."""
    text = ("In Seon Kim has requested that this article be withdrawn because of "
            "its simultaneous publication in another journal.")
    notice, _ = extract._notice_from_text(text)
    assert notice.startswith("this article be withdrawn because")


@pytest.mark.parametrize("text", [
    "The samples have been withdrawn at predetermined intervals.",
    "The drug has been withdrawn due to adverse effects.",
    "It was withdrawn because the patient reported nausea.",
    "The participant was withdrawn by agreement among the guardians and clinicians.",
    "The participant's essay was withdrawn by mutual agreement with their legal guardian.",
    "The article describes how the samples have been withdrawn at intervals.",
    "The article, which discusses a drug, notes that it was withdrawn due to adverse effects.",
    "Two patients were withdrawn at the request of their legal guardians.",
    "The participant was withdrawn by mutual agreement after an adverse event.",
])
def test_perfect_tense_or_an_ambiguous_pronoun_is_not_publication_evidence(text):
    assert V14_CUE.search(text)
    assert not extract.NOTICE_CUE.search(text)


# --- through the routes that stored the methods text ---------------------------

def _elsevier(*paras: str) -> str:
    body = "".join(f"<ce:para>{p}</ce:para>" for p in paras)
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<full-text-retrieval-response '
            'xmlns="http://www.elsevier.com/xml/svapi/article/dtd" '
            'xmlns:ce="http://www.elsevier.com/xml/common/dtd" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:xocs="http://www.elsevier.com/xml/xocs/dtd">'
            "<coredata><dc:title>An article</dc:title></coredata>"
            f"<originalText><xocs:doc>{body}</xocs:doc>"
            "</originalText></full-text-retrieval-response>")


METHODS_PARA = ("Drug release was studied in a USP dissolution apparatus. About 2– 3 mL "
                "samples were withdrawn at an interval of 0.5, 1, 2, 3, 5 and 12 h and "
                "filtered prior to collect them in airtight glass vials.")


def test_an_elsevier_article_is_not_given_its_methods_as_a_notice():
    """10.1016/j.msec.2017.03.154: the v14 notice was this sampling sentence
    and 3,000 characters of the article after it."""
    e = extract.extract(_elsevier(METHODS_PARA, "Results are shown in Fig. 2."),
                        kind="original")
    assert e.notice == ""
    assert e.notice_method == "none"


def test_the_statement_below_the_methods_is_the_notice():
    """With the methods sentence no longer a cue, the first match is the real
    statement further down."""
    e = extract.extract(_elsevier(
        METHODS_PARA,
        "This article has been withdrawn at the request of the author(s) and/or "
        "editor. The Publisher apologizes for any inconvenience this may cause."),
        kind="both")
    assert e.notice.startswith("This article has been withdrawn at the request of the author(s) and/or editor")
    assert "samples" not in e.notice


def test_an_html_page_does_not_start_its_notice_in_the_methods():
    """10.1186/s11671-017-1826-1, a Springer page whose v14 notice began at
    "5 ml samples of dissolution medium were withdrawn at 5, 10, 15 ... min"."""
    html = ("<html><head><title>t</title></head><body>"
            "<p>Then, 5 ml samples of dissolution medium were withdrawn at 5, 10, 15, "
            "20, 30, 45, and 60 min and passed through a 0.22 μm microporous "
            "membrane filter for HPLC analysis.</p>"
            "<p>The release profile is shown in Fig. 4 and discussed below.</p>"
            "</body></html>")
    e = extract.extract(html, kind="original")
    assert e.notice == ""
