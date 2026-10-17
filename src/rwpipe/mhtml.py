"""Get the HTML document back out of an MHTML archive.

An MHTML file is a MIME multipart message: the page, then every stylesheet,
script, font and image it loaded, each as its own part. Stage 2 needs exactly
one of those - the top-level HTML document - and treating the whole file as
text produces a soup of base64 and CSS in which every keyword search matches.

Three details decide whether the text that comes out is the page or a
plausible-looking corruption of it:

**Quoted-printable.** Long lines are broken with a trailing `=` and rejoined by
the reader. Miss it and a DOI, a title or a sentence splits in half - the
verification work found DOIs reported absent purely because of this.

**Charset per part.** The `Content-Type` of each part carries its own encoding,
and publishers are not consistent. Decoding everything as UTF-8 mangles the
Cyrillic, Chinese and accented titles, which are exactly the records where a
title comparison then fails for the wrong reason.

**Which part is the document.** Chromium writes the main page first, but the
file also contains HTML fragments - iframes, ad frames, embedded viewers. The
part matching `Snapshot-Content-Location` is the page that was actually
captured; falling back to "the first text/html part" is right often enough to
be dangerous and wrong often enough to matter.
"""
from __future__ import annotations

import email
import email.policy
import gzip
import html
import io
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Document:
    """One captured page, decoded."""
    html: str = ""
    url: str | None = None
    subject: str | None = None          # the page title at capture time
    captured_at: str | None = None
    parts: int = 0
    charset: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.html) and self.error is None


def _decode(part) -> tuple[str, str | None]:
    """Payload of one MIME part as text, with its declared charset."""
    raw = part.get_payload(decode=True)
    if raw is None:
        return "", None
    declared = part.get_content_charset()
    for enc in (declared, "utf-8", "cp1252", "latin-1"):
        if not enc:
            continue
        try:
            # Return the encoding that worked, not the one the part claimed.
            # Reporting the declaration made the column useless: 4,138 of 4,200
            # rows read NULL because the part declared nothing, while the bytes
            # had decoded cleanly as UTF-8 the whole time.
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace"), "utf-8/replace"


# The Internet Archive's banner, inserted into every page it replays: its
# "collected by" text ("Formed in 2009, the Archive Team ...") was extracted as
# the body of an Archive capture of 10.1177/1367006916648412 and graded "full"
# (review of 7 October 2026). Only the Archive writes these markers.
_WAYBACK_TOOLBAR = re.compile(r"<!--\s*BEGIN WAYBACK TOOLBAR INSERT\s*-->.*?"
                              r"<!--\s*END WAYBACK TOOLBAR INSERT\s*-->", re.S | re.I)


def strip_wayback_toolbar(html: str) -> str:
    return _WAYBACK_TOOLBAR.sub(" ", html) if "WAYBACK TOOLBAR" in html else html


def read(path: Path | str) -> Document:
    """Decode an archive, gzipped or not, MHTML, markup or PDF text."""
    p = Path(path)
    try:
        raw = gzip.open(p, "rb").read() if p.suffix == ".gz" else p.read_bytes()
    except Exception as exc:                               # noqa: BLE001
        return Document(error=f"read: {type(exc).__name__}")
    return parse(raw)


def _pdf_document(raw: bytes) -> Document:
    """Recover page text without changing or claiming to validate PDF bytes."""
    integrity = "lossy" if b"\xef\xbf\xbd" in raw else "recovered"
    doc = Document(parts=1, charset=f"pdf-text/{integrity}")
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw), strict=False)
        page_count = len(reader.pages)
    except Exception as exc:                               # noqa: BLE001
        doc.error = f"pdf-text: {type(exc).__name__}"
        return doc

    pages = []
    states = []
    has_text = False
    for number in range(page_count):
        text, error = "", ""
        try:
            text = reader.pages[number].extract_text() or ""
            text = text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
            if "\ufffd" in text:
                integrity = "lossy"
        except Exception as exc:                           # noqa: BLE001
            error = type(exc).__name__
            integrity = "lossy"
        status = "error" if error else ("ok" if text.strip() else "empty")
        has_text = has_text or status == "ok"
        states.append(f"{number + 1}={status}" + (f"({error})" if error else ""))
        detail = f' data-error="{html.escape(error, quote=True)}"' if error else ""
        pages.append(
            f'<section class="rwpipe-pdf-page" data-page="{number + 1}" '
            f'data-status="{status}"{detail}><pre>{html.escape(text)}</pre></section>'
        )
    doc.charset = f"pdf-text/{integrity}"
    if not has_text:
        doc.error = "pdf-text: no extractable text; pages=" + (",".join(states) or "none")
        return doc
    doc.html = (
        f'<html data-rwpipe-format="pdf-text" data-rwpipe-integrity="{integrity}" '
        f'data-rwpipe-page-count="{page_count}" '
        'data-rwpipe-graphics="unverified"><body>' + "\n".join(pages) + "</body></html>"
    )
    return doc


def parse(raw: bytes) -> Document:
    """Decode archive bytes already in memory, MHTML or plain markup.

    Split out of `read` so that a caller which must repair the bytes first - the
    Taylor & Francis hand-off, written through Windows text mode - decodes them
    by exactly the same rules as every stored archive.
    """
    if raw[:200].lstrip().startswith(b"%PDF"):
        return _pdf_document(raw)
    head = raw[:200].lstrip().lower()
    # Markup, in whatever dialect. This check has now been too narrow three
    # times: it knew MHTML, then HTML was added, then JATS XML from PubMed
    # Central (which opens with an XML declaration), and then Elsevier's
    # Article Retrieval API, whose response opens with
    # `<full-text-retrieval-response` and no declaration at all. Each time,
    # thousands of verified captures were filed as "no text/html part" - not
    # missing, not corrupt, just written in a container the reader did not
    # recognise.
    #
    # So the rule is now the general one rather than a list of dialects: a MIME
    # part begins with headers, and markup begins with a tag. If the first
    # non-space byte is "<", this is a document and not an envelope, and the
    # dialect is the extractor's problem rather than the reader's.
    if head.startswith(b"<"):
        for enc in ("utf-8", "cp1252", "latin-1"):
            try:
                return Document(html=strip_wayback_toolbar(raw.decode(enc)), parts=1,
                                charset=enc)
            except UnicodeDecodeError:
                continue
        return Document(html=strip_wayback_toolbar(raw.decode("utf-8", "replace")), parts=1)

    try:
        msg = email.message_from_bytes(raw, policy=email.policy.default)
    except Exception as exc:                               # noqa: BLE001
        return Document(error=f"parse: {type(exc).__name__}")

    doc = Document(
        url=msg.get("Snapshot-Content-Location"),
        subject=msg.get("Subject"),
        captured_at=msg.get("Date"),
    )

    html_parts: list[tuple[str, str | None, str | None]] = []
    for part in msg.walk():
        doc.parts += 1
        if part.get_content_type() not in ("text/html", "application/xhtml+xml"):
            continue
        text, charset = _decode(part)
        if text:
            html_parts.append((text, charset, part.get("Content-Location")))

    if not html_parts:
        doc.error = "no text/html part"
        return doc

    # Prefer the part whose location is the page that was captured.
    chosen = html_parts[0]
    if doc.url:
        for candidate in html_parts:
            if candidate[2] and candidate[2].rstrip("/") == doc.url.rstrip("/"):
                chosen = candidate
                break
    else:
        chosen = max(html_parts, key=lambda t: len(t[0]))

    doc.html, doc.charset = strip_wayback_toolbar(chosen[0]), chosen[1]
    return doc
