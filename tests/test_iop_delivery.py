"""The IOP delivery filename, and why it is only a hint.

IOP names each zip `<n>_<ISSN>_<vol>_<issue>_<article>_<escaped DOI>.zip`,
escaping the DOI as `.` -> `__` and `/` -> `_`. The parser takes the *last*
`_10__<registrant>_` run, because a volume or issue of 10 earlier in the name
looks exactly like the start of a DOI. The DOI read from the XML inside the
zip is still the authority; these cases only pin down the cross-check.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "check_iop_delivery",
    Path(__file__).resolve().parents[1] / "scripts" / "check_iop_delivery.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


@pytest.mark.parametrize("name,doi", [
    # The example in IOP's delivery note of 2026-09-11.
    ("6_2631-8695_8_3_039701_10__1088_2631-8695_ae3e7d.zip",
     "10.1088/2631-8695/ae3e7d"),
    # Volume 10: "_10_" earlier in the name must not be taken for "10.".
    ("6_0953-8984_10_1_001_10__1088_0953-8984_10_1_001.zip",
     "10.1088/0953-8984/10/1/001"),
    # A dot inside the suffix, escaped the same way as the one after "10".
    ("6_0013-4651_150_1_A1_10__1149_1__1531195.zip",
     "10.1149/1.1531195"),
    # One of the two DOIs IOP corrected on 2026-09-09: the file carries the
    # corrected DOI, and is filed under the original via doi_corrections.
    ("6_1751-8113_40_14_C01_10__1088_1751-8121_40_14_C01.zip",
     "10.1088/1751-8121/40/14/c01"),
])
def test_filename_doi(name, doi):
    m = _mod.NAME_DOI.match(name)
    assert m, f"no DOI segment found in {name}"
    assert _mod.unescape(m.group(1)) == doi


def test_non_delivery_filenames_are_ignored():
    assert _mod.NAME_DOI.match("README.txt") is None
    assert _mod.NAME_DOI.match("6_2631-8695_8_3_039701.zip") is None


def test_xml_doi_prefers_the_jats_article_id():
    xml = ('<?xml version="1.0"?><article><front><article-meta>'
           '<article-id pub-id-type="publisher-id">ae3e7d</article-id>'
           '<article-id pub-id-type="doi">10.1088/2631-8695/AE3E7D</article-id>'
           '</article-meta></front></article>')
    doi, how = _mod.xml_doi(xml)
    assert doi == "10.1088/2631-8695/ae3e7d"
    assert how == "jats-article-id"
