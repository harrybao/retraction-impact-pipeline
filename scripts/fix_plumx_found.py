"""Repair the `found` column on rows collected before the semantics were fixed.

    python scripts/fix_plumx_found.py --dry-run
    python scripts/fix_plumx_found.py

`plumx.found` was set from the HTTP status: any 200 meant found. Measured
2026-09-07, this endpoint answers 200 with an empty envelope for a DOI it has
never heard of:

    GET .../analytics/plumx/doi/10.9999/definitely-not-a-real-doi-cs43-test
    200, 74 bytes, {"id_type":"doi","id_value":"..."}, no count_categories

So the 404 branch never fired, and every "PlumX holds no record of this paper"
was stored as "this paper has zero attention". Those are different findings and
only one of them belongs in a results table.

The repair needs no new requests. `totals_json` is NULL exactly when the
response carried no count_categories, so the empty-envelope rows identify
themselves. Idempotent, and safe to run while a collection is in progress.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console

from rwpipe import db

console = Console()

WRONG = "found = 1 AND totals_json IS NULL AND COALESCE(error,'') = ''"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = db.connect()
    conn.execute("PRAGMA busy_timeout = 15000")

    total = conn.execute("SELECT COUNT(*) FROM plumx").fetchone()[0]
    wrong = conn.execute(f"SELECT COUNT(*) FROM plumx WHERE {WRONG}").fetchone()[0]

    console.print(f"[bold]{total:,} rows[/bold] in plumx")
    console.print(f"{wrong:,} say found=1 with no metrics of any kind - "
                  f"those are PlumX's 'no record' answer")

    # A guard, not decoration: if any of these rows carries a metric, then
    # totals_json is not the marker this repair assumes it is, and the whole
    # rule is wrong rather than the rows.
    leak = conn.execute(
        f"""SELECT COUNT(*) FROM plumx WHERE {WRONG}
             AND usage_count + capture_count + mention_count
               + social_count + citation_count > 0""").fetchone()[0]
    if leak:
        console.print(f"[red]refusing: {leak:,} of them carry a nonzero metric, "
                      f"so totals_json is not the right marker[/red]")
        return 1

    if args.dry_run:
        console.print("[dim]dry run - nothing written[/dim]")
        return 0

    conn.execute(f"UPDATE plumx SET found = 0 WHERE {WRONG}")
    conn.commit()

    # Second repair, same cause. A DOI carrying a character a URL cannot hold
    # was requested unencoded, and this endpoint does not reject that - it
    # answers about whatever the truncated string resolved to, or about
    # nothing. Those rows look exactly like an honest "no record", so they
    # cannot be told apart after the fact; they have to be fetched again.
    # Marking `error` is how a row asks to be retried: targets() selects on it.
    special = [d for (d,) in conn.execute("SELECT doi FROM plumx")
               if any(ch in d for ch in "[]<>#?{}|^\\\" %;")]
    if special:
        conn.executemany(
            "UPDATE plumx SET error = 'refetch: doi was not url-encoded' "
            "WHERE doi = ?", [(d,) for d in special])
        conn.commit()
        console.print(f"[yellow]{len(special)} rows flagged for re-collection"
                      f"[/yellow] - their DOI needed encoding and did not get "
                      f"it. Re-run collect_plumx.py to pick them up.")

    now_found = conn.execute("SELECT COUNT(*) FROM plumx WHERE found = 1").fetchone()[0]
    no_rec = conn.execute("SELECT COUNT(*) FROM plumx WHERE found = 0 "
                          "AND COALESCE(error,'') = ''").fetchone()[0]
    console.print(f"[green]repaired {wrong:,}[/green]")
    console.print(f"  in PlumX with metrics : {now_found:,}")
    console.print(f"  PlumX holds no record : {no_rec:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
