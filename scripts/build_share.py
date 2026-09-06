"""Assemble the folder teammates work from.

    python scripts/build_share.py

Everything here is derived. Run the three generators first if the database has
moved on:

    python scripts/clean_data.py
    python scripts/export_tables.py
    python scripts/export_master.py

Two rules this encodes:

**Small files stay loose, big ones go in the zip.** GitHub renders a CSV under
a few MB in the browser, so the summary tables anyone might want to glance at
are left unpacked and only the bulk data is archived. A teammate answering
"which publishers blocked us" should not have to download 60 MB first.

**One master table, not two.** `cs43_master.csv` is the join everyone uses.
`impact_master.csv` exists in `data/clean` for internal work but is deliberately
kept out of this folder: two files both called a master is how half a team ends
up analysing a different dataset from the other half.
"""
from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console

from rwpipe import config

console = Console()

CLEAN = config.DATA / "clean"
SHARE = config.DATA / "share"

# Browsable on GitHub without downloading anything.
LOOSE = [
    "crawl_by_host.csv",
    "crawl_by_publisher.csv",
    "failure_reasons.csv",
    "blocked_publishers.csv",
    "wikipedia_citations.csv",
    # Small enough to read in the browser, and both answer a question people
    # keep asking by hand: where a free copy exists, and what each probe
    # actually returned from which IP.
    "wayback_captures.csv",
    "probe_results.csv",
    # The gap, aggregated. 14 MB of per-document detail goes in the zip; this
    # is the version somebody can open in a browser and answer "what is still
    # missing and is anyone doing anything about it".
    "not_collected_by_host.csv",
    "dtypes.json",
]

# The bulk, zipped.
ZIPPED = [
    "cs43_master.csv",
    "openalex_engagement.csv",
    "altmetric.csv",
    "api_recovered.csv",
    "records_clean.csv",
    "crawl_by_doi.csv",
    "engagement.csv",
    "subjects.csv",
    "reasons.csv",
    "authors.csv",
    "countries.csv",
]

# A second archive rather than a bigger first one. All of this was collected
# weeks ago, counted in a status update, and never shipped - so nobody outside
# this machine could check any of it - but folding it into the main zip takes
# that file from 55 MB to 81 MB, and someone who wants the master table should
# not have to download 1.4 million posts to get it. Same rule as loose vs
# zipped, one level up.
ZIPPED_EXTRA = [
    # The Stage 3 input: 1.4 million dated mentions, each carrying how many
    # days it falls before or after the retraction.
    "altmetric_posts.csv.gz",
    "altmetric_detail.csv",
    "oa_locations.csv",
    "oa_all_locations.csv",
    "archive_checks.csv",
    # Every page we do not have, why, and what could still reach it. A gap of
    # this size is a finding, not an omission, and it belongs in the shared
    # data rather than in six separate queries.
    "not_collected.csv",
]

ARCHIVE = "cs43_all_datasets.zip"
ARCHIVE_EXTRA = "cs43_attention_and_access.zip"

# Documentation is an input, not something that happens to be lying in the
# output folder. The README was hand-placed and survived every rebuild, so a
# stale figure could outlive the data it described - and did: it carried a
# withdrawn citation claim for days after the numbers changed.
DOCS = {
    "README.md": "docs/share-README.md",
    "data_quality.md": "docs/share-data-quality.md",
}


def main() -> int:
    SHARE.mkdir(parents=True, exist_ok=True)

    # Remove what this script produced last time. Anything left behind is a
    # file nobody can regenerate, and a share folder that cannot be rebuilt
    # from scratch is not reproducible - it is a pile of artefacts.
    expected = set(LOOSE) | set(DOCS) | {ARCHIVE, ARCHIVE_EXTRA, "MANIFEST.txt"}
    for f in SHARE.iterdir():
        if f.is_file() and f.name in expected:
            f.unlink()
    stale = [f.name for f in SHARE.iterdir() if f.is_file()]
    if stale:
        console.print(f"[yellow]not produced by this script, left alone: "
                      f"{', '.join(stale)}[/yellow]")
    missing = [f for f in LOOSE + ZIPPED + ZIPPED_EXTRA
               if not (CLEAN / f).exists()]
    if missing:
        console.print(f"[red]missing from data/clean: {', '.join(missing)}[/red]")
        console.print("[yellow]run clean_data.py / export_tables.py / "
                      "export_master.py first[/yellow]")
        return 1

    for name in LOOSE:
        src = CLEAN / name
        if name == "dtypes.json" and not src.exists():
            continue
        shutil.copy2(src, SHARE / name)
        console.print(f"  loose  {name:<28} {src.stat().st_size/1e6:>7.1f} MB")

    for name, src_rel in DOCS.items():
        src = config.ROOT / src_rel
        if src.exists():
            shutil.copy2(src, SHARE / name)
            console.print(f"  doc    {name:<28} from {src_rel}")
        else:
            console.print(f"  [red]missing doc source: {src_rel}[/red]")

    for archive, members in ((ARCHIVE, ZIPPED), (ARCHIVE_EXTRA, ZIPPED_EXTRA)):
        out = SHARE / archive
        raw = 0
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED,
                             compresslevel=9) as z:
            for name in members:
                src = CLEAN / name
                raw += src.stat().st_size
                z.write(src, name)
                console.print(f"  zip    {name:<28} "
                              f"{src.stat().st_size/1e6:>7.1f} MB")
        console.print(f"  [bold]{out.name}[/bold]  "
                      f"{out.stat().st_size/1e6:.0f} MB  "
                      f"(from {raw/1e6:.0f} MB raw)")

    # A manifest, so a teammate can tell whether their copy matches ours and
    # when it was built. Without it, "which version of the data did you use"
    # has no answer.
    import hashlib
    from datetime import datetime, timezone
    lines = [f"CS-43 share package",
             f"built {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
             ""]
    for f in sorted(SHARE.iterdir()):
        if not f.is_file() or f.name == "MANIFEST.txt":
            continue
        h = hashlib.sha256(f.read_bytes()).hexdigest()[:16]
        lines.append(f"{h}  {f.stat().st_size:>12,}  {f.name}")
    (SHARE / "MANIFEST.txt").write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
    console.print("[dim]MANIFEST.txt written[/dim]")

    # A file over GitHub's 100 MB limit cannot be pushed at all, and finding
    # that out during the push is a bad time to find it out.
    for f in sorted(SHARE.iterdir()):
        if f.is_file() and f.stat().st_size > 100_000_000:
            console.print(f"[red]{f.name} exceeds GitHub's 100 MB limit[/red]")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
