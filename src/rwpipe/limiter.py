"""Adaptive, host-aware rate limiting.

Two problems this fixes, both found by measuring the live crawl:

1. **Rate limiting was keyed on the DOI prefix, but publishers serve many
   prefixes from one host.** `link.springer.com` alone serves 5 prefixes, so
   five workers could hit Springer concurrently, each believing it was honouring
   a 5 s gap while the site actually received one request per second. That is
   almost certainly why Springer blocked us so often: we were speeding, not
   merely unwelcome.

2. **A fixed delay is wrong in both directions.** Elsevier and PLOS answered
   every request; holding them to 5 s wastes hours. Springer and SAGE blocked
   at 5 s; holding them there keeps failing. So the delay adapts per host,
   AIMD-style (the same shape as TCP congestion control): ease off gently while
   things work, back off hard the moment a block appears.

The host is not known until a DOI has been resolved once, so the prefix->host
map is learned from previously fetched documents and extended as the run goes.
"""
from __future__ import annotations

import asyncio
import time

# The DOI resolver is not a publisher, and must never be treated as one.
#
# A DOI that never gets past doi.org records `doi.org` as its final host. That
# host can therefore only ever accumulate failures - a successful fetch has by
# definition redirected away from it - so the breaker condemned it after 40
# tries and then archived, without a request, every DOI whose resolution
# stalled there. 1,135 pages across 53 prefixes were written off this way, none
# of them evidence about any publisher: a dead resolver hit is a fact about one
# DOI, not about the site behind it.
#
# The same reasoning applies to the prefix->host map. Learning `doi.org` as a
# prefix's host hands that entire prefix to the condemned key on the next
# startup, which is how 10.26355 (575 pages) and 10.4314 (408) were lost.
RESOLVER_HOSTS = {"doi.org", "dx.doi.org", "www.doi.org"}


class HostMap:
    """Learned DOI-prefix -> publisher-host mapping."""

    def __init__(self, initial: dict[str, str] | None = None):
        self._map: dict[str, str] = dict(initial or {})

    @classmethod
    def from_db(cls, conn) -> "HostMap":
        """Seed from hosts already observed, taking the majority per prefix."""
        rows = conn.execute(
            """SELECT doi_prefix, final_host, COUNT(*) n FROM documents
               WHERE final_host IS NOT NULL AND final_host <> ''
               GROUP BY doi_prefix, final_host ORDER BY doi_prefix, n DESC"""
        ).fetchall()
        best: dict[str, str] = {}
        for r in rows:
            if r["final_host"] in RESOLVER_HOSTS:
                continue
            best.setdefault(r["doi_prefix"], r["final_host"])
        return cls(best)

    def key(self, prefix: str) -> str:
        """The limiting key: the host when known, else the prefix itself."""
        return self._map.get(prefix, prefix)

    def learn(self, prefix: str, host: str | None) -> None:
        if host and host not in RESOLVER_HOSTS:
            self._map.setdefault(prefix, host)

    def prefixes_for(self, keys: set[str]) -> set[str]:
        """Which prefixes route to any of these busy keys.

        Includes the keys themselves: a prefix whose host is not yet known acts
        as its own key, so it must be excluded too. Keys that are hostnames
        simply never match a doi_prefix, which is harmless.
        """
        return {p for p, h in self._map.items() if h in keys} | set(keys)


class Limiter:
    """One lock and one adaptive delay per host."""

    def __init__(self, base: float = 5.0, floor: float = 1.0,
                 ceiling: float = 90.0, ease: float = 0.85, backoff: float = 2.0):
        self.base, self.floor, self.ceiling = base, floor, ceiling
        # `ease` is a MULTIPLIER, not a subtraction. An additive recovery is
        # catastrophically asymmetric against a doubling backoff: from the 90s
        # ceiling, shaving 0.25s per success needs 356 successes to get back to
        # 1s - and each of those successes costs a 90s wait, so recovery alone
        # takes ~9 hours. Springer sat at the ceiling with a 94% success rate
        # and 13,394 pages left, which projected to 335 hours for one publisher.
        # Multiplying by 0.85 recovers the same distance in ~20 successes.
        self.ease, self.backoff = ease, backoff
        self._locks: dict[str, asyncio.Lock] = {}
        self._delay: dict[str, float] = {}
        self._next_ok: dict[str, float] = {}
        self.busy: set[str] = set()

    def delay(self, key: str) -> float:
        return self._delay.setdefault(key, self.base)

    def lock(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    async def wait_turn(self, key: str) -> None:
        due = self._next_ok.get(key, 0.0)
        now = time.monotonic()
        if due > now:
            await asyncio.sleep(due - now)
        self._next_ok[key] = time.monotonic() + self.delay(key)

    def record(self, key: str, blocked: bool) -> None:
        """AIMD: ease off gradually on success, back off hard on a block."""
        d = self.delay(key)
        self._delay[key] = (
            min(self.ceiling, d * self.backoff) if blocked
            else max(self.floor, d * self.ease)
        )

    def snapshot(self, top: int = 8) -> list[tuple[str, float]]:
        """Current delays, slowest first - for progress reporting."""
        return sorted(self._delay.items(), key=lambda kv: -kv[1])[:top]


class Breaker:
    """Stop knocking on doors that never open.

    The adaptive limiter slows down under refusal but never gives up, so a host
    that refuses everything still consumes a request every `ceiling` seconds
    forever. Measured on the live crawl: 4,855 attempts across eight hosts with
    zero successes between them, at up to 90s each, with 30,000+ more pages
    queued behind the same walls - hundreds of hours spent confirming a result
    already known after the first fifty tries.

    Once a host has been tried `min_attempts` times with no success at all, its
    remaining pages are archived as blocked without a request. They were going
    to fail either way; the only difference is 90 seconds versus a millisecond.
    """

    def __init__(self, min_attempts: int = 40, parole_every: int = 50):
        self.min_attempts = min_attempts
        self.parole_every = parole_every
        self._tried: dict[str, int] = {}
        self._ok: dict[str, int] = {}
        self._skipped: dict[str, int] = {}

    @classmethod
    def from_db(cls, conn, min_attempts: int = 40, parole_every: int = 50) -> "Breaker":
        """Seed from history - but only from attempts that still stand.

        A row requeued for a fresh trial has its verdict cleared, and the
        breaker must not re-import the old one. Counting every row ever
        attempted meant a host released for retesting was condemned again on
        the next startup before a single new request went out: jbc.org's 837
        requeued pages were all archived by the breaker, not one of them
        actually tried. A pardon has to clear the record it pardons.
        """
        b = cls(min_attempts, parole_every)
        for r in conn.execute(
            """SELECT final_host, COUNT(*) tried, SUM(status='done') ok
               FROM documents
               WHERE final_host IS NOT NULL AND final_host <> ''
                 AND status IN ('done', 'blocked', 'failed')
               GROUP BY final_host"""
        ):
            if r["final_host"] in RESOLVER_HOSTS:
                continue
            b._tried[r["final_host"]] = r["tried"]
            b._ok[r["final_host"]] = r["ok"] or 0
        return b

    def record(self, host: str | None, ok: bool) -> None:
        """A success on parole clears the breaker: reset the skip counter so
        the host is treated as live again."""
        if not host or host in RESOLVER_HOSTS:
            return
        if ok:
            self._skipped.pop(host, None)
        self._tried[host] = self._tried.get(host, 0) + 1
        if ok:
            self._ok[host] = self._ok.get(host, 0) + 1

    def is_dead(self, host: str | None, *, consume: bool = True) -> bool:
        """Dead, unless this attempt is a scheduled parole check.

        A breaker that never reconsiders turns a temporary condition into a
        permanent verdict. Three hosts - cell.com, doi.org, jbc.org, 3,359
        pages between them - were condemned during the window when our own
        rate limiting was keyed on the wrong thing and we were hitting some
        publishers five times faster than intended. Retested afterwards they
        answer about a third of the time. We had recorded our own misbehaviour
        as their refusal.

        So one request in `parole_every` is let through regardless. If the host
        has genuinely reopened, that success clears the breaker; if not, the
        cost is one request per fifty.
        """
        if not host or host in RESOLVER_HOSTS:
            return False
        if self._tried.get(host, 0) < self.min_attempts or self._ok.get(host, 0):
            return False
        if not consume:
            # Asking whether a host is dead must not spend its parole. The
            # first version incremented the counter on every call, so listing
            # the dead hosts for the startup log consumed one parole slot per
            # host - real parole requests then happened less often than one in
            # fifty - and a host could be reported alive purely because the
            # log's own call landed on the boundary. Observation changed the
            # thing observed, and the log was the least reliable place to
            # notice it.
            return True
        self._skipped[host] = self._skipped.get(host, 0) + 1
        if self._skipped[host] % self.parole_every == 0:
            return False          # parole: try it again
        return True

    def dead_hosts(self) -> list[tuple[str, int]]:
        """Report only. Uses the non-consuming form, so calling this leaves
        the parole schedule exactly as it was."""
        return sorted(
            ((h, n) for h, n in self._tried.items() if self.is_dead(h, consume=False)),
            key=lambda kv: -kv[1])
