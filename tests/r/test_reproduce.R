# Checks for the comparison in R/reproduce.R. Run from the project root:
#   Rscript --vanilla tests/r/test_reproduce.R
#
# reproduce_results() says whether each result file is identical to ours. R
# ends CSV lines with CRLF on Windows and LF elsewhere, so the comparison has
# to ignore line endings in text files - or every table reads as "changed" on
# a Mac with the same numbers - while still catching a real change.

source("R/reproduce.R")

fails <- 0L
ok <- function(label, cond) {
  if (isTRUE(cond)) cat("ok   ", label, "\n")
  else { fails <<- fails + 1L; cat("FAIL ", label, "\n") }
}

ok("fourteen distinct analysis steps are registered",
   length(REPRODUCE_STEPS) == 14L && !anyDuplicated(names(REPRODUCE_STEPS)))
ok("every registered analysis exists", all(file.exists(names(REPRODUCE_STEPS))))
ok("news union and social media are included",
   all(c("analysis/news_coverage_union.R", "analysis/social_media.R") %in% names(REPRODUCE_STEPS)))
ok("paper/notice merge precedes the coverage and social summaries",
   match("analysis/notice_news_merge.R", names(REPRODUCE_STEPS)) <
     match("analysis/news_coverage_union.R", names(REPRODUCE_STEPS)) &&
   match("analysis/news_coverage_union.R", names(REPRODUCE_STEPS)) <
     match("analysis/social_media.R", names(REPRODUCE_STEPS)))
local({
  old <- Sys.getenv("CS43_REPORTS", unset = NA)
  on.exit(if (is.na(old)) Sys.unsetenv("CS43_REPORTS") else Sys.setenv(CS43_REPORTS = old))
  pipeline_dir <- function() getwd()
  environment(reproduce_results) <- environment()
  setwd <- function(...) stop("output guard did not fire", call. = FALSE)
  Sys.setenv(CS43_REPORTS = file.path(tempdir(), "redirected-reports"))
  err <- try(reproduce_results(), silent = TRUE)
  ok("full reproduction refuses a partial output redirect before running any script",
     inherits(err, "try-error") && grepl("standalone analysis rehearsals", err, fixed = TRUE))
})

d <- tempfile("reports-")
dir.create(file.path(d, "figures"), recursive = TRUE)
put <- function(name, bytes) writeBin(bytes, file.path(d, name))
put("crlf.csv", charToRaw("\"a\",\"b\"\r\n1,2\r\n"))
put("lf.csv",   charToRaw("\"a\",\"b\"\n1,2\n"))
put("other.csv", charToRaw("\"a\",\"b\"\n1,3\n"))
put("figures/x.png", as.raw(c(0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a)))
put("figures/y.png", as.raw(c(0x89, 0x50, 0x4e, 0x47, 0x0a, 0x1a, 0x0a)))

x <- reports_checksums(d)
ok("files below the folder are named by their relative path",
   setequal(names(x), c("crlf.csv", "lf.csv", "other.csv", "figures/x.png", "figures/y.png")))
ok("a CSV with CRLF endings matches the same CSV with LF", x[["crlf.csv"]] == x[["lf.csv"]])
ok("a changed number is still a change", x[["lf.csv"]] != x[["other.csv"]])
ok("binary files are compared byte for byte, CR included",
   x[["figures/x.png"]] != x[["figures/y.png"]])
ok("a binary file's checksum is the plain md5",
   x[["figures/x.png"]] == unname(tools::md5sum(file.path(d, "figures/x.png"))))

unlink(d, recursive = TRUE)
cat("\n", if (fails) sprintf("%d FAILED", fails) else "all passed", "\n", sep = "")
quit(status = if (fails) 1L else 0L)
