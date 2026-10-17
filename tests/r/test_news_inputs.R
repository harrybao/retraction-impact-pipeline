# Controller checks use a complete tiny ledger, with no external data access.
repo <- getwd()
fails <- 0L
ok <- function(label, cond) {
  if (isTRUE(cond)) cat("ok   ", label, "\n")
  else { fails <<- fails + 1L; cat("FAIL ", label, "\n") }
}
fixture <- function(change = function(x) x, hand = NULL) {
  root <- tempfile("news-inputs-")
  dir.create(file.path(root, "data", "clean"), recursive = TRUE)
  dir.create(file.path(root, "R"))
  file.copy(file.path(repo, "R", c("analysis_data.R", "news_union.R", "notice_news.R")),
            file.path(root, "R"))
  x <- list(
    altmetric = data.frame(doi = c("10.1/p", "10.1/q"), news_count = c(1, 1)),
    plumx = data.frame(doi = c("10.1/p", "10.1/q"), detail_json = "{}"),
    altmetric_notice = data.frame(retraction_doi = c("10.1/n", "10.1/m"), news_count = 0),
    plumx_notice = data.frame(retraction_doi = c("10.1/n", "10.1/m"), detail_json = "{}"),
    notice_papers = data.frame(retraction_doi = c("10.1/n", "10.1/m"),
                               original_doi = c("10.1/p", "10.1/q"), n_papers = 1),
    records_clean = data.frame(original_doi = c("10.1/p", "10.1/q"),
                               retraction_doi = c("10.1/n", "10.1/m"),
                               retraction_nature = c("Retraction", "Correction")))
  x <- change(x)
  for (name in names(x)) write.csv(x[[name]], file.path(root, "data", "clean", paste0(name, ".csv")), row.names = FALSE)
  if (!is.null(hand)) {
    dir.create(file.path(root, "data", "reports"), recursive = TRUE)
    write.csv(hand, file.path(root, "data", "reports", "news-coverage-union-2026-09-27.csv"), row.names = FALSE)
  }
  old <- Sys.getenv("CS43_REPORTS", unset = NA)
  on.exit({ setwd(repo); if (is.na(old)) Sys.unsetenv("CS43_REPORTS") else Sys.setenv(CS43_REPORTS = old) })
  setwd(root)
  Sys.setenv(CS43_REPORTS = file.path(root, "reports"))
  err <- tryCatch({ capture.output(sys.source(file.path(repo, "analysis", "news_coverage_union.R"),
                                             envir = new.env(parent = globalenv()))); "" },
                  error = conditionMessage)
  list(root = root, error = err)
}
r <- fixture()
ok("complete mixed-nature corpus runs", identical(r$error, ""))
if (nzchar(r$error)) stop("baseline fixture failed: ", r$error, call. = FALSE)
p <- read.csv(file.path(r$root, "reports", "news-coverage-population.csv"))
ok("scope distinguishes coverage with and without Retraction records",
   identical(p$original_dois, c(2L, 1L, 2L, 1L, 1L, 0L)))
r <- fixture(hand = data.frame(measure = c("ALL FOUR", "  papers with dated, text-bearing Altmetric news"),
                                papers = c(2, 2)))
comparison <- read.csv(file.path(r$root, "reports", "news-coverage-union-vs-2026-09-27.csv"))
clarified <- comparison[comparison$measure == "  papers reached through Altmetric news counts", ]
ok("clarifying the old text claim preserves historical count comparison",
   identical(r$error, "") && nrow(clarified) == 1L && clarified$papers_2026_09_27 == 2L &&
     clarified$difference == 0 && grepl("Label clarified", clarified$note))
cases <- list(
  missing_count = function(x) { x$altmetric$news_count <- NULL; x },
  blank_key = function(x) { x$plumx$doi[1] <- ""; x },
  duplicate_key = function(x) { x$altmetric$doi[2] <- x$altmetric$doi[1]; x },
  incomplete_both = function(x) { x$altmetric <- x$altmetric[1, ]; x$plumx <- x$plumx[1, ]; x },
  incomplete_alt_notice = function(x) { x$altmetric_notice <- x$altmetric_notice[1, ]; x },
  incomplete_plumx_notice = function(x) { x$plumx_notice <- x$plumx_notice[1, ]; x },
  incomplete_both_notices = function(x) { x$altmetric_notice <- x$altmetric_notice[1, ]; x$plumx_notice <- x$plumx_notice[1, ]; x },
  missing_whole_notice = function(x) { x$notice_papers <- x$notice_papers[1, ]; x },
  unknown_size = function(x) { x$notice_papers$n_papers <- NA; x },
  overflowing_size = function(x) { x$notice_papers$n_papers <- 2147483648; x },
  inconsistent_size = function(x) { x$notice_papers$n_papers <- 2; x },
  duplicate_pair = function(x) { x$notice_papers <- rbind(x$notice_papers, x$notice_papers); x })
for (name in names(cases)) {
  r <- fixture(cases[[name]])
  ok(paste(name, "fails before writing results"), nzchar(r$error) &&
       !length(list.files(file.path(r$root, "reports"), pattern = "[.]csv$")))
}
r <- fixture(function(x) { x$plumx_notice$error <- c("http 406", ""); x })
p <- read.csv(file.path(r$root, "reports", "news-coverage-population.csv"))
ok("a failed notice lookup is reported as unknown",
   identical(r$error, "") && tail(p$original_dois, 1) == 1L)
r <- fixture(function(x) {
  x$records_clean <- rbind(x$records_clean, data.frame(original_doi = "10.1/p",
                          retraction_doi = "10.1/p", retraction_nature = "Retraction"))
  x
})
ok("self-only notices use the existing complete paper lookup", identical(r$error, ""))
r <- fixture(function(x) {
  x$records_clean <- rbind(x$records_clean, data.frame(original_doi = "",
                          retraction_doi = "10.1/unmapped", retraction_nature = "Retraction"))
  x
})
ok("a notice with no original DOI still requires an attention lookup",
   grepl("complete notice DOI list", r$error))
cat(if (fails) sprintf("\n%d FAILED\n", fails) else "\nall passed\n")
quit(status = if (fails) 1L else 0L)
