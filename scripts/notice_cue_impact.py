"""Read-only impact of the combined v15 notice repairs on stored pages.

    python scripts/notice_cue_impact.py --all --out PATH  # full read-only census
    python scripts/notice_cue_impact.py --workers 8
    python scripts/notice_cue_impact.py --base-rev none   # no git: stored rows only

Writes a CSV, optionally changed notices as JSONL, and the historical extractor
beside the CSV. The database is opened read-only and immutable; a non-empty WAL
is refused. No row of document_text is touched: every selected notice
below is extracted again in memory, by the path extract_text.py takes -
mhtml.read(), extract.extract() with the role targets() computes, and the IOP
PDF when the body is empty - and compared with the v14 notice stored for it.

**Which documents.** --all selects every currently usable stored page. Without
--all a candidate scan and a random control reduce the workload. Until v14
"withdrawn" followed by at / by / following /
due to / after / because was a cue on its own. v15 keeps the phrase only in
the wordings listed in extract.NOTICE_CUE, and every one of those still
contains it. The repaired v15 also changes publisher boundaries, so the
phrase-only candidate mode is NOT a full measurement of this combined repair.
Use --all for release decisions; candidate mode is a withdrawal-cue probe.
In candidate mode three places are searched: the stored v14 text (notice, body,
abstract), the archived page itself - the page route reads text that is not
stored, and a cue anywhere below a marker decides whether the notice ends
there (extract._walk) - and the IOP PDFs, which only the notice reads for a
notice page. Archive copies are also selected to measure toolbar removal;
controls test whether outputs without the phrase remain unchanged.

**Drift, told apart.** With git available the v14 extractor (--base-rev) is
run on the same page as well. A notice that the old code no longer reproduces
changed for some other reason since the v14 run - a library, a moved file -
and `cue_effect` records a difference between the baseline and repaired
extractor (including boundaries, despite the historical column name).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import random
import re
import sqlite3
import subprocess
import sys
import uuid
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from rwpipe import config, extract, mhtml, pdftext  # noqa: E402

# v14's alternative, as it was written: a literal space, case-insensitive.
PHRASE = re.compile(r"withdrawn (?:at|by|following|due to|after|because)", re.I)
# In a page the words can be split by inline markup ("withdrawn <em>by</em>")
# or by a line break, so the page search is looser. It only chooses which
# documents are re-extracted; a superset costs time, never a result.
LOOSE = re.compile(r"withdrawn\s*(?:at|by|following|due\s*to|after|because)", re.I)
TAG = re.compile(r"<[^>]*>")
TOOLBAR = re.compile(r"wayback machine|archive team", re.I)
# No address reaches the report: the snippets go through the same redaction
# as extracted text, and a bare IPv4 is masked on top, since the known exits
# come from the environment and may not be set where this runs.
IPV4 = re.compile(extract._IPV4)

# Current committed extractor is v14; keep a copy with the measurement output.
BASE_REV = "986ca02027cd0390c9c40a68f7bf3f4db1031520"
SNIPPET = 120

# Optional row-level adjudications. Empty means not recorded here; separate
# review evidence must not be represented as populated CSV review fields.
REVIEWED: dict[str, str] = {}


def ro_connect() -> sqlite3.Connection:
    wal = Path(str(config.DB_PATH) + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise RuntimeError("notice dry run needs a checkpointed database (non-empty WAL)")
    conn = sqlite3.connect(config.DB_PATH.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _snip(text: str) -> str:
    text = extract._redact_ips((text or "")[:SNIPPET].replace("\n", " | "))
    return IPV4.sub("[ip]", text)


# --- which documents ---------------------------------------------------------

def stored_candidates(conn) -> set[str]:
    out = set()
    for r in conn.execute("SELECT doi, notice, body, abstract FROM document_text"):
        if any(PHRASE.search(r[k] or "") for k in ("notice", "body", "abstract")):
            out.add(r["doi"])
    return out


def _page_has_phrase(row) -> tuple[str, bool]:
    doi, rel = row
    try:
        doc = mhtml.read(config.DATA / rel)
    except Exception:                                      # noqa: BLE001
        return doi, False
    html = doc.html or ""
    if "withdrawn" not in html.lower():
        return doi, False
    text = re.sub(r"\s+", " ", extract._unwrap_escaped_markup(html))
    import html as _html
    return doi, bool(LOOSE.search(_html.unescape(TAG.sub("", text))))


def _pdf_has_phrase(row) -> tuple[str, bool]:
    doi, rel = row
    pages = pdftext.pages_of(config.DATA / rel)
    return doi, bool(LOOSE.search(" ".join(pages)))


def archive_candidates(conn, workers: int) -> tuple[set[str], set[str]]:
    pages = conn.execute(
        """SELECT doi, source_path FROM document_text
           WHERE capture_format IN ('mhtml','html') AND source_path IS NOT NULL""").fetchall()
    try:
        pdfs = conn.execute("SELECT doi, path FROM pdf_files").fetchall()
    except sqlite3.OperationalError:
        pdfs = []
    with Pool(workers) as pool:
        in_page = {d for d, hit in pool.imap_unordered(
            _page_has_phrase, [tuple(r) for r in pages], chunksize=50) if hit}
        in_pdf = {d for d, hit in pool.imap_unordered(
            _pdf_has_phrase, [tuple(r) for r in pdfs], chunksize=10) if hit}
    return in_page, in_pdf


# --- re-extraction -------------------------------------------------------------

_BASE = None
_CONN = None


def _init(base_path: str | None) -> None:
    global _BASE, _CONN
    _CONN = ro_connect()
    if base_path:
        spec = importlib.util.spec_from_file_location("rwpipe_extract_base", base_path)
        _BASE = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = _BASE
        spec.loader.exec_module(_BASE)


def _run(mod, html: str, doi: str, role: str):
    """extract_text.main()'s two steps, with `mod` as the extractor."""
    e = mod.extract(html, kind=role)
    if not e.error and not (e.body or "").strip():
        pdftext.extract = mod                   # fill_from_pdf calls the module
        try:
            pdftext.fill_from_pdf(_CONN, e, doi, role)
        finally:
            pdftext.extract = extract
    return e


def _toolbar(old: str, new: str) -> str:
    return (f"{'yes' if TOOLBAR.search(old or '') else 'no'}->"
            f"{'yes' if TOOLBAR.search(new or '') else 'no'}")


def _measure(task: dict) -> dict:
    from extract_text import classify
    doc = mhtml.read(config.DATA / task["mhtml_path"])
    if not doc.ok:
        new = extract.Extracted(error=doc.error or "unreadable")
        base = None
    else:
        new = _run(extract, doc.html, task["doi"], task["role"])
        # Stored/new equality alone cannot rule out cancelling baseline drift.
        base = _run(_BASE, doc.html, task["doi"], task["role"]) if _BASE else None
    tb_notice = tb_body = ""
    if task["selected_by"] == "wayback":
        tb_notice = _toolbar(task["old_notice"], new.notice)
        tb_body = _toolbar(task["old_body"], new.body)
    return {
        "doi": task["doi"],
        "new_notice": new.notice or "",
        "new_method": new.notice_method,
        "new_quality": classify(new, task["role"]),
        "base_notice": None if base is None else (base.notice or ""),
        "toolbar_notice": tb_notice,
        "toolbar_body": tb_body,
        "new_error": new.error or "",
        "baseline_error": (doc.error or "unreadable") if not doc.ok else
                          ((base.error or "") if base is not None else ""),
    }


def base_module(rev: str, directory: Path) -> str | None:
    """Keep the comparison source alongside the measurement, never in data/."""
    if rev.lower() == "none":
        return None
    try:
        src = subprocess.run(["git", "-c", f"safe.directory={ROOT.as_posix()}",
                              "show", f"{rev}:src/rwpipe/extract.py"], cwd=ROOT,
                             capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        print(f"git cannot supply {rev}:src/rwpipe/extract.py - comparing with stored rows only")
        return None
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "extractor-v14-reference.py"
    path.write_bytes(src)
    return str(path)


def change_of(old: str, new: str) -> str:
    if old == new:
        return "same"
    if old and not new:
        return "lost"
    if new and not old:
        return "gained"
    return "changed"


def shift_of(old: str, new: str) -> str:
    """For a changed notice, where the new one begins relative to the old.

    `earlier`: the old opening is inside the new notice, after its start - the
    match now begins at the subject ("This article is withdrawn by"), or the
    statement above the methods passage is reached. `later`: the new opening is
    inside the old notice - the start moved past text the old cue had begun at.
    `same-start`: only the end differs. `elsewhere`: neither contains the other.
    """
    if not (old and new) or old == new:
        return ""
    head_old, head_new = old[:60], new[:60]
    if head_old == head_new:
        return "same-start"
    if new.find(head_old) > 0:
        return "earlier"
    if old.find(head_new) > 0:
        return "later"
    return "elsewhere"


def _checkpoint_signature(tasks, base_path):
    sources = [Path(__file__), ROOT / "scripts/extract_text.py", ROOT / "requirements.txt",
               *sorted((ROOT / "src/rwpipe").glob("*.py")), ROOT / "src/rwpipe/identity_aliases.json"]
    if base_path:
        sources.append(Path(base_path))
    def stamp(path):
        try:
            s = path.stat()
            return str(path.resolve()), s.st_size, s.st_mtime_ns
        except FileNotFoundError:
            return str(path.resolve()), None, None
    captures = [stamp(config.DATA / t["mhtml_path"]) for t in tasks]
    with ro_connect() as conn:
        try:
            captures.extend(stamp(config.DATA / r[0]) for r in conn.execute("SELECT path FROM pdf_files") if r[0])
        except sqlite3.OperationalError:
            pass
    data = dict(sources={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
                tasks=tasks, captures=captures, database=stamp(config.DB_PATH),
                python=sys.version,
                libraries={n: importlib.metadata.version(n) for n in ("trafilatura", "beautifulsoup4", "lxml", "pypdf")},
                redaction_hash=hashlib.sha256(os.environ.get("RWPIPE_KNOWN_EXITS", "").encode()).hexdigest())
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _publish_checkpoint(directory, name, payload):
    pending = directory / f"incomplete-{uuid.uuid4().hex}.json"
    pending.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
    pending.replace(directory / name)


def measure_tasks(tasks, workers, base_path, checkpoint=None):
    """Reuse only atomic, version/input-matched audit chunks; never DB results."""
    cached = {}
    if checkpoint:
        checkpoint.mkdir(parents=True, exist_ok=True)
        signature = _checkpoint_signature(tasks, base_path)
        manifest = checkpoint / "manifest.json"
        if manifest.exists():
            if json.loads(manifest.read_text(encoding="utf-8")) != {"signature": signature}:
                raise RuntimeError("checkpoint source or inputs changed; use a new checkpoint directory")
        else:
            _publish_checkpoint(checkpoint, "manifest.json", {"signature": signature})
        wanted = {t["doi"] for t in tasks}
        for chunk in checkpoint.glob("chunk-*.json"):
            for result in json.loads(chunk.read_text(encoding="utf-8")):
                if result["doi"] not in wanted or result["doi"] in cached:
                    raise RuntimeError("checkpoint has an unexpected or duplicate DOI")
                cached[result["doi"]] = result
        print(f"checkpoint: {len(cached):,} measured, {len(tasks)-len(cached):,} remaining", flush=True)
    pending = [t for t in tasks if t["doi"] not in cached]
    batch = []
    if pending:
        # Pool.imap can wait forever for a task lost when a native worker exits.
        # Futures surface a broken process pool; unordered completion also
        # retains later results rather than waiting behind one slow archive.
        with ProcessPoolExecutor(workers, initializer=_init, initargs=(base_path,)) as pool:
            futures = [pool.submit(_measure, task) for task in pending]
            for future in as_completed(futures):
                result = future.result()
                cached[result["doi"]] = result
                batch.append(result)
                if checkpoint and len(batch) >= 100:
                    _publish_checkpoint(checkpoint, f"chunk-{uuid.uuid4().hex}.json", batch)
                    batch = []
                if len(cached) % 1000 == 0:
                    print(f"re-extracted {len(cached):,}/{len(tasks):,}", flush=True)
        if checkpoint and batch:
            _publish_checkpoint(checkpoint, f"chunk-{uuid.uuid4().hex}.json", batch)
    return [cached[t["doi"]] for t in tasks]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=None, help="limit the census for a smoke test")
    ap.add_argument("--all", action="store_true",
                    help="re-extract every current usable document, not just cue candidates")
    ap.add_argument("--control", type=int, default=300)
    ap.add_argument("--base-rev", default=BASE_REV)
    ap.add_argument("--out", type=Path,
                    default=ROOT / "runs/checks/notice-cue-v15-impact.csv")
    ap.add_argument("--dump", type=Path, help="also write every notice in full (JSON lines)")
    ap.add_argument("--checkpoint", type=Path, help="audit-only atomic chunk directory for version/input-aware resume")
    args = ap.parse_args()

    conn = ro_connect()
    from extract_text import targets
    roles = {r["doi"]: r for r in targets(conn, args.limit, True)}

    stored = set() if args.all else stored_candidates(conn)
    in_page, in_pdf = (set(), set()) if args.all else archive_candidates(conn, args.workers)
    wayback = {r["doi"] for r in conn.execute(
        "SELECT doi FROM documents WHERE source_kind = 'wayback'")}
    affected = set(roles) if args.all else stored | in_page | in_pdf
    rest = sorted(set(roles) - affected - wayback)
    random.seed(20261013)
    control = set(random.sample(rest, min(args.control, len(rest))))
    print(f"phrase in stored text {len(stored):,}, in the page {len(in_page):,}, "
          f"in an IOP PDF {len(in_pdf):,}; {len(affected):,} documents; "
          f"{len(wayback)} Archive copies; control {len(control)}")

    def why(doi: str) -> str:
        if args.all:
            return "full-census"
        if doi in stored:
            return "stored-text"
        if doi in in_page:
            return "page-text"
        if doi in in_pdf:
            return "pdf-text"
        return "wayback" if doi in wayback else "control"

    tasks, old = [], {}
    for doi in sorted(affected | wayback | control):
        t = conn.execute("""SELECT t.doc_role, t.capture_format, t.host, t.notice,
                                   t.notice_method, t.quality,
                                   CASE WHEN d.source_kind = 'wayback' THEN t.body ELSE '' END AS body,
                                   d.source_kind
                            FROM document_text t JOIN documents d ON d.doi = t.doi
                            WHERE t.doi = ?""", (doi,)).fetchone()
        r = roles.get(doi)
        if r is None:
            continue                      # no longer usable: extract_text drops its row
        if t is None:
            meta = conn.execute("SELECT source_kind FROM documents WHERE doi=?", (doi,)).fetchone()
            t = dict(doc_role=r["doc_role"], capture_format=r["capture_format"],
                     host=r["final_host"], notice="", notice_method="not-stored",
                     quality="not-stored", body="", source_kind=meta["source_kind"])
        old[doi] = t
        sel = "wayback" if doi in wayback else why(doi)
        tasks.append({"doi": doi, "role": r["doc_role"], "mhtml_path": r["mhtml_path"],
                      "selected_by": sel, "old_notice": t["notice"],
                      "old_body": t["body"] if sel == "wayback" else ""})

    base_path = base_module(args.base_rev, args.out.parent)
    results = measure_tasks(tasks, args.workers, base_path, args.checkpoint)

    stats: Counter = Counter()
    rows = []
    for task, res in zip(tasks, results):
        t = old[task["doi"]]
        old_notice = t["notice"] or ""
        change = change_of(old_notice, res["new_notice"])
        if res["baseline_error"]:
            rerun, effect = "unreadable", "unknown"
        elif res["base_notice"] is None:
            rerun, effect = "not-run", "unchanged-output" if change == "same" else "unknown"
        else:
            rerun = "same" if res["base_notice"] == old_notice else "differs"
            effect = "yes" if res["base_notice"] != res["new_notice"] else "no"
        rows.append({
            "doi": task["doi"], "host": t["host"],
            # documents.source_kind: the route that produced the page. Empty
            # for the crawler and for the two routes that set none (see
            # R/stage1_pages.R).
            "source_kind": t["source_kind"] or "", "doc_role": task["role"],
            "capture_format": t["capture_format"], "selected_by": task["selected_by"],
            "notice_method": t["notice_method"], "new_notice_method": res["new_method"],
            "change": change, "start_shift": shift_of(old_notice, res["new_notice"]),
            "cue_effect": effect, "v14_rerun": rerun,
            "old_quality": t["quality"], "new_quality": res["new_quality"],
            "old_chars": len(old_notice), "new_chars": len(res["new_notice"]),
            "old_start": _snip(old_notice), "new_start": _snip(res["new_notice"]),
            "toolbar_notice": res["toolbar_notice"], "toolbar_body": res["toolbar_body"],
            "new_error": res["new_error"],
            "baseline_error": res["baseline_error"],
            "review": REVIEWED.get(task["doi"], ""),
        })
        stats[(task["selected_by"], change, effect)] += 1

    order = {"lost": 0, "changed": 1, "gained": 2, "same": 3}
    rows.sort(key=lambda r: (order[r["change"]], r["selected_by"], r["doi"]))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        fields = list(rows[0]) if rows else [
            "doi", "host", "source_kind", "doc_role", "capture_format", "selected_by",
            "notice_method", "new_notice_method", "change", "start_shift", "cue_effect",
            "v14_rerun", "old_quality", "new_quality", "old_chars", "new_chars",
            "old_start", "new_start", "toolbar_notice", "toolbar_body", "new_error", "baseline_error", "review"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    if args.dump:
        with open(args.dump, "w", encoding="utf-8") as f:
            for task, res in zip(tasks, results):
                if res["new_notice"] == (old[task["doi"]]["notice"] or ""):
                    continue
                f.write(json.dumps({"doi": task["doi"], "old": old[task["doi"]]["notice"] or "",
                                    "new": res["new_notice"], "base": res["base_notice"]},
                                   ensure_ascii=False) + "\n")

    print(f"\n{len(rows):,} documents -> {args.out}")
    print(f"{'selected by':<12} {'change':<8} {'cue effect':<10} n")
    for (sel, change, effect), n in sorted(stats.items()):
        print(f"{sel:<12} {change:<8} {effect:<10} {n:,}")
    quality = Counter((r["old_quality"], r["new_quality"]) for r in rows
                      if r["old_quality"] != r["new_quality"] and r["cue_effect"] != "no")
    for (a, b), n in quality.most_common():
        print(f"quality {a} -> {b}: {n}")
    shift = Counter((r["start_shift"], r["cue_effect"]) for r in rows if r["change"] == "changed")
    print("changed notices by start shift, cue effect:", dict(shift))
    for col in ("toolbar_notice", "toolbar_body"):
        tb = Counter(r[col] for r in rows if r[col])
        print(f"Archive toolbar text in {col[8:]} (stored -> re-extracted):", dict(tb))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
