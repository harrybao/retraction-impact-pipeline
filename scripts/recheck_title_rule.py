"""Re-score the crawler's title check on stored archives, before and after a change.

    python scripts/recheck_title_rule.py [--sample 5000]

The crawler refuses a page that carries its DOI but whose title scores under
0.30 against the record. That rule was added after the main crawl, so no stored
archive was ever judged by it, and the handover test on 2026-09-18 found it
refusing correct IEEE and J-STAGE pages. This measures, on the archives we
hold (read-only):

* every held document whose stored score is under 0.30 with its DOI present -
  how many the rule still refuses after the fix;
* a random sample of the rest - whether any correct page now scores worse;
* the same sample paired with another record's title - whether a wrong paper
  is still refused, which is the case the rule exists for.
"""
from __future__ import annotations

import argparse
import csv
import random
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rwpipe import config, fetch  # noqa: E402
from verify_archives import readable  # noqa: E402

TITLE_SQL = """COALESCE((SELECT r.title FROM records r WHERE r.original_doi = d.doi),
                        (SELECT r.title FROM records r WHERE r.retraction_doi = d.doi))"""


def refused(ok: bool, sim: float) -> bool:
    """The crawler's rule, given that the DOI is present."""
    return not ok and sim < 0.30


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=5000)
    args = ap.parse_args()
    conn = sqlite3.connect(f"file:{config.DB_PATH.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(f"""
        SELECT d.doi, d.mhtml_path, a.host, a.title_similarity AS old, {TITLE_SQL} AS title
        FROM documents d JOIN archive_checks a ON a.doi = d.doi
        WHERE d.usable = 1 AND a.doi_found = 1 AND d.mhtml_path IS NOT NULL""").fetchall()
    low = [r for r in rows if (r["old"] or 0) < 0.30]
    rest = [r for r in rows if (r["old"] or 0) >= 0.30]
    random.seed(20260918)
    sample = random.sample(rest, min(args.sample, len(rest)))
    titles = [r["title"] for r in sample]
    shuffled = titles[1:] + titles[:1]          # every page against another record

    out = Path(config.REPORTS) / "title-rule-recheck-2026-09-18.csv"
    per_host = Counter()
    per_host_fixed = Counter()
    stats = Counter()
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["set", "doi", "host", "old_similarity", "new_similarity",
                     "new_title_ok", "refused_after", "page_title"])
        for group, items in (("low", low), ("rest", sample)):
            for i, r in enumerate(items):
                text = readable(config.DATA / r["mhtml_path"])
                if not text:
                    stats[f"{group}_unreadable"] += 1
                    continue
                ok, sim = fetch.title_matches(text, r["title"] or "")
                page = fetch.page_title_of(text)
                ref = refused(ok, sim)
                stats[f"{group}_n"] += 1
                stats[f"{group}_refused_after"] += ref
                if group == "low":
                    per_host[r["host"]] += 1
                    per_host_fixed[r["host"]] += not ref
                else:
                    stats["rest_now_below_030"] += sim < 0.30 and not ok
                    stats["rest_score_dropped_0.1+"] += sim < (r["old"] or 0) - 0.1
                    wok, wsim = fetch.title_matches(text, shuffled[i] or "")
                    stats["wrong_title_n"] += 1
                    stats["wrong_title_refused"] += refused(wok, wsim)
                    stats["wrong_title_accepted_ok"] += wok
                w.writerow([group, r["doi"], r["host"], r["old"], round(sim, 3),
                            int(ok), int(ref), page[:120]])
                if (i + 1) % 2000 == 0:
                    print(f"  {group} {i + 1:,}/{len(items):,}", flush=True)

    summary = Path(config.REPORTS) / "title-rule-recheck-summary-2026-09-18.csv"
    with open(summary, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", "value"])
        w.writerow(["held documents with DOI present", len(rows)])
        w.writerow(["of those scoring < 0.30 before (refused by the rule)", len(low)])
        for k in sorted(stats):
            w.writerow([k, stats[k]])
        for h, n in per_host.most_common(15):
            w.writerow([f"low | host={h} | fixed of", f"{per_host_fixed[h]} / {n}"])
    print(open(summary, encoding="utf-8").read())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
