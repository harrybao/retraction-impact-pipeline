# Smoke test: every Stage 1/2 R function, run once on a separate directory.
#
#   Rscript tests/r/smoke_stages.R [delivery_root]
#
# Network steps are limited to a few requests each. Merges run as dry runs
# against the local delivery folders, when present. Nothing is written to the
# collected dataset: every call gets `data_dir = scratch`. One row per step
# goes to data/reports/r-stage-smoke-<date>.csv.

source("R/python.R"); source("R/stages.R"); source("R/handover.R")

args <- commandArgs(trailingOnly = TRUE)
deliveries <- if (length(args)) args[1] else file.path(dirname(pipeline_dir()))
scratch <- file.path(tempdir(), "cs43-smoke")
unlink(scratch, recursive = TRUE)
dir.create(scratch, recursive = TRUE)

# A step that needs something this machine may legitimately not have - a
# publisher key, an institutional entitlement, a teammate's delivery folder -
# declares it in `needs`. Those are reported as skipped when they fail,
# because their failing says nothing about whether the R wrapper works.
# Anything else failing is a failure, and this script exits non-zero for it.
#
# It did not, before. It printed how many steps were ok and exited 0 either
# way, so it passed in any harness that checks an exit status - which is worse
# than having no smoke test, because it is a green light that means nothing.
log <- data.frame(step = character(), outcome = character(), seconds = numeric(),
                  note = character(), needs = character(), stringsAsFactors = FALSE)
step <- function(name, expr, note = "", needs = "") {
  t0 <- Sys.time()
  err <- tryCatch({ force(expr); NULL }, error = function(e) conditionMessage(e))
  secs <- round(as.numeric(difftime(Sys.time(), t0, units = "secs")), 1)
  outcome <- if (is.null(err)) "ok" else if (nzchar(needs)) "skipped" else "error"
  log[nrow(log) + 1, ] <<- list(name, outcome, secs,
                                if (is.null(err)) note else err, needs)
  message(sprintf("\n== %-28s %-7s %6.1fs %s\n", name, toupper(outcome), secs,
                  if (is.null(err)) note else err))
}
have <- function(...) { p <- file.path(deliveries, ...); if (file.exists(p)) p else NULL }

step("use_pipeline_python", use_pipeline_python(),
     needs = "uv, and a cache path it can link into")
# Without an interpreter nothing below can run, and thirty copies of the same
# error say less than one. Stop here and name it.
if (log$outcome[log$step == "use_pipeline_python"] != "ok") {
  message("\nno Python interpreter, so nothing downstream can be tested.")
  message("note: ", log$note[log$step == "use_pipeline_python"])
  message("set R_USER_CACHE_DIR to a path uv can link into (a non-redirected ",
          "drive), or set CS43_PYTHON to an existing interpreter, then run again.")
  quit(status = 1L)
}
step("install_browser", install_browser(),
     needs = "network, and the Playwright browser download")

# Stage 0 and 1a
step("build_queue", build_queue(scratch), "whole CSV into a separate frontier")
step("crawl", crawl(scratch, workers = 1, limit = 3, prefix = "10.1371", max_attempts = 1),
     "3 PLOS pages")
step("verify_archives", verify_archives(scratch))
step("find_oa_locations", find_oa_locations(scratch, limit = 3))
step("recover_pmc", recover_pmc(scratch, limit = 3))
step("recover_open_access", recover_open_access(scratch, limit = 3))
step("recover_wayback", recover_wayback(scratch, limit = 3))
step("probe_plain_http", probe_plain_http(scratch, hosts = 2, delay = 1),
     needs = "network")
# Needs a host or a prefix: the script refuses to sweep the whole frontier
# against publishers that have said no, and the smoke test was calling it
# with neither, so it exited 2 every time and the run reported a failure
# that was the script behaving correctly.
step("recover_plain_http",
     recover_plain_http(scratch, prefix = "10.1371", limit = 3, dry_run = TRUE),
     "dry run, one prefix")
# Works from probe results, and a scratch database has none until the probe
# above has run against this frontier.
step("recover_remainder", recover_remainder(scratch, dry_run = TRUE),
     needs = "probe_remainder results for this frontier")
step("recover_elsevier", recover_elsevier(scratch, limit = 2),
     needs = "ELSEVIER_API_KEY + institutional entitlement")
step("enrich_metadata", enrich_metadata(scratch, limit = 3))
step("backfill_hosts", backfill_hosts(scratch, limit = 3))
step("record_doi_corrections", record_doi_corrections(scratch, list = TRUE))

# Merges: dry runs on the delivery folders, when this machine has them
tandf_log <- have("_group-repo", "Labeller F", "tandf_download_log_final.csv")
if (!is.null(have("tandf-delivery", "mhtml")) && !is.null(tandf_log)) {
  step("merge_batch(tandf)", merge_batch("tandf", have("tandf-delivery", "mhtml"),
                                        log = tandf_log, data_dir = scratch), "dry run")
}
if (!is.null(have("sage_mhtml"))) {
  step("merge_batch(sage)", merge_batch("sage", have("sage_mhtml"), data_dir = scratch), "dry run")
}
if (!is.null(have("CAMPUS-KIT 2", "CAMPUS-KIT"))) {
  step("merge_batch(campus)", merge_batch("campus", have("CAMPUS-KIT 2", "CAMPUS-KIT"),
                                          data_dir = scratch), "dry run")
}
if (!is.null(have("iop-delivery"))) {
  step("merge_batch(iop)", merge_batch("iop", have("iop-delivery"), data_dir = scratch), "dry run")
  step("check_iop_delivery", check_iop_delivery(have("iop-delivery"), data_dir = scratch))
}
# A teammate's shard: this scratch run's own database stands in for one.
shard <- file.path(tempdir(), "cs43-shard")
unlink(shard, recursive = TRUE)
step("merge_batch(shard)", {
  dir.create(shard)
  file.copy(file.path(scratch, "interim", "pipeline.sqlite"), shard)
  file.copy(file.path(scratch, "mhtml"), shard, recursive = TRUE)
  merge_batch("shard", shard, who = "smoke-test", data_dir = scratch)
}, "dry run, own scratch database as the teammate's")

# Stage 1b
step("collect_openalex", collect_openalex(scratch, limit = 3))
step("collect_altmetric", collect_altmetric(scratch, limit = 3),
     needs = "ALTMETRIC_API_KEY")
step("collect_plumx", collect_plumx(scratch, probe = TRUE),
     needs = "ELSEVIER_API_KEY + ELSEVIER_INSTTOKEN")
step("collect_wikipedia", collect_wikipedia(scratch, limit = 3))

# Stage 2
step("extract_text", extract_text(scratch))
step("flag_shared_bodies", flag_shared_bodies(scratch))
step("export_all", export_all(scratch))
step("build_share", build_share(scratch))
step("rebuild_all (plan only)", rebuild_all(scratch))

log$outputs_in_scratch <- ""
log$outputs_in_scratch[log$step == "export_all"] <-
  paste(length(list.files(file.path(scratch, "clean"))), "files in clean/")
log$outputs_in_scratch[log$step == "build_share"] <-
  paste(length(list.files(file.path(scratch, "share"))), "files in share/")
out <- file.path(pipeline_dir(), "data", "reports",
                 format(Sys.Date(), "r-stage-smoke-%Y-%m-%d.csv"))
utils::write.csv(log, out, row.names = FALSE, fileEncoding = "UTF-8")
print(log[c("step", "outcome", "seconds", "note")], right = FALSE)

n_ok <- sum(log$outcome == "ok")
n_skip <- sum(log$outcome == "skipped")
failed <- log[log$outcome == "error", ]
message("\n", n_ok, " ok, ", n_skip, " skipped for want of a key or a delivery, ",
        nrow(failed), " failed, of ", nrow(log), " steps; written to ", out)
if (nrow(failed)) {
  message("\nfailed:")
  for (i in seq_len(nrow(failed))) {
    message("  ", failed$step[i], ": ", substr(failed$note[i], 1, 200))
  }
}
quit(status = if (nrow(failed)) 1L else 0L)
