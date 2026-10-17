"""Stage 1a - render the frontier and archive each page as gzipped MHTML.

    python scripts/crawl.py --limit 100                       # smoke test
    python scripts/crawl.py --workers 8 --max-attempts 1      # full first pass
    python scripts/crawl.py --prefix 10.1016 10.1002 10.1371  # one publisher set
    python scripts/crawl.py --retry-only                      # re-attempt failures

Safe to stop at any time (Ctrl+C) and safe to re-run: progress lives in the
SQLite frontier, so a restart resumes and never re-fetches a completed page.

Pacing is per publisher *host* and adaptive - see rwpipe.limiter for why both
of those matter.
"""
from __future__ import annotations

import argparse
import asyncio
import signal
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playwright.async_api import async_playwright
from rich.console import Console

from rwpipe import config, db, storage
from rwpipe.fetch import fetch_page
from rwpipe.limiter import Breaker, HostMap, Limiter

console = Console()
stopping = asyncio.Event()

# Hosts measured to answer WebKit where they refuse Chromium. Restricted to
# those with a rate worth the extra request: a sweep of 18 blocked hosts x 8
# DOIs found MDPI at 8/8 and cell.com at 4/8, while eleven others sat at 1/8 -
# one page for eight requests, each failure deepening the block. IOP, EDP and
# Cureus refuse both engines at 0/8.
WEBKIT_HOSTS = {"www.mdpi.com", "cell.com", "www.cell.com"}


class Frontier:
    """Hands out work. Serialised through one asyncio lock (single process)."""

    def __init__(self, conn, prefixes, limit, retry_only=False, max_attempts=None,
                 shard=None):
        self.shard = shard          # (index, total) or None
        self.conn = conn
        self.prefix_rank = self._rank_prefixes(conn)
        self.prefixes = prefixes
        self.remaining = limit
        self.retry_only = retry_only
        self.max_attempts = max_attempts or config.MAX_ATTEMPTS
        self._lock = asyncio.Lock()

    def _statuses(self) -> str:
        return "('failed')" if self.retry_only else "('pending','failed')"

    async def claim(self, busy_prefixes: set[str]):
        """Take one page, avoiding any publisher a worker is already on."""
        async with self._lock:
            if self.remaining is not None and self.remaining <= 0:
                return None
            # The title and role travel with the claim: fetch_page cannot verify a
            # capture is the right paper without knowing what the right paper is
            # called, and cannot judge a notice without knowing one was asked for.
            sql = ("SELECT doi, doi_prefix, attempts, final_host, "
                   " COALESCE((SELECT r.title FROM records r WHERE r.original_doi = documents.doi),"
                   "          (SELECT r.title FROM records r WHERE r.retraction_doi = documents.doi)) AS rec_title, "
                   " COALESCE((SELECT r.journal FROM records r WHERE r.original_doi = documents.doi),"
                   "          (SELECT r.journal FROM records r WHERE r.retraction_doi = documents.doi)) AS rec_journal, "
                   " CASE WHEN EXISTS(SELECT 1 FROM records r WHERE r.retraction_doi = documents.doi) "
                   "      THEN 'retraction' ELSE 'original' END AS doc_role "
                   "FROM documents "
                   f"WHERE status IN {self._statuses()} AND attempts < ?")
            args = [self.max_attempts]
            if self.prefixes:
                sql += " AND doi_prefix IN (%s)" % ",".join("?" * len(self.prefixes))
                args += list(self.prefixes)
            if self.shard:
                sql += " AND shard % ? = ?"
                args += [self.shard[1], self.shard[0]]
            if busy_prefixes:
                sql += " AND doi_prefix NOT IN (%s)" % ",".join("?" * len(busy_prefixes))
                args += sorted(busy_prefixes)
            # Least-tried first, then most-likely-to-succeed. Without the
            # second key, requeued pages from dead publishers (attempts reset
            # to 0) crowd to the front and hold every worker through a 90 s
            # backoff each, while thousands of pages from a publisher that
            # answers every request wait behind them. Observed directly: 8,075
            # ScienceDirect pages at 100% success sat untouched for a quarter
            # of an hour while all eight workers ground through cell.com and
            # jbc.org at 0%.
            sql += (" ORDER BY attempts, "
                    "COALESCE("
                    "  (SELECT rank FROM host_rank hr "
                    "     WHERE hr.final_host = documents.final_host), "
                    "  (SELECT rank FROM prefix_rank pr "
                    "     WHERE pr.doi_prefix = documents.doi_prefix), "
                    "  500), "
                    # Shard, not doi. Alphabetical order clusters a publisher's
                    # imprints together - every 10.1016/j.cell* DOI sits in one
                    # run - so the crawler grinds through an entire dead imprint
                    # before reaching the working one behind it. An Elsevier-only
                    # pass spent 12 of 13 requests on cell.com for exactly this
                    # reason. Shard is a stable hash, so the queue is spread
                    # across imprints while staying reproducible.
                    "shard LIMIT 1")
            row = self.conn.execute(sql, args).fetchone()
            if row is None:
                return None
            # Stamp the claim, not just the completion. fetched_at is written
            # when a page finishes, so a row whose worker hangs keeps whatever
            # timestamp it had before - one page sat in 'running' since 27
            # August and nothing could tell how long, because the only clock on
            # the row belonged to an event that never happened.
            self.conn.execute(
                "UPDATE documents SET status='running', attempts=attempts+1, "
                "claimed_at=datetime('now') WHERE doi=?", (row["doi"],))
            self.conn.commit()
            if self.remaining is not None:
                self.remaining -= 1
            return dict(row)

    @staticmethod
    def _rank_prefixes(conn) -> int:
        """Rank each DOI prefix by how often its host has actually answered.

        Materialised into a temp table rather than joined inline: the claim
        query runs once per page, and recomputing this aggregate 100,000 times
        would cost more than the scheduling it pays for.
        """
        conn.execute("DROP TABLE IF EXISTS prefix_rank")
        conn.execute("CREATE TEMP TABLE prefix_rank (doi_prefix TEXT PRIMARY KEY, rank INTEGER)")
        conn.execute(
            """INSERT INTO prefix_rank (doi_prefix, rank)
               SELECT doi_prefix,
                      CAST(100 - 100.0 * SUM(status='done')
                           / NULLIF(SUM(status IN ('done','blocked','failed')), 0)
                           AS INTEGER)
               FROM documents
               GROUP BY doi_prefix
               HAVING SUM(status IN ('done','blocked','failed')) >= 10"""
        )

        # Prefix is too coarse on its own. 10.1016 fans out to sciencedirect.com
        # at 100%, ovid.com, linkinghub, and cell.com at 0% - one rank for all
        # four means the workable majority queues behind the hopeless minority.
        # A page attempted before already records where it landed, so rank those
        # by their own host and fall back to the prefix only for pages never
        # tried. Measured: 12 of 12 pending Elsevier DOIs sampled at random
        # succeed, while the crawler was spending its time on the cell.com slice
        # of the same prefix.
        conn.execute("DROP TABLE IF EXISTS host_rank")
        conn.execute("CREATE TEMP TABLE host_rank (final_host TEXT PRIMARY KEY, rank INTEGER)")
        conn.execute(
            """INSERT INTO host_rank (final_host, rank)
               SELECT final_host,
                      CAST(100 - 100.0 * SUM(status='done')
                           / NULLIF(SUM(status IN ('done','blocked','failed')), 0)
                           AS INTEGER)
               FROM documents
               WHERE final_host IS NOT NULL AND final_host <> ''
               GROUP BY final_host
               HAVING SUM(status IN ('done','blocked','failed')) >= 10"""
        )
        conn.commit()
        return conn.execute("SELECT COUNT(*) FROM prefix_rank").fetchone()[0]

    async def anything_left(self) -> bool:
        async with self._lock:
            if self.remaining is not None and self.remaining <= 0:
                return False
            sql = (f"SELECT 1 FROM documents WHERE status IN {self._statuses()} "
                   "AND attempts < ?")
            args = [self.max_attempts]
            if self.prefixes:
                sql += " AND doi_prefix IN (%s)" % ",".join("?" * len(self.prefixes))
                args += list(self.prefixes)
            if self.shard:
                sql += " AND shard % ? = ?"
                args += [self.shard[1], self.shard[0]]
            return self.conn.execute(sql + " LIMIT 1", args).fetchone() is not None


async def new_context(browser, spoof_ua: bool = True):
    """A fresh browsing context.

    WebKit gets its own real user agent rather than the Chrome string. The
    point of using WebKit is that it *is* a different browser, down to the TLS
    handshake; advertising Chrome from a WebKit stack would be the one thing
    here that genuinely is impersonation, and an obvious inconsistency for a
    bot-management product to notice.
    """
    kwargs = {"viewport": {"width": 1440, "height": 900},
              "locale": "en-AU", "ignore_https_errors": True}
    if spoof_ua:
        kwargs["user_agent"] = config.USER_AGENT
    return await browser.new_context(**kwargs)



def recover_stale(conn, minutes: int = 15, shard=None) -> int:
    """Put rows back that no live worker is still holding.

    A page is claimed, its worker hangs, and the row stays 'running' forever -
    invisible to the frontier, never retried, never counted as lost. One page
    sat in that state from 27 August onward. Startup already resets these, but
    a run lasting days needs the check *during* the run as well.
    """
    sql = ("UPDATE documents SET status='pending' WHERE status='running' "
           "AND (claimed_at IS NULL OR claimed_at < datetime('now', ?))")
    args = [f"-{int(minutes)} minutes"]
    if shard:
        sql += " AND shard % ? = ?"
        args += [shard[1], shard[0]]
    cur = conn.execute(sql, args)
    conn.commit()
    return cur.rowcount


async def worker(browser, frontier: Frontier, lim: Limiter, hosts: HostMap,
                 brk: Breaker, stats: Counter, recycle_every: int = 500,
                 webkit=None, wk_brk: Breaker | None = None) -> None:
    ctx = await new_context(browser)
    wk_ctx = await new_context(webkit, spoof_ua=False) if webkit else None
    since_recycle = 0
    try:
        while not stopping.is_set():
            task = await frontier.claim(hosts.prefixes_for(lim.busy))
            if task is None:
                # Either the run is finished, or every remaining publisher is
                # currently held by another worker. Only the first is a reason
                # to exit.
                if not await frontier.anything_left():
                    return
                await asyncio.sleep(1.0)
                continue

            # A context held for tens of thousands of pages accumulates memory
            # and cookie state; recycling keeps a multi-day run flat.
            if since_recycle >= recycle_every:
                await ctx.close()
                ctx = await new_context(browser)
                since_recycle = 0
            since_recycle += 1

            prefix, doi = task["doi_prefix"], task["doi"]
            attempts = task["attempts"] + 1          # this attempt
            key = hosts.key(prefix)                  # host when known, else prefix

            # A retry knows something the first attempt did not: where the
            # page actually landed last time. The pre-check below is keyed on
            # the host *guessed* from the DOI prefix, which for 10.1016 is
            # sciencedirect.com - so a page that resolved to cell.com on its
            # first attempt sails through the guard and spends another full
            # request proving what is already known. Measured: 459 rows queued
            # for two further attempts each against a host standing at 0
            # successes in 1,552 tries.
            #
            # Only rows that carry a result are treated this way. Requeued
            # pages have their host cleared, so this cannot repeat the earlier
            # mistake where jbc.org's 837 requeued pages were all archived
            # without a single request being made.
            landed_before = task.get("final_host")
            if landed_before and brk.is_dead(landed_before):
                frontier.conn.execute(
                    """UPDATE documents SET status='blocked',
                           error=?, fetched_at=datetime('now') WHERE doi=?""",
                    (f"circuit breaker: previous attempt landed on "
                     f"{landed_before}, unreachable "
                     f"({brk._tried.get(landed_before, 0)} attempts, 0 ok)",
                     doi))
                frontier.conn.commit()
                stats["skipped_dead"] += 1
                continue

            # Both engines exhausted for this host: archive without a request.
            if brk.is_dead(key) and (wk_ctx is None or (wk_brk and wk_brk.is_dead(key))):
                frontier.conn.execute(
                    """UPDATE documents SET status='blocked', final_host=?,
                           error=?, fetched_at=datetime('now') WHERE doi=?""",
                    (key, f"circuit breaker: {key} unreachable by chromium "
                          f"({brk._tried.get(key, 0)} attempts)"
                          + (" and webkit" if wk_ctx is not None else ""), doi))
                frontier.conn.commit()
                stats["skipped_dead"] += 1
                continue

            use_webkit = False
            lim.busy.add(key)
            try:
                async with lim.lock(key):
                    await lim.wait_turn(key)
                    res, mhtml = await fetch_page(
                        ctx, doi,
                        expect_title=task.get("rec_title"),
                        expect_journal=task.get("rec_journal"),
                        doc_role=task.get("doc_role", "original"))

                    # Chromium was refused. Try the other engine on the same
                    # page before writing it off: measured over 18 samples,
                    # WebKit reaches MDPI 18/18 where Chromium reaches 0 of
                    # 1,036. Bot-management products are tuned hardest against
                    # headless Chromium, because that is what nearly every
                    # crawler uses.
                    #
                    # Retrying per page rather than per host matters: the host
                    # verdict is cleared whenever pages are requeued, so keying
                    # the fallback on it meant a host released for retesting
                    # went straight back to the engine that could not reach it.
                    # Judged on the host the request landed on as well as the
                    # routing key: on a fresh database no prefix->host route is
                    # learned yet, the key is the bare prefix ("10.3390"), and
                    # the first MDPI page never reached WebKit (the handover
                    # test, 2026-09-18).
                    if (not res.ok and wk_ctx is not None
                            and (key in WEBKIT_HOSTS or (res.final_host or "") in WEBKIT_HOSTS)
                            and not (wk_brk and wk_brk.is_dead(key))):
                        await lim.wait_turn(key)
                        wk_res, wk_mhtml = await fetch_page(
                            wk_ctx, doi, capture="html",
                            expect_title=task.get("rec_title"),
                            expect_journal=task.get("rec_journal"),
                            doc_role=task.get("doc_role", "original"))
                        stats["webkit_try"] += 1
                        if wk_res.ok:
                            stats["webkit_won"] += 1
                            res, mhtml, use_webkit = wk_res, wk_mhtml, True
                        elif wk_brk is not None:
                            wk_brk.record(wk_res.final_host or key, False)
            finally:
                lim.busy.discard(key)

            hosts.learn(prefix, res.final_host)
            # Charge the outcome to where the request actually landed, not to
            # the host we guessed from the prefix. One prefix can fan out to
            # several sites - 10.1016 reaches sciencedirect.com,
            # heartlungcirc.org, linkinghub and cell.com - and booking cell.com's
            # refusals against the shared key dragged ScienceDirect to the 90 s
            # ceiling despite 4,511 successes and not one failure. That put
            # 7,931 pages from a publisher answering every request behind a
            # penalty earned entirely by a different site.
            landed = res.final_host or key
            lim.record(landed, res.blocked)
            if landed != key:
                # keep the guessed key roughly in step, so the next page for
                # this prefix is not paced from a stale delay
                lim._delay[key] = lim.delay(landed)
            (wk_brk if use_webkit else brk).record(res.final_host, res.ok)

            path_str, size, gz = None, None, None
            if res.ok and mhtml:
                path, size, gz = storage.write_capture(
                    doi, prefix, mhtml, res.capture_format or "mhtml")
                path_str = storage.rel(path)
                status = "done"
            elif attempts >= frontier.max_attempts:
                # out of retries - park it, with the reason preserved
                status = "blocked" if res.blocked else "failed"
            else:
                status = "failed"                    # eligible for another pass

            frontier.conn.execute(
                """UPDATE documents SET status=?, final_url=?, final_host=?,
                       http_status=?, mhtml_path=?, mhtml_bytes=?, gz_bytes=?,
                       capture_format=?, elapsed_ms=?, error=?,
                       fetched_at=datetime('now')
                   WHERE doi=?""",
                (status, res.final_url, res.final_host, res.http_status,
                 path_str, size, gz, res.capture_format, res.elapsed_ms,
                 res.error or res.block_reason, doi),
            )
            frontier.conn.commit()

            stats[status] += 1
            stats["_bytes"] += size or 0
            stats["_gz"] += gz or 0
            n = sum(v for k, v in stats.items() if not k.startswith("_"))
            if n % 200 == 0:
                # Sweep up rows whose worker never came back. Cheap, and the
                # alternative is a page silently lost for the rest of the run.
                freed = recover_stale(frontier.conn, shard=frontier.shard)
                if freed:
                    console.print(f"[yellow]recovered {freed} stalled page(s) "
                                  f"back to pending[/yellow]")
            if n % 25 == 0:
                pace = ", ".join(f"{k.split('.')[0][:16]}={d:.1f}s"
                                 for k, d in lim.snapshot(3))
                console.print(
                    f"[dim]{n:>6}[/dim] done={stats['done']} "
                    f"blocked={stats['blocked']} dead={stats['skipped_dead']} "
                    f"| {stats['_gz'] / 1e9:.2f} GB "
                    f"| {stats['done'] / n * 100:.0f}% | slowest: {pace}"
                )
    finally:
        await ctx.close()
        if wk_ctx is not None:
            await wk_ctx.close()


async def run(workers, prefixes, limit, delay, headless, retry_only=False,
              max_attempts=None, recycle_every=500, floor=1.0, shard=None,
              breaker_after=40, webkit_fallback=True):
    conn = db.connect()
    db.init(conn)
    db.sync_documents(conn)
    reset_sql = "UPDATE documents SET status='pending' WHERE status='running'"
    reset_args = []
    if shard:
        reset_sql += " AND shard % ? = ?"
        reset_args = [shard[1], shard[0]]
    cur = conn.execute(reset_sql, reset_args)
    conn.commit()
    if cur.rowcount:
        console.print(f"[yellow]recovered {cur.rowcount} interrupted pages[/yellow]")

    frontier = Frontier(conn, prefixes, limit, retry_only, max_attempts, shard)
    hosts = HostMap.from_db(conn)
    brk = Breaker.from_db(conn, min_attempts=breaker_after)
    lim = Limiter(base=delay, floor=floor)
    dead = brk.dead_hosts()
    console.print(f"[dim]learned prefix->host routes: {len(hosts._map)}; "
                  f"scheduling by success rate over {frontier.prefix_rank} prefixes[/dim]")
    if dead:
        console.print(f"[yellow]circuit breaker open on {len(dead)} hosts "
                      f"({sum(n for _, n in dead):,} failed attempts already spent):[/yellow]")
        for h, n in dead[:8]:
            console.print(f"    {h[:40]:<42} {n:>6,} tries, 0 ok")
    stats: Counter = Counter()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        webkit = None
        wk_brk = None
        if webkit_fallback:
            try:
                webkit = await pw.webkit.launch(headless=headless)
                # WebKit keeps its own record: a host Chromium cannot reach is
                # no evidence about WebKit, and the reverse holds too.
                wk_brk = Breaker(min_attempts=breaker_after, parole_every=50)
                console.print("[dim]webkit fallback armed for chromium-blocked hosts[/dim]")
            except Exception as exc:                      # noqa: BLE001
                console.print(f"[yellow]webkit unavailable ({type(exc).__name__}), "
                              f"chromium only[/yellow]")
        try:
            await asyncio.gather(
                *(worker(browser, frontier, lim, hosts, brk, stats, recycle_every,
                         webkit, wk_brk)
                  for _ in range(workers))
            )
        finally:
            await browser.close()
            if webkit is not None:
                await webkit.close()

    conn.execute(reset_sql, reset_args)
    conn.commit()
    conn.close()
    return stats, lim


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=None, help="stop after N pages")
    ap.add_argument("--prefix", nargs="*", default=None, help="only these DOI prefixes")
    ap.add_argument("--delay", type=float, default=config.DEFAULT_HOST_DELAY,
                    help="starting seconds between hits on one host")
    ap.add_argument("--floor", type=float, default=1.0,
                    help="fastest the adaptive limiter may go, per host")
    ap.add_argument("--max-attempts", type=int, default=None,
                    help="cap attempts per page; 1 = single pass, no retries")
    ap.add_argument("--recycle-every", type=int, default=500,
                    help="rebuild each browser context after N pages")
    ap.add_argument("--retry-only", action="store_true",
                    help="re-attempt pages that already failed, nothing new")
    ap.add_argument("--no-webkit", action="store_true",
                    help="disable the WebKit fallback for chromium-blocked hosts")
    ap.add_argument("--breaker-after", type=int, default=40,
                    help="give up on a host after N attempts with zero successes")
    ap.add_argument("--shard", default=None, metavar="I/N",
                    help="take only this slice of the work, e.g. 3/8 for "
                         "member 3 of an 8-person split")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    shard = None
    if args.shard:
        i, n = (int(x) for x in args.shard.split('/'))
        if not 0 <= i < n:
            ap.error('--shard I/N needs 0 <= I < N')
        shard = (i, n)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _stop(*_):
        console.print("\n[yellow]stopping - finishing in-flight pages[/yellow]")
        loop.call_soon_threadsafe(stopping.set)

    signal.signal(signal.SIGINT, _stop)

    started = time.time()
    console.print(
        f"[bold]crawling[/bold] workers={args.workers} start-delay={args.delay}s "
        f"floor={args.floor}s limit={args.limit or 'all'} "
        f"prefixes={args.prefix or 'all'}\n"
    )
    stats, lim = loop.run_until_complete(
        run(args.workers, args.prefix, args.limit, args.delay,
            not args.headed, args.retry_only, args.max_attempts,
            args.recycle_every, args.floor, shard, args.breaker_after,
            not args.no_webkit))

    elapsed = time.time() - started
    n = sum(v for k, v in stats.items() if not k.startswith("_"))
    console.print(f"\n[bold]run finished in {elapsed / 60:.1f} min[/bold]")
    if n:
        console.print(f"  pages attempted  : {n}")
        console.print(f"  done             : {stats['done']} ({stats['done'] / n * 100:.0f}%)")
        console.print(f"  failed (retry)   : {stats['failed']}")
        console.print(f"  blocked (gave up): {stats['blocked']}")
        console.print(f"  skipped (dead host): {stats['skipped_dead']}")
        if stats["webkit_try"]:
            console.print(f"  webkit fallback    : {stats['webkit_won']}"
                          f"/{stats['webkit_try']} recovered from "
                          f"chromium-blocked hosts")
        console.print(f"  captured         : {stats['_bytes'] / 1e9:.2f} GB "
                      f"-> {stats['_gz'] / 1e9:.2f} GB on disk")
        console.print(f"  rate             : {n / elapsed * 3600:.0f} pages/hour")
        if stats["done"]:
            console.print(f"  avg page         : {stats['_bytes'] / stats['done'] / 1e6:.2f} MB "
                          f"({stats['_gz'] / stats['_bytes'] * 100:.0f}% after gzip)")
        console.print("\n[bold]where the limiter settled[/bold]")
        for host, d in lim.snapshot(10):
            console.print(f"  {d:>6.1f}s  {host}")

    conn = db.connect()
    console.print("\n[bold]frontier[/bold]")
    for s, c in sorted(db.counts_by_status(conn).items()):
        console.print(f"  {s:<8} {c:>8,}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
