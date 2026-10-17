# Every Stage 3 table and figure from the local dataset, in one call.
#
#   source("run.R"); reproduce_results()
#
# The client's requirement: reproducible R code that regenerates every table
# and figure we report. This runs each
# analysis script in order, each in its own R process (so one script's objects
# cannot leak into the next), writes a log per script to logs/reproduce/, and
# at the end compares every result file the run wrote with ours, as listed in
# data/reports_reference.csv: identical, changed, or new.
#
# What it needs is the handover dataset, not the internet and not a model:
#   data/clean/     the Stage 1-2 exports (or, for the citation part, the
#                   bundle in data/share, which ensure_analysis_inputs() unpacks)
#   data/controls/  the control group
#   data/llm/       the model's saved answers - no model server is needed to
#                   score them; one is needed only to ask again
#   data/labelling/ the seven labellers' returned sheets and the answer keys
#
# Stage 1 and 2 themselves (collection and text extraction) are rebuilt with
# rebuild_all(), which takes days and the API keys; they are not part of this.

REPRODUCE_STEPS <- c(
  "analysis/stage1_rendered_pages.R" = "Stage 1: what the collected pages are, their sizes and contents",
  "analysis/citation_impact.R"       = "Stage 3 parts 1-2: control group and citation impact",
  "analysis/labelling_agreement.R"   = "Stage 3 part 3: agreement among the seven labellers",
  "analysis/labelling_round2_score.R" = "Stage 3 part 3: round 2 of the labelling (protocol v2)",
  "analysis/llm_validate.R"          = "Stage 3 part 3: the model scored against the labellers",
  "analysis/media_classification.R"  = "Stage 3 part 4: media after retraction (paper DOIs)",
  "analysis/media_breakdowns.R"      = "Stage 3 part 4: media by reason, country, publisher, discipline",
  "analysis/notice_news_merge.R"     = "Stage 1: news on paper and notice DOIs, each article once",
  "analysis/news_coverage_union.R"   = "Stage 1: news coverage union, Altmetric and PlumX on paper and notice DOIs",
  "analysis/social_media.R"          = "Stage 3 part 4: social media coverage and validation limits",
  "analysis/media_with_notices.R"    = "Stage 3 part 4: media after retraction, notice posts included",
  "analysis/notice_classification.R" = "Stage 3 part 5: what retraction notices say",
  "analysis/notice_r0_check.R"       = "Stage 3 part 5: 'no reason given' checked against the labellers",
  "analysis/figures.R"               = "Stage 3 parts 4-5: the media and notice figures"
)

reports_checksums <- function(dir = file.path("data", "reports")) {
  # Recursive: the figures are in data/reports/figures, and they are results
  # too. Named by path below data/reports ("figures/notice-reasons.png").
  f <- list.files(dir, recursive = TRUE)
  stats::setNames(vapply(file.path(dir, f), content_md5, ""), f)
}

# A text file's checksum ignores line endings: R writes CSV lines ending in
# CRLF on Windows and LF on macOS and Linux, so the same numbers would
# otherwise read as "changed" on another system. Other files: their bytes.
TEXT_EXT <- "[.](csv|txt|md|json|log|tsv)$"
content_md5 <- function(path) {
  if (!grepl(TEXT_EXT, path, ignore.case = TRUE)) return(unname(tools::md5sum(path)))
  b <- readBin(path, "raw", file.size(path))
  tf <- tempfile()
  on.exit(unlink(tf))
  writeBin(b[b != as.raw(13)], tf)
  unname(tools::md5sum(tf))
}

# Our reports' checksums, shipped with the project. The comparison is against
# these - what we published - and not against whatever data/reports held
# before a run, which is ours only the first time (a run overwrites it).
# Rewritten only on purpose, by us, after a change we mean to publish.
REPORTS_REFERENCE <- file.path("data", "reports_reference.csv")

write_reports_reference <- function() {
  x <- reports_checksums()
  tracked <- tryCatch(system2("git", c("-C", shQuote(pipeline_dir()), "ls-files", "data/reports"),
                              stdout = TRUE), error = function(e) character())
  if (length(tracked)) x <- x[names(x) %in% sub("^data/reports/", "", tracked)]
  utils::write.csv(data.frame(file = names(x), md5 = unname(x)), REPORTS_REFERENCE,
                   row.names = FALSE)
  invisible(length(x))
}

reproduce_results <- function(steps = names(REPRODUCE_STEPS), stop_on_error = FALSE) {
  root <- pipeline_dir()
  redirect <- Sys.getenv("CS43_REPORTS", unset = NA_character_)
  if (!is.na(redirect) &&
      (!nzchar(trimws(redirect)) ||
       normalizePath(file.path(root, "data", "reports"), winslash = "/", mustWork = FALSE) !=
         normalizePath(redirect, winslash = "/", mustWork = FALSE))) {
    stop("CS43_REPORTS redirection is for standalone analysis rehearsals only; ",
         "unset it before reproduce_results() so all 14 scripts are verified together", call. = FALSE)
  }
  old <- setwd(root)
  on.exit(setwd(old), add = TRUE)
  logs <- file.path("logs", "reproduce")
  dir.create(logs, showWarnings = FALSE, recursive = TRUE)
  before <- reports_checksums()
  started <- Sys.time() - 1
  rscript <- file.path(R.home("bin"), if (.Platform$OS.type == "windows") "Rscript.exe" else "Rscript")
  rows <- list()
  for (s in steps) {
    cat(sprintf("%-36s %s ... ", s, REPRODUCE_STEPS[[s]]))
    t0 <- Sys.time()
    log <- file.path(logs, sub("[.]R$", ".log", basename(s)))
    # No model server, even if one is running on this machine: the analysis
    # reads the saved answers in data/llm, and a live server would make
    # llm_validate.R ask again the few items that failed when we ran it.
    res <- processx::run(rscript, s, wd = root, error_on_status = FALSE,
                         stdout = log, stderr = "2>&1",
                         env = c("current", CS43_LLM_HOST = "http://127.0.0.1:9"))
    secs <- round(as.numeric(difftime(Sys.time(), t0, units = "secs")))
    ok <- res$status == 0
    cat(if (ok) "ok" else "FAILED", sprintf("(%d s)\n", secs))
    rows[[length(rows) + 1L]] <- data.frame(script = s, what = REPRODUCE_STEPS[[s]],
                                            status = if (ok) "ok" else "failed",
                                            seconds = secs, log = log,
                                            stringsAsFactors = FALSE)
    if (!ok && stop_on_error) break
  }
  after <- reports_checksums()
  ours <- if (file.exists(REPORTS_REFERENCE)) {
    r <- utils::read.csv(REPORTS_REFERENCE, stringsAsFactors = FALSE)
    stats::setNames(r$md5, r$file)
  } else {
    before
  }
  # Only the files this run wrote are evidence; the folder also holds checks
  # and logs that no script here writes.
  written <- file.mtime(file.path("data", "reports", names(after))) >= started
  cmp <- data.frame(
    file = names(after),
    written_this_run = written,
    outcome = ifelse(!names(after) %in% names(ours), "new",
                     ifelse(after == ours[names(after)], "identical", "changed")),
    stringsAsFactors = FALSE)
  steps_df <- do.call(rbind, rows)
  w <- cmp[cmp$written_this_run, ]
  cat(sprintf(paste0("\n%d of %d scripts ran. Of the %d result files they wrote: ",
                     "%d identical to ours, %d changed, %d new.\n"),
              sum(steps_df$status == "ok"), nrow(steps_df), nrow(w),
              sum(w$outcome == "identical"), sum(w$outcome == "changed"),
              sum(w$outcome == "new")))
  if (any(w$outcome == "changed")) {
    cat("Changed:", paste(w$file[w$outcome == "changed"], collapse = ", "), "\n")
  }
  if (any(steps_df$status != "ok")) {
    cat("Failed - see the log named beside each:\n")
    print(steps_df[steps_df$status != "ok", c("script", "log")], row.names = FALSE)
  }
  utils::write.csv(steps_df, file.path(logs, "steps.csv"), row.names = FALSE)
  utils::write.csv(cmp, file.path(logs, "reports.csv"), row.names = FALSE)
  invisible(list(steps = steps_df, reports = cmp))
}

# The model runs behind data/llm/corpus, from R. Needs an Ollama server with
# the model (CS43_LLM_MODEL, default qwen3:14b) and hours: about 9 for the
# media texts, 2 for the notice posts, 17 for the notices (measured: 2.7, 2.4
# and 1.0 items a second on our graphics card). Resumable - a
# stopped run carries on from the last answer - and a finished file is left
# alone. reproduce_results() needs none of this; it reads the saved answers.
#   A   media texts filed under paper DOIs
#   AN  media texts filed under retraction-notice DOIs
#   B   the retraction notices themselves
classify_corpus <- function(tasks = c("A", "AN", "B"), limit = NULL) {
  if (!llm_available()) stop("no model server at ", LLM_HOST, " - start Ollama first",
                             call. = FALSE)
  rscript <- file.path(R.home("bin"), if (.Platform$OS.type == "windows") "Rscript.exe" else "Rscript")
  for (t in tasks) {
    message("\n== llm_classify_corpus.R ", t, " ==")
    res <- processx::run(rscript, c("analysis/llm_classify_corpus.R", t,
                                    if (!is.null(limit)) as.character(limit)),
                         wd = pipeline_dir(), echo = TRUE, error_on_status = FALSE)
    if (res$status != 0) stop("task ", t, " stopped with status ", res$status, call. = FALSE)
  }
  invisible(tasks)
}
