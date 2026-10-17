"""Recovered PDF routing remains explicit in future text exports."""
import importlib.util
from pathlib import Path
import pytest
from rwpipe import mhtml

spec = importlib.util.spec_from_file_location("pdf_pipeline_format", Path(__file__).resolve().parents[1] / "scripts/extract_text.py")
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)


@pytest.mark.parametrize("charset", ["pdf-text/lossy", "pdf-text/recovered"])
def test_detected_pdf_overrides_mislabeled_acquisition_format(charset):
    doc = mhtml.Document(charset=charset)
    assert pipeline.source_format(doc, "html") == "pdf"
    assert pipeline.source_format(doc, "mhtml") == "pdf"


@pytest.mark.parametrize("recorded", ["html", "mhtml", "jats", "elsevier-xml", None])
def test_non_pdf_recorded_formats_are_not_rewritten(recorded):
    assert pipeline.source_format(mhtml.Document(charset="utf-8"), recorded) == recorded
