"""Central paths and tunables. Everything else imports from here.

Set RWPIPE_DATA to relocate the data directory - useful when a team member
keeps captures on a different drive, and for testing against a scratch copy
without touching a live crawl.
"""
import os
from pathlib import Path

# repo root = parent of src/
ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = ROOT.parent                     # "E:/5703 capstone"

RAW_CSV = PROJECT_ROOT / "Data" / "retraction_watch.csv"
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

# Contact address sent to Crossref/OpenAlex for the "polite pool".
# TODO: replace with the group's shared address before running at scale.
POLITE_CONTACT = "comp5703-cs43@uni.sydney.edu.au"
