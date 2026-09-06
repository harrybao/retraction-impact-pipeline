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


def read(path: Path | str) -> Document:
    """Decode an archive, gzipped or not, MHTML or plain HTML."""
    p = Path(path)
    try:
        raw = gzip.open(p, "rb").read() if p.suffix == ".gz" else p.read_bytes()
    except Exception as exc:                               # noqa: BLE001
        return Document(error=f"read: {type(exc).__name__}")

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
                return Document(html=raw.decode(enc), parts=1, charset=enc)
            except UnicodeDecodeError:
                continue
        return Document(html=raw.decode("utf-8", "replace"), parts=1)

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
        if part.get_content_type() != "text/html":
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

    doc.html, doc.charset = chosen[0], chosen[1]
    return doc
