"""Article text from a PDF, for documents whose XML carries only metadata.

IOP delivered each article as a zip holding one JATS XML and one PDF. For 2,347
of the 2,552 that entered the corpus the XML is metadata only - title, abstract,
sometimes a line of the notice - and the PDF is the only copy of the text.
Without this they sit in the corpus as `thin`: verified, counted in coverage,
and empty of the text Stage 3 is meant to read.

What a PDF is depends on whose DOI it is filed under, and the rule follows:

* **An original article (or a DOI that is both)** - IOP prints the retraction
  notice as page 1 and the watermarked article after it. The PDF supplies the
  body; the notice comes from the XML where it has one, else from page 1.

* **A retraction notice's own DOI** - the PDF *is* the notice, a single page
  with a journal header and a copyright footer. It supplies the notice and
  never the body. The first version put the page into `body`, and the page's
  header and footer then outweighed the statement in classify()'s proportion
  test: 177 of 1,272 notice documents were graded `full`, i.e. counted as
  articles. A notice document has no article body to find. Where the cue
  matcher does not recognise the statement - "Publishers' Note: Withdrawal of
  the paper" - the whole page is the notice, the same `body-is-notice` rule the
  HTML route already applies.

Everything goes through the same `_clean` and cue matcher as the other routes,
the IP redaction included. Rows this changes gain `+pdf` in `method`, and a
notice found here is `pdf-cue` or `pdf-body-is-notice`, so the route is
queryable. It was added under extractor v9 without a version bump: the only
documents with a PDF are the IOP batch, and none had a document_text row before
this route existed.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import config, extract

_INDEX: dict[str, str] | None = None


def _index(conn) -> dict[str, str]:
    """doi -> PDF path relative to the data directory, loaded once per run."""
    global _INDEX
    if _INDEX is None:
        try:
            _INDEX = dict(conn.execute("SELECT doi, path FROM pdf_files"))
        except Exception:                                  # noqa: BLE001
            _INDEX = {}                                    # fresh database
    return _INDEX


def _fix_surrogates(s: str) -> str:
    """Re-pair UTF-16 surrogates, and replace any that cannot be paired.

    pypdf returns a character outside the Basic Multilingual Plane - the
    mathematical italic in an equation, U+1D465 and its neighbours - as two
    surrogate code points, and sometimes only one of the pair. SQLite refuses
    such a string outright. The first full run of this route died on U+D835 in
    a physics paper 384 documents in, and the rest of the batch got no text at
    all. A round trip through UTF-16 turns a pair back into the real character
    and a lone half into U+FFFD.
    """
    return s.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def pages_of(path: Path) -> list[str]:
    """Text of each page, or [] if the PDF will not open."""
    try:
        from pypdf import PdfReader
        return [_fix_surrogates(p.extract_text() or "")
                for p in PdfReader(str(path)).pages]
    except Exception:                                      # noqa: BLE001
        return []


def fill_from_pdf(conn, e, doi: str, kind: str) -> bool:
    """Fill `e` from the document's PDF. Returns True if anything changed."""
    rel = _index(conn).get(doi)
    if not rel:
        return False
    pages = pages_of(config.DATA / rel)
    whole = extract._clean("\n".join(pages))
    if not whole.strip():
        return False
    # v12: unwrap before the cue search. The statement is laid out across the
    # page ("This article ... has been" / "retracted by IOP Publishing"), so a
    # cue spanning the break could not match, and the notice began mid-sentence
    # on 839 of 2,261 pdf-cue documents, every one of them IOP.
    first = extract._clean(pages[0]) if pages else ""
    cand, how = extract._notice_from_text(extract._unwrap(first), kind)
    if not cand:
        cand, how = extract._notice_from_text(extract._unwrap(whole), kind)

    if kind == "retraction":
        # The PDF is the notice: it feeds `notice`, never `body`.
        if not cand:
            cand, how = whole[:3000], "body-is-notice"
        # The XML abstract is often a shortened copy of the statement; keep
        # whichever is the fuller version.
        if len(cand) <= len(e.notice or ""):
            return False
        e.notice, e.notice_method = cand, f"pdf-{how}"
        e.method = f"{e.method or 'unknown'}+pdf"
        return True

    body = extract._strip_boilerplate(whole, extract._KEEP_BODY)
    if not body:
        return False
    e.body = body
    if not (e.notice or "").strip() and cand:
        e.notice, e.notice_method = cand, f"pdf-{how}"
    e.method = f"{e.method or 'unknown'}+pdf"
    return True


_NOTICE_HEADING = re.compile(
    r"^(?:retraction(?:\s+(?:notice|statement|note))?|notice of retraction|"
    r"withdrawal(?:\s+(?:notice|statement))?|expression of concern|"
    r"publishers?' note|corrigendum|erratum)(?:\s*:.*)?$", re.I
)
_RESEARCH_HEADING = re.compile(
    r"^(?:original (?:research|article)|research article|abstract|"
    r"(?:1[.\s]+)?introduction)\s*:?(?:\s*$)", re.I
)
_NOTICE_OPENING = re.compile(
    r"^(?:this (?:article|paper)|the (?:article|paper|following article|editors?|"
    r"publishers?)|at the (?:request|authors?))\b", re.I
)


def _notice_page(text: str) -> bool:
    """An editorial page, not a research page with a retracted watermark."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if any(_RESEARCH_HEADING.match(line) for line in lines):
        return False
    opening = extract._unwrap("\n".join(lines[:30]))
    cue = extract.NOTICE_CUE.search(opening)
    if not cue:
        return False
    return (any(_NOTICE_HEADING.match(line) for line in lines[:20]) or
            (cue.start() < 400 and bool(_NOTICE_OPENING.match(cue.group()))))


def extract_capture(html: str, kind: str = "original") -> extract.Extracted:
    """Extract the escaped per-page wrapper produced by mhtml's PDF reader.

    The method records text recovery integrity, not intact PDF or image fidelity.
    This route does not invent article metadata from a notice cover's first DOI.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    root = soup.find("html", attrs={"data-rwpipe-format": "pdf-text"})
    if root is None:
        return extract.Extracted(error="not a pdf-text capture", method="pdf-text")
    integrity = root.get("data-rwpipe-integrity", "recovered")
    if integrity not in ("lossy", "recovered"):
        return extract.Extracted(error="invalid pdf-text integrity", method="pdf-text")
    pages = []
    for section in root.select("section.rwpipe-pdf-page"):
        pre = section.find("pre")
        if pre is not None and section.get("data-status", "ok") not in ("error", "empty"):
            text = extract._clean(_fix_surrogates(pre.get_text()), keep_stops=True)
            if text:
                pages.append(text)
    if not pages:
        return extract.Extracted(error="empty pdf-text capture", method=f"pdf-text/{integrity}")

    notice_pages = []
    for i, text in enumerate(pages):
        if not _notice_page(text):
            break
        notice_pages.append(i)
    notice, how = "", "none"
    if notice_pages:
        first = pages[notice_pages[0]]
        notice, how = extract._notice_from_text(extract._unwrap(first), kind)
    elif kind == "retraction":
        first = pages[0]
        notice, how = extract._notice_from_text(extract._unwrap(first), kind)
        if not notice:
            notice = extract._body_as_notice(first)
            how = "body-is-notice" if notice else "none"

    body = ""
    if kind != "retraction":
        body = extract._strip_boilerplate(
            "\n\n".join(text for i, text in enumerate(pages) if i not in notice_pages),
            extract._KEEP_BODY,
        )
    return extract.Extracted(body=body, notice=notice,
        notice_method=f"pdf-{how}" if notice else "none", method=f"pdf-text/{integrity}")
