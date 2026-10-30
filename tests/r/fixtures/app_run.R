# A deterministic worker fixture: no network, database, or production exports.
run_pipeline <- function(items, data_dir, ...) {
  dir.create(data_dir, recursive = TRUE, showWarnings = FALSE)
  key <- Sys.getenv("ALTMETRIC_API_KEY")
  cat("Fixture running\n")
  if (items[1] == "record:0") stop("Fixture controlled failure")
  if (items[1] == "record:9") Sys.sleep(60)
  if (nzchar(key)) {
    half <- nchar(key) %/% 2L
    cat(substr(key, 1, half)); flush.console(); Sys.sleep(.15)
    cat(substr(key, half + 1L, nchar(key)), "\n", sep = "")
  }
  if (any(!grepl("^(10[.]|record:)", items))) stop("An invalid token reached the collector")
  x <- data.frame(item = items, doi = items, page = "original", status = "done", verified = 1L, quality = "full", body_chars = 6000,
                   csv_title = "Fixture paper title", extracted_title = "Extracted fixture title",
                   notice_start = "This article has been retracted because of a fixture reason.",
                   error = if (nzchar(key)) paste0("https://fixture.invalid?api_key=", key) else "")
  unknown <- items == "record:999999"
  x$status[unknown] <- "not run"; x$verified[unknown] <- NA_integer_; x$quality[unknown] <- NA_character_
  x$reason <- ifelse(unknown, "Record ID not in the Retraction Watch CSV", "ok")
  attr(x, "steps") <- data.frame(step = "fixture", outcome = "ok", seconds = 0)
  x
}

REPRODUCE_STEPS <- stats::setNames(paste("Fixture", 1:14), paste0("analysis/step", 1:14, ".R"))
reproduce_results <- function(steps) {
  if (nzchar(Sys.getenv("ALTMETRIC_API_KEY"))) stop("Credentials reached offline reproduction")
  i <- match(steps, names(REPRODUCE_STEPS))
  dir.create("data/reports", recursive = TRUE, showWarnings = FALSE)
  file <- paste0("step", i, ".csv")
  utils::write.csv(data.frame(value = i), file.path("data/reports", file), row.names = FALSE)
  list(steps = data.frame(script = steps, what = REPRODUCE_STEPS[[steps]], status = "ok", seconds = 0, log = "fixture"),
       reports = data.frame(file = file, written_this_run = TRUE,
                            outcome = if (i == 2) "changed" else "identical"))
}
