# Make sure the Stage 3 analysis has its inputs, on a machine that has just
# cloned the repository.
#
# The cleaned tables and the control group are derived data, so they are not
# committed - `data/clean/*.csv` and `data/controls/*` are both ignored. What
# IS committed is `data/share/`, which supplies the mapped inputs below.
# Notice attention exports, saved model answers and human-label sheets require
# the matching dataset package; a source-only clone cannot run every analysis.
#
# The client runs R and nothing else. Telling him to unzip four archives into
# the right two folders before sourcing anything is how a reproducible project
# stops being reproducible, so the analysis calls this instead and the unzip
# happens by itself, once.
#
# Nothing here re-collects anything. If an archive is missing the function says
# which one and stops, rather than quietly analysing a smaller dataset.

# analysis input -> the archive in data/share that carries it
#
# All four Stage 3 parts, not just the citation analysis. The media and notice
# work reads the attention and text archives, and leaving those out meant the
# labelling sampler and the media counts worked on this machine and nowhere
# else - the failure a fresh clone is supposed to catch.
ANALYSIS_INPUTS <- c(
  # Part 2, the citation analysis.
  "data/clean/openalex_engagement.csv" = "cs43_all_datasets.zip",
  "data/clean/records_clean.csv"       = "cs43_all_datasets.zip",
  "data/clean/subjects.csv"            = "cs43_all_datasets.zip",
  "data/clean/reasons.csv"             = "cs43_all_datasets.zip",
  "data/clean/countries.csv"           = "cs43_all_datasets.zip",
  "data/controls/controls_v1.csv"      = "cs43_control_group.zip",
  "data/controls/papers.csv"           = "cs43_control_group.zip",
  "data/controls/groups.csv"           = "cs43_control_group.zip",
  # Parts 1, 3 and 4: the dated mentions behind the media analysis and the
  # labelling sample, and the retraction notices behind the statement analysis.
  "data/clean/altmetric_posts.csv.gz"  = "cs43_attention_and_access.zip",
  "data/clean/altmetric_detail.csv"    = "cs43_attention_and_access.zip",
  "data/clean/plumx.csv"               = "cs43_attention_and_access.zip",
  "data/clean/stage2_notices.csv.gz"   = "cs43_stage2_text.zip",
  "data/clean/stage2_index.csv"        = "cs43_stage2_text.zip",
  "data/clean/altmetric.csv"           = "cs43_all_datasets.zip"
)

SHARE_DIR <- file.path("data", "share")

ensure_analysis_inputs <- function(inputs = ANALYSIS_INPUTS, share = SHARE_DIR) {
  missing <- inputs[!file.exists(names(inputs))]
  if (!length(missing)) return(invisible(names(inputs)))

  archives <- unique(unname(missing))
  absent <- archives[!file.exists(file.path(share, archives))]
  if (length(absent)) {
    stop("the analysis needs ", paste(names(missing), collapse = ", "),
         ", which live in ", paste(absent, collapse = " and "),
         " - but ", if (length(absent) > 1) "those archives are" else "that archive is",
         " not in ", share, ". Restore the share bundle from the repository, ",
         "or rebuild it with scripts/build_share.py.", call. = FALSE)
  }

  for (a in archives) {
    want <- names(missing)[missing == a]
    # Archive members are stored flat, so the member name is the basename and
    # the destination is whichever folder that input belongs in.
    for (f in want) {
      message("unpacking ", basename(f), " from ", a)
      dir.create(dirname(f), showWarnings = FALSE, recursive = TRUE)
      got <- utils::unzip(file.path(share, a), files = basename(f),
                          exdir = dirname(f), overwrite = TRUE)
      if (!length(got) || !file.exists(f)) {
        stop(basename(f), " is not inside ", a, ". The share bundle is older ",
             "than this analysis; rebuild it with scripts/build_share.py.",
             call. = FALSE)
      }
    }
  }
  invisible(names(inputs))
}

# The publisher grouping is written by R and tracked, so it is normally just
# there. Rebuild it if it is not, because a missing mapping would silently fall
# back to raw imprint strings and change the client's publisher ranking.
ensure_publisher_families <- function(path = file.path(SHARE_DIR, "publisher_families.csv")) {
  if (!file.exists(path)) {
    source("R/publisher_families.R")
    ensure_analysis_inputs(ANALYSIS_INPUTS["data/clean/records_clean.csv"])
    write_publisher_families(out = path)
  }
  utils::read.csv(path, stringsAsFactors = FALSE, colClasses = "character")
}
