# The client's handover test, rehearsed: the pipeline from R on a few extreme
# examples, paper DOIs and notice DOIs both, into a scratch directory.
#
#   Rscript analysis/handover_test.R
#
# The client will test the pipeline with both kinds of DOI, and the output
# expected is the MHTML, the Altmetric data and the analysis. So this is one
# call to run_pipeline(), as the client would make it: pages in a browser, the routes
# that built the dataset for the pages a publisher refuses, text, and the
# OpenAlex, Altmetric and PlumX data for the same items, in one report.
#
# Nothing here touches the collected dataset: run_pipeline() refuses data/.
#
# Writes data/reports/handover-test.csv and handover-test-steps.csv.

source("run.R")
# The client's first run, in order: the Python environment, then the
# browsers the crawler drives. Both are no-ops once done.
invisible(use_pipeline_python())
install_browser()

cases <- c(
  # paper DOIs
  "normal PLOS article"                     = "10.1371/journal.pone.0066275",
  "same DOI in both columns (Springer)"     = "10.1007/s10772-021-09882-4",
  "Elsevier, famous case with heavy news"   = "10.1016/s0140-6736(20)31180-6",
  "Taylor & Francis"                        = "10.1080/21691401.2019.1699825",
  "SAGE"                                    = "10.1177/2058739219837050",
  "IOP"                                     = "10.1088/1757-899x/1145/1/012038",
  "RSC, never obtained (HTTP 403)"          = "10.1039/c7ra02830j",
  "MDPI (WebKit fallback)"                  = "10.3390/jof6020045",
  "IEEE conference paper"                   = "10.1109/iccsit.2010.5564527",
  "pre-1985 book chapter (De Gruyter)"      = "10.1515/9783486820225-015",
  "Japanese-language article"               = "10.5360/membrane.35.160",
  # retraction-notice DOIs
  "notice: one notice for 659 records (SAGE)"    = "10.1177/10641246251331509",
  "notice: mass retraction with news (Springer)" = "10.1007/s13277-017-5487-6",
  "notice: single paper with news (Hindawi)"     = "10.1155/2016/4835617",
  "notice: OUP"                                  = "10.1093/alcalc/agu071",
  # DOIs the CSV does not hold: run all the same, with Crossref's details
  "not in the CSV: retracted after our snapshot (BMC, Oct 2026)" = "10.1186/s41065-025-00496-3",
  "not in the CSV: never retracted (PLOS Medicine)"              = "10.1371/journal.pmed.0020124",
  # items that must fail cleanly
  "record with no original DOI"             = "record:241",
  "record with no DOI at all"               = "record:17",
  "DOI that does not exist"                 = "10.9999/not-in-retraction-watch",
  "not a DOI"                               = "unavailable")

# Inside the project, beside the client's own runs (runs/ is gitignored), so
# nothing is written outside the folder he was given.
data_dir <- file.path(pipeline_dir(), "runs", paste0("handover-test_", format(Sys.Date())))
unlink(data_dir, recursive = TRUE)
t0 <- Sys.time()
res <- run_pipeline(unname(cases), data_dir = data_dir)
minutes <- round(as.numeric(difftime(Sys.time(), t0, units = "mins")), 1)

res$case <- names(cases)[match(res$item, cases)]
res <- res[c("case", setdiff(names(res), "case"))]
dir.create(file.path("data", "reports"), showWarnings = FALSE)
utils::write.csv(res, file.path("data", "reports", "handover-test.csv"),
                 row.names = FALSE, fileEncoding = "UTF-8")
utils::write.csv(attr(res, "steps"), file.path("data", "reports", "handover-test-steps.csv"),
                 row.names = FALSE, fileEncoding = "UTF-8")

old <- options(width = 200)
cat("\n== pages ==\n")
print(res[c("case", "status", "quality", "obtained_via", "reason")], right = FALSE)
cat("\n== media and citations ==\n")
print(res[!is.na(res$doi) & nzchar(res$doi),
          c("case", "page", "openalex_citations", "altmetric_record", "altmetric_posts",
            "altmetric_news", "plumx_record", "plumx_news")], right = FALSE)
options(old)
cat(sprintf("\n%d items, %d rows, %.1f minutes. Scratch directory: %s\n",
            length(cases), nrow(res), minutes, data_dir))
