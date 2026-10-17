"""Subject codes survive the trip from the Retraction Watch CSV.

Retraction Watch writes subjects as "(BLS) Biochemistry". Six of its seven
codes are plain letters; the seventh is ``B/T``. A pattern that allows only
letters matches the six and drops the seventh, which loses the code but keeps
the label, so nothing fails and nothing is empty - the column just goes blank
for 43,678 of 195,350 rows, all of them Technology, Computer Science, Data
Science and the business subjects.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rwpipe.clean import explode_list, split_subject

# Every code Retraction Watch actually uses, counted over data/clean/subjects.csv.
RW_CODES = ["BLS", "HSC", "B/T", "PHY", "SOC", "ENV", "HUM"]


@pytest.mark.parametrize("code", RW_CODES)
def test_every_retraction_watch_code_is_parsed(code):
    assert split_subject(f"({code}) Some Subject") == (code, "Some Subject")


def test_the_slashed_code_is_not_dropped():
    # The regression: B/T came back as (None, "(B/T) Computer Science").
    assert split_subject("(B/T) Computer Science") == ("B/T", "Computer Science")


def test_label_keeps_its_own_punctuation():
    assert split_subject("(B/T) Business - Economics") == ("B/T", "Business - Economics")


def test_unparseable_subject_keeps_the_whole_string_as_the_label():
    assert split_subject("Biochemistry") == (None, "Biochemistry")


def test_lowercase_is_not_a_code():
    assert split_subject("(bls) Biochemistry") == (None, "(bls) Biochemistry")


def test_surrounding_whitespace_is_stripped():
    assert split_subject("  (HSC)   Medicine  ") == ("HSC", "Medicine")


def test_explode_list_fills_the_code_column():
    clean = pd.DataFrame({
        "record_id": [1, 2],
        "subject_list": [["(B/T) Computer Science", "(BLS) Biochemistry"],
                         ["(B/T) Technology"]],
    })
    ex = explode_list(clean, "subject")
    assert list(ex["subject_code"]) == ["B/T", "BLS", "B/T"]
    assert list(ex["subject_label"]) == ["Computer Science", "Biochemistry", "Technology"]
    assert ex["subject_code"].isna().sum() == 0
