# Checks for R/analysis_data.R. Run from the project root:
#   Rscript tests/r/test_analysis_data.R
#
# The point of ensure_analysis_inputs() is the fresh clone: the client has the
# repository and nothing else, and the analysis has to find its data anyway.
# That path is only exercised once, on a machine nobody here is sitting at, so
# it is tested rather than tried.

source("R/analysis_data.R")

fails <- 0L
ok <- function(label, cond) {
  if (isTRUE(cond)) cat("ok   ", label, "\n")
  else { fails <<- fails + 1L; cat("FAIL ", label, "\n") }
}

# -- the real archives carry what the analysis asks of them -----------------
# Checked by listing, not extracting: if the share bundle is rebuilt without a
# file the analysis needs, this is where it shows up, not in the client's
# session six weeks later.
for (a in unique(unname(ANALYSIS_INPUTS))) {
  z <- file.path(SHARE_DIR, a)
  if (!file.exists(z)) { ok(paste(a, "exists"), FALSE); next }
  members <- utils::unzip(z, list = TRUE)$Name
  want <- basename(names(ANALYSIS_INPUTS)[ANALYSIS_INPUTS == a])
  for (w in want) ok(paste0(w, " is inside ", a), w %in% members)
}

# -- the mechanism, on a scratch copy ---------------------------------------
tmp <- file.path(tempdir(), "cs43-ensure-test")
unlink(tmp, recursive = TRUE)
dir.create(file.path(tmp, "data", "share"), recursive = TRUE)

old <- setwd(tmp)
on.exit(setwd(old), add = TRUE)
# Members are stored flat, as build_share.py writes them: build the archive
# from files in the working directory, then take them away again so the only
# copy left is the one inside the zip.
writeLines(c("doi,year", "10.1/x,2020"), "toy_clean.csv")
writeLines(c("doi,control", "10.1/x,10.2/y"), "toy_controls.csv")
zipped <- suppressWarnings(utils::zip(file.path("data", "share", "toy.zip"),
                                      files = c("toy_clean.csv", "toy_controls.csv"),
                                      flags = "-q"))
if (!identical(zipped, 0L) || !file.exists(file.path("data", "share", "toy.zip"))) {
  cat("SKIP  no working zip command on this machine; mechanism tests skipped\n")
  setwd(old); unlink(tmp, recursive = TRUE)
  cat("\n", if (fails) sprintf("%d FAILED", fails) else "all passed", "\n", sep = "")
  quit(status = if (fails) 1L else 0L)
}
unlink(c("toy_clean.csv", "toy_controls.csv"))

toy <- c("data/clean/toy_clean.csv" = "toy.zip",
         "data/controls/toy_controls.csv" = "toy.zip")
ok("nothing is unpacked yet", !file.exists("data/clean/toy_clean.csv"))
ensure_analysis_inputs(toy)
ok("a missing input is unpacked into data/clean",
   file.exists("data/clean/toy_clean.csv"))
ok("and into data/controls", file.exists("data/controls/toy_controls.csv"))
ok("the contents survive the trip",
   grepl("10.1/x", paste(readLines("data/clean/toy_clean.csv"), collapse = "")))

# A second call must not overwrite work already on disk.
writeLines("edited", "data/clean/toy_clean.csv")
ensure_analysis_inputs(toy)
ok("an input already present is left alone",
   identical(readLines("data/clean/toy_clean.csv"), "edited"))

# -- failure is explicit ----------------------------------------------------
e <- tryCatch(ensure_analysis_inputs(c("data/clean/absent.csv" = "no_such.zip")),
              error = function(e) conditionMessage(e))
ok("a missing archive is an error", is.character(e))
ok("the error names the archive", grepl("no_such.zip", e))
ok("the error says how to get it back", grepl("build_share", e))

e2 <- tryCatch(ensure_analysis_inputs(c("data/clean/not_in_zip.csv" = "toy.zip")),
               error = function(e) conditionMessage(e))
ok("a member missing from an existing archive is an error", is.character(e2))
ok("that error says the bundle is stale", grepl("rebuild", e2))

setwd(old)
unlink(tmp, recursive = TRUE)

cat("\n", if (fails) sprintf("%d FAILED", fails) else "all passed", "\n", sep = "")
quit(status = if (fails) 1L else 0L)
