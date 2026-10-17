"""Central paths and tunables. Everything else imports from here.

Set RWPIPE_DATA to relocate the data directory - useful when a team member
keeps captures on a different drive, and for testing against a scratch copy
without touching a live crawl.
"""
import os
import re
from pathlib import Path

# repo root = parent of src/
ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = ROOT.parent                     # "E:/5703 capstone"

# The Retraction Watch CSV, looked for in this order:
#   1. RWPIPE_CSV, if set;
#   2. data/raw/retraction_watch.csv inside the project - where the handover
#      copy carries it, so a folder moved to another machine still finds it;
#   3. Data/retraction_watch.csv one level up - where it sat while this was
#      built, outside the repository.
# If none exists the in-project path is used, so the error names the place a
# client should put the file.
def _raw_csv() -> Path:
    candidates = [Path(os.environ["RWPIPE_CSV"])] if os.environ.get("RWPIPE_CSV") else []
    candidates += [ROOT / "data" / "raw" / "retraction_watch.csv",
                   PROJECT_ROOT / "Data" / "retraction_watch.csv"]
    for c in candidates:
        if c.is_file():
            return c
    return candidates[0]


RAW_CSV = _raw_csv()
DATA = Path(os.environ.get("RWPIPE_DATA") or (ROOT / "data"))
INTERIM = DATA / "interim"
MHTML_DIR = DATA / "mhtml"
REPORTS = DATA / "reports"
DB_PATH = INTERIM / "pipeline.sqlite"

for _d in (INTERIM, MHTML_DIR, REPORTS):
    _d.mkdir(parents=True, exist_ok=True)

# --- crawler politeness -------------------------------------------------
# Per-host minimum gap between requests, in seconds. Publishers with known
# aggressive bot protection get a wider gap. Tune from probe results.
DEFAULT_HOST_DELAY = 5.0
HOST_DELAY = {
    "ieeexplore.ieee.org": 12.0,
    "www.sciencedirect.com": 12.0,
    "linkinghub.elsevier.com": 12.0,
    "onlinelibrary.wiley.com": 8.0,
    "link.springer.com": 6.0,
    "journals.plos.org": 3.0,
}

NAV_TIMEOUT_MS = 45_000
MAX_ATTEMPTS = 3

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Contact address sent with every API and plain-HTTP request (User-Agent,
# Crossref's and OpenAlex's mailto, NCBI's email), which puts the requests in
# the "polite pool" and tells a site whom to write to about them. It has to
# reach whoever is running the pipeline, so that person sets RWPIPE_CONTACT;
# without it, the address of the student who built it.
#
# Until 2026-10-13 this was a fixed placeholder group address, here since the
# first commit with a TODO to replace it, while twelve scripts wrote the
# student's own university address into their User-Agent instead - two
# addresses, and no way for the client to give his. Every request now names
# this one; R reads the same variable (polite_contact() in R/python.R), and a
# script R starts inherits it.
DEFAULT_CONTACT = "xbao0816@uni.sydney.edu.au"


def contact_address(value: str = "") -> str:
    contact = value.strip() or DEFAULT_CONTACT
    local, sep, domain = contact.partition("@")
    labels = domain.split(".")
    if (not contact.isascii() or any(ord(c) < 33 or ord(c) == 127 for c in contact) or
            sep != "@" or "@" in domain or not local or
            not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+", local) or
            len(labels) < 2 or any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
                                   for label in labels)):
        raise ValueError("RWPIPE_CONTACT must be one ASCII email address without spaces or control characters")
    return contact


POLITE_CONTACT = contact_address(os.environ.get("RWPIPE_CONTACT", ""))
