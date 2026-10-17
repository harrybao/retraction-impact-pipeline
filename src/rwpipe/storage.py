"""Where captured pages live on disk.

DOIs contain '/' and occasionally characters Windows forbids in filenames, so
the file name is a hash and the DOI->path mapping lives in the frontier table.
Files are sharded two levels deep (prefix, then hash head) to keep any single
directory well under the ~100k entries where Windows directory listing degrades.
"""
from __future__ import annotations

import gzip
import hashlib
from pathlib import Path

from . import config


def page_path(doi: str, prefix: str, fmt: str = "mhtml", tag: str | None = None) -> Path:
    """`tag` names a copy that must not share the document's own path: an
    Internet Archive capture is written as `<sha1>.wayback.mhtml.gz` beside the
    crawler's `<sha1>.mhtml.gz`. Without it the two overwrote each other: the
    Archive capture replaced the crawler's refusal page kept as evidence, and a
    teammate's import later replaced 10 Archive captures (found 7 October 2026).
    """
    h = hashlib.sha1(doi.encode("utf-8")).hexdigest()
    ext = "mhtml.gz" if fmt == "mhtml" else "html.gz"
    if tag:
        ext = f"{tag}.{ext}"
    return config.MHTML_DIR / prefix.replace("/", "_") / h[:2] / f"{h}.{ext}"


def write_capture(doi: str, prefix: str, text: str,
                  fmt: str = "mhtml", tag: str | None = None) -> tuple[Path, int, int]:
    """Write a capture in whichever format we managed to obtain.

    MHTML is the format the client asked for, and it is what Chromium
    produces. WebKit cannot: MHTML capture goes through the Chrome DevTools
    Protocol, which is Chromium-only. For the handful of publishers that refuse
    Chromium but answer WebKit, rendered HTML is the alternative - the article
    body, abstract and retraction notice all survive; what is lost is the
    inlined images. The format is recorded per document so no downstream stage
    can mistake one for the other.
    """
    path = page_path(doi, prefix, fmt, tag)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = text.encode("utf-8", "ignore")
    tmp = path.with_suffix(".part")
    with gzip.open(tmp, "wb", compresslevel=6) as fh:
        fh.write(raw)
    tmp.replace(path)
    return path, len(raw), path.stat().st_size


def write_mhtml(doi: str, prefix: str, mhtml: str) -> tuple[Path, int, int]:
    """Gzip the capture to disk.

    Returns (path, uncompressed_bytes, on_disk_bytes). Both are recorded: the
    uncompressed size is what storage projections are built from, the on-disk
    size is what actually fills the drive.
    """
    path = page_path(doi, prefix)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = mhtml.encode("utf-8", "ignore")
    tmp = path.with_suffix(".part")
    with gzip.open(tmp, "wb", compresslevel=6) as fh:
        fh.write(raw)
    tmp.replace(path)          # atomic: a crash mid-write never leaves a half file
    return path, len(raw), path.stat().st_size


def read_mhtml(path: str | Path) -> str:
    """Read an archive by the path recorded in the frontier.

    Paths are stored data-relative, so resolve before opening - otherwise every
    caller has to remember to do it, and most will not.
    """
    with gzip.open(resolve(path), "rb") as fh:
        return fh.read().decode("utf-8", "ignore")


def rel(path: Path) -> str:
    """Store paths relative to the data directory so the dataset is portable.

    Relative to DATA rather than the repo root: teammates may keep captures on
    a different drive (RWPIPE_DATA), and a merge has to resolve a teammate's
    recorded path against *their* data root, not ours.
    """
    try:
        return str(path.relative_to(config.DATA)).replace("\\", "/")
    except ValueError:
        return str(path)


def resolve(rel_path: str, data_dir: Path | None = None) -> Path:
    """Turn a recorded path back into a real one.

    Accepts both the current `mhtml/...` form and the earlier `data/mhtml/...`
    form written before paths became data-relative, so archives captured by
    either version still resolve.
    """
    base = data_dir or config.DATA
    r = str(rel_path).replace("\\", "/").lstrip("/")
    if r.startswith("data/"):
        r = r[len("data/"):]
    return base / r
