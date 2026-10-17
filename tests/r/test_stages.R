# Checks for R/stages.R. Run from the project root:
#   Rscript --vanilla tests/r/test_stages.R
#
# These run no Python and touch no data. The thing being checked is that the
# rebuild plan still lists every step there is: `pipeline_steps()` is a hand-
# written order, and a step added to the file but not to that list simply never
# runs in a full rebuild. Three had already drifted out of it that way -
# recover_plain_http, backfill_hosts and record_doi_corrections - and nothing
# failed, because a missing step is silence.

source("R/stages.R")

fails <- 0L
ok <- function(label, cond) {
  if (isTRUE(cond)) cat("ok   ", label, "\n")
  else { fails <<- fails + 1L; cat("FAIL ", label, "\n") }
}

steps <- pipeline_steps()
ok("the plan has a step and a needs column for every row",
   nrow(steps) > 0 && !anyNA(steps$step) && !anyNA(steps$needs))
ok("step and needs are the same length", length(steps$step) == length(steps$needs))

# Everything in R/stages.R that is a pipeline step. The exclusions are the
# functions that are not steps: the merges need a teammate's delivery folder
# and are documented separately, and the last three are the machinery itself.
NOT_A_STEP <- c("merge_batch", "check_iop_delivery", "pipeline_steps", "rebuild_all")
defined <- ls(envir = environment())
defined <- defined[vapply(defined, function(n) is.function(get(n)), logical(1))]
defined <- setdiff(defined, c(NOT_A_STEP, "ok", "fails"))
# Helpers that arrive from R/python.R rather than being stages.
from_python <- c("pipeline_dir", "use_pipeline_python", "set_api_keys", "run_python",
                 "run_script", "install_browser", "read_db", "opt", "flag")
defined <- setdiff(defined, from_python)

missing <- setdiff(defined, steps$step)
if (length(missing)) cat("      not in pipeline_steps():", paste(missing, collapse = ", "), "\n")
ok("every stage function appears in the rebuild plan", !length(missing))

unknown <- setdiff(steps$step, defined)
if (length(unknown)) cat("      in the plan but not defined:", paste(unknown, collapse = ", "), "\n")
ok("every step in the plan is a function that exists", !length(unknown))

# The three that had drifted out, named so a future edit that drops one again
# fails on its own line rather than in a set difference.
for (s in c("recover_plain_http", "backfill_hosts", "record_doi_corrections")) {
  ok(paste(s, "is in the rebuild plan"), s %in% steps$step)
}

# Order matters in two places: the recoveries run before the long tail, and
# nothing is exported before the text is extracted.
pos <- function(s) which(steps$step == s)[1]
ok("plain HTTP recovery runs before the remainder",
   pos("recover_plain_http") < pos("recover_remainder"))
ok("hosts are backfilled after metadata enrichment",
   pos("backfill_hosts") > pos("enrich_metadata"))
ok("text is extracted before anything is exported",
   pos("extract_text") < pos("export_all"))
ok("the share bundle is built last", pos("build_share") == nrow(steps))

# rebuild_all only prints a plan unless told otherwise. A full run takes days,
# so a default that started one would be a trap.
plan <- rebuild_all()
ok("rebuild_all returns a plan without running anything", is.data.frame(plan))
ok("the default plan is the whole pipeline", nrow(plan) == nrow(steps))
ok("from/to narrows the plan",
   nrow(rebuild_all(from = "extract_text", to = "export_all")) == 3)
ok("an unknown step is an error",
   inherits(try(rebuild_all(from = "no_such_step"), silent = TRUE), "try-error"))
ok("a reversed range is an error",
   inherits(try(rebuild_all(from = "build_share", to = "build_queue"), silent = TRUE),
            "try-error"))

# A confirmed run, with every step replaced by a stub: a failing step is
# recorded and the rest still run, the manual step is skipped by name, and a
# failed queue stops the run (nothing after it has anything to work on).
called <- character()
for (s in unique(steps$step)) {
  assign(s, local({ nm <- s; function(data_dir = NULL, ...) {
    called <<- c(called, nm)
    if (nm == "recover_elsevier") stop("ELSEVIER_API_KEY not set")
    invisible(NULL)
  } }))
}
invisible(capture.output(res <- suppressMessages(rebuild_all(data_dir = tempdir(), confirm = TRUE))))
ok("a confirmed run reports an outcome for every step",
   nrow(res) == nrow(steps) && !anyNA(res$outcome))
ok("a failing step is recorded with its reason",
   grepl("^failed: ELSEVIER_API_KEY not set", res$outcome[res$step == "recover_elsevier"]))
ok("the steps after a failure still run", "build_share" %in% called)
ok("recover_plain_http is skipped, not run",
   !"recover_plain_http" %in% called &&
     grepl("^skipped", res$outcome[res$step == "recover_plain_http"]))
called <- character()
build_queue <- function(data_dir = NULL, ...) { called <<- c(called, "build_queue"); stop("no CSV") }
invisible(capture.output(res <- suppressMessages(rebuild_all(data_dir = tempdir(), confirm = TRUE))))
ok("a failed queue stops the run", identical(called, "build_queue") && is.na(res$outcome[2]))

cat("\n", if (fails) sprintf("%d FAILED", fails) else "all passed", "\n", sep = "")
quit(status = if (fails) 1L else 0L)
