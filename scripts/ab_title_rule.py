"""A/B of the crawler's title check: the committed fetch.py against the working copy.

    python scripts/ab_title_rule.py [--n 3000]

Only captures the crawler itself makes (mhtml, html) are scored: JATS and
Elsevier XML come from APIs and never meet this rule. Three sets, read-only:

* low       held documents that scored < 0.30 with their DOI present
* correct   a random sample of the other held documents, own title
* wrong     the same sample, each against another record's title

The rule refuses a page when the title check fails and the score is < 0.30.
On `correct` and `low` a refusal is an error; on `wrong` a refusal is the rule
doing its job. A page with no <title> element passes the title check by
design (the DOI check stands alone), so `wrong` also counts those.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import random
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from rwpipe import config  # noqa: E402
from rwpipe import fetch as new  # noqa: E402
from verify_archives import readable  # noqa: E402


def committed_fetch():
    src = subprocess.run(["git", "show", "HEAD:src/rwpipe/fetch.py"], cwd=ROOT,
                         capture_output=True, check=True).stdout
    tmp = Path(tempfile.mkdtemp()) / "fetch_old.py"
    tmp.write_bytes(src.replace(b"from . import config", b"from rwpipe import config"))
    spec = importlib.util.spec_from_file_location("fetch_old", tmp)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fetch_old"] = mod          # dataclasses look the module up
    spec.loader.exec_module(mod)
    return mod


def refuses(tag, mod, text, ok, sim, journal) -> bool:
    """The crawler's rule as each version applies it, the DOI being present."""
    if ok or sim >= 0.30:
        return False
    if tag == "old":
        return True
    return mod.informative_title(mod.page_title_of(text), journal)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3000)
    args = ap.parse_args()
    old = committed_fetch()
    conn = sqlite3.connect(f"file:{config.DB_PATH.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT d.doi, d.mhtml_path, a.host, a.title_similarity AS sim,
               COALESCE((SELECT r.title FROM records r WHERE r.original_doi = d.doi),
                        (SELECT r.title FROM records r WHERE r.retraction_doi = d.doi)) AS title,
               COALESCE((SELECT r.journal FROM records r WHERE r.original_doi = d.doi),
                        (SELECT r.journal FROM records r WHERE r.retraction_doi = d.doi)) AS journal
        FROM documents d JOIN archive_checks a ON a.doi = d.doi
        WHERE d.usable = 1 AND a.doi_found = 1 AND d.capture_format IN ('mhtml', 'html')
          AND d.mhtml_path IS NOT NULL""").fetchall()
    random.seed(20260918)
    low = [r for r in rows if (r["sim"] or 0) < 0.30]
    rest = random.sample([r for r in rows if (r["sim"] or 0) >= 0.30], args.n)
    wrong = [(r["title"], r["journal"]) for r in rest]
    wrong = wrong[1:] + wrong[:1]

    c = Counter()
    per_host_low = Counter()
    per_host_low_new = Counter()
    for group, items in (("low", low), ("correct", rest)):
        for i, r in enumerate(items):
            text = readable(config.DATA / r["mhtml_path"])
            if not text:
                continue
            c[f"{group}_n"] += 1
            for tag, mod in (("old", old), ("new", new)):
                ok, sim = mod.title_matches(text, r["title"] or "")
                ref = refuses(tag, mod, text, ok, sim, r["journal"])
                c[f"{group}_refused_{tag}"] += ref
                if group == "low" and tag == "new" and ref:
                    per_host_low_new[r["host"]] += 1
                if group == "correct":
                    wt, wj = wrong[i]
                    wok, wsim = mod.title_matches(text, wt or "")
                    c[f"wrong_refused_{tag}"] += refuses(tag, mod, text, wok, wsim, wj)
                    c[f"wrong_passed_{tag}"] += wok
            if group == "low":
                per_host_low[r["host"]] += 1
            if not new.page_title_of(text):
                c[f"{group}_no_title_element_new"] += 1
            if (i + 1) % 2000 == 0:
                print(f"  {group} {i + 1:,}/{len(items):,}", flush=True)
    c["wrong_n"] = c["correct_n"]

    out = Path(config.REPORTS) / "title-rule-ab-2026-09-18.csv"
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", "value"])
        w.writerow(["crawler-made held documents with DOI present", len(rows)])
        for k in sorted(c):
            w.writerow([k, c[k]])
        for h, n in per_host_low.most_common(12):
            w.writerow([f"low still refused (new) | host={h}", f"{per_host_low_new[h]} / {n}"])
    print(open(out, encoding="utf-8").read())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
