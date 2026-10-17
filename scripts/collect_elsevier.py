"""Collect the Elsevier articles through the API they have now entitled us to.

    python scripts/collect_elsevier.py --limit 20     # sample first
    python scripts/collect_elsevier.py                # all of them

The largest single gap in the project, and the one that was never a technical
problem. 9,185 documents returned HTTP 200 with a full-size page containing
"There was a problem providing the content you requested" instead of the
article - Elsevier's own block page. Elsevier diagnosed it themselves: the
Article Retrieval API was disabled on the key we were issued.

On 2026-09-06 they approved TDM access for 13,785 records and issued an
institutional token, which is what makes this work off campus.

**The token is a credential, not a setting.** Elsevier's terms are explicit and
this script is written to them: it lives only in .env, which is gitignored and
has never been committed; it is sent as a header over HTTPS and never as a
query parameter, so it cannot appear in a URL, a log line or an address bar;
and it is never written to the database, the share bundle or any file this
project ships. Elsevier can revoke it without notice, so a run that starts
returning 401 stops rather than retrying.

Quota is "no fixed limit but subject to ongoing review, keep your request rate
reasonable", so the default is one request per second. That is 9,185 documents
in about two and a half hours, which is slow enough to be uncontroversial and
fast enough to finish tonight.

What comes back is Elsevier's own XML in one of two shapes - `<ce:sections>`
with structured paragraphs, or `<xocs:rawtext>` as a plain dump - both inside
`<originalText>`. A response that carries the DOI but no full-text container is
metadata only, and is recorded as such rather than counted as a success: an
abstract that parses cleanly is exactly the failure this project has been
bitten by before.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx
from rich.console import Console

from rwpipe import config, db, fetch, storage

console = Console()

ENDPOINT = "https://api.elsevier.com/content/article/doi/"
UA = f"COMP5703-CS43 student project (mailto:{config.POLITE_CONTACT})"

# The full-text containers. Either is enough; neither means metadata only.
FULLTEXT = ("<originalText", "<xocs:rawtext", "<ce:para")


class Revoked(Exception):
    """401/403 from Elsevier. The token can be withdrawn without notice, and
    hammering the endpoint after that is the wrong response to it."""


def credentials() -> tuple[str, str]:
    env = Path(__file__).resolve().parents[1] / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    key = os.environ.get("ELSEVIER_API_KEY")
    tok = os.environ.get("ELSEVIER_INSTTOKEN")
    if not key:
        raise SystemExit("ELSEVIER_API_KEY missing from .env")
    if not tok:
        console.print("[yellow]no ELSEVIER_INSTTOKEN - on-campus access only"
                      "[/yellow]")
    return key, tok or ""


def targets(conn, limit):
    sql = """SELECT doi, doi_prefix FROM documents
             WHERE usable IS NOT 1
               AND (error LIKE 'soft refusal%'
                    OR final_host IN ('www.sciencedirect.com',
                                      'linkinghub.elsevier.com', 'cell.com'))
             ORDER BY doi"""
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--rate", type=float, default=1.0,
                    help="seconds between requests")
    args = ap.parse_args()

    key, tok = credentials()
    headers = {"X-ELS-APIKey": key, "User-Agent": UA}
    if tok:
        headers["X-ELS-Insttoken"] = tok

    conn = db.connect()
    db.init(conn)
    rows = targets(conn, args.limit)
    if not rows:
        console.print("[green]nothing left for the Elsevier API[/green]")
        return 0

    console.print(f"[bold]{len(rows):,} documents[/bold] at {args.rate}s apart "
                  f"— about {len(rows) * args.rate / 3600:.1f} h\n")

    got = meta = missing = err = 0
    started = time.time()
    with httpx.Client(timeout=90.0, follow_redirects=True,
                      headers=headers) as cl:
        for n, (doi, prefix) in enumerate(rows, 1):
            try:
                r = cl.get(ENDPOINT + doi, params={"httpAccept": "text/xml"})
            except Exception as exc:                        # noqa: BLE001
                err += 1
                conn.execute("UPDATE documents SET error=?, attempts=attempts+1 "
                             "WHERE doi=?", (f"els: {type(exc).__name__}", doi))
                time.sleep(3)
                continue

            if r.status_code in (401, 403):
                conn.commit()
                raise Revoked(f"HTTP {r.status_code} — the entitlement has "
                              f"changed. Stopping rather than retrying.")
            if r.status_code == 404:
                missing += 1
                conn.execute("UPDATE documents SET error=?, attempts=attempts+1 "
                             "WHERE doi=?", ("els: not in Elsevier's index", doi))
            elif r.status_code != 200:
                err += 1
                conn.execute("UPDATE documents SET error=?, attempts=attempts+1 "
                             "WHERE doi=?", (f"els: http {r.status_code}", doi))
            else:
                xml = r.text
                if not fetch.contains_doi(xml, doi):
                    err += 1
                    conn.execute(
                        "UPDATE documents SET error=?, attempts=attempts+1 "
                        "WHERE doi=?", ("els: response is a different record", doi))
                elif not any(t in xml for t in FULLTEXT):
                    # Metadata and an abstract. Real, but not the article, and
                    # counting it as one would repeat the mistake that filed
                    # 10,265 refusal pages as successes.
                    meta += 1
                    conn.execute(
                        "UPDATE documents SET error=?, attempts=attempts+1 "
                        "WHERE doi=?", ("els: metadata only, no full text", doi))
                else:
                    path, raw_n, gz_n = storage.write_capture(
                        doi, prefix, xml, fmt="elsxml")
                    conn.execute(
                        """UPDATE documents
                              SET status='done', usable=1, http_status=200,
                                  mhtml_path=?, mhtml_bytes=?, gz_bytes=?,
                                  capture_format='elsxml',
                                  source_kind='elsevier-api',
                                  final_host='api.elsevier.com',
                                  final_url=?, error=NULL,
                                  attempts=attempts+1, fetched_at=datetime('now')
                            WHERE doi=?""",
                        (storage.rel(path), raw_n, gz_n,
                         ENDPOINT + doi, doi))
                    got += 1

            if n % 25 == 0:
                conn.commit()
            if n % 200 == 0:
                el = max(time.time() - started, 1)
                console.print(f"  [dim]{n:>6,}/{len(rows):,}[/dim]  "
                              f"full text {got:,}  metadata only {meta:,}  "
                              f"not indexed {missing:,}  error {err:,}  "
                              f"| {n / el * 3600:,.0f}/h")
            time.sleep(args.rate)

    conn.commit()
    console.print(f"\n[bold]{len(rows):,} requested[/bold]")
    console.print(f"  full text recovered   {got:>7,}")
    console.print(f"  metadata only         {meta:>7,}")
    console.print(f"  not in their index    {missing:>7,}")
    console.print(f"  errors                {err:>7,}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Revoked as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(2)
