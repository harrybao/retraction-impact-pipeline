source("R/app.R")
fails <- 0L
ok <- function(label, cond) {
  if (isTRUE(cond)) cat("ok   ", label, "\n")
  else { fails <<- fails + 1L; cat("FAIL ", label, "\n") }
}
rejects <- function(expr) inherits(try(force(expr), silent = TRUE), "try-error")

ok("DOI links, duplicate IDs and record IDs are normalised",
   identical(cs43_app_items("https://doi.org/10.1234/ABC\n10.1234/abc\nrecord:12"), c("10.1234/abc", "record:12")))
ok("punctuation in legacy DOIs is not mistaken for input separators",
   identical(cs43_app_items("10.3157/0013-872x(2007)118[512:ansopb]2.0.co;2"),
             "10.3157/0013-872x(2007)118[512:ansopb]2.0.co;2"))
ok("blank input is rejected", rejects(cs43_app_items(" \n ")))
ok("invalid tokens are retained but cannot become collector arguments",
   identical(cs43_app_items("hello"), "hello") &&
     !any(cs43_app_valid_items(cs43_app_items("system('bad') --help"))))
ok("over-limit input is rejected", rejects(cs43_app_items(paste0("record:", 1:1001, collapse = "\n"))))
ok("over-long input is rejected", rejects(cs43_app_items(paste(rep("x", 30001), collapse = ""))))
ok("credentials and URL query values are redacted",
   identical(cs43_app_redact("hello secret-value https://x?api_key=unknown&ok=1", "secret-value"),
             "hello [REDACTED] https://x?api_key=[REDACTED]&ok=1"))
ok("URL-encoded credentials are redacted", !grepl("%2B", cs43_app_redact("a%2Bb", "a+b"), fixed = TRUE))
ok("report path traversal is refused", !nrow(cs43_app_report(getwd(), "../private.csv")))
ok("all four breakdowns have distinct files", length(unique(vapply(c("reason", "country", "publisher", "discipline"),
   function(g) cs43_app_analysis_files("citations", g), ""))) == 4L)
validation <- data.frame(task = "test", measure = c("items", "Krippendorff alpha, labellers", "model = majority of the same six"), value = c(200, .8, .9))
ok("validation charts do not mix counts and agreement fractions",
   identical(cs43_app_validation_chart(validation, "agreement")$value, .8) &&
     identical(cs43_app_validation_chart(validation, "items")$value, 200) &&
     identical(cs43_app_validation_chart(validation, "model")$value, .9))
ok("citation columns use descriptive English",
   identical(cs43_app_labels(c("t_pre", "c_post", "ratio_mh")),
             c("Retracted papers' citations, before", "Control papers' citations, after", "Matched citation ratio")))
display <- cs43_app_display_values(data.frame(group = c("k = 1 years each side", "k = 2 years each side", NA_character_)))
ok("citation windows have correct singular and plural forms",
   identical(display$group, c("1 year each side", "2 years each side", NA_character_)))
display <- cs43_app_display_values(data.frame(reason = c("R6_paper_mill", "not a DOI", NA_character_)))
ok("reason codes are readable without changing input-validation reasons",
   identical(display$reason, c("Paper mills", "not a DOI", NA_character_)))
ok("uninitialised, hidden and narrow plot widths are safe",
   all(vapply(list(NULL, NA_real_, 0, -1, 240, 800), cs43_app_plot_width, numeric(1)) >= 360))
ok("validation charts leave vertical room for long multi-line labels",
   cs43_app_analysis_height("citations", 12L) == 440L &&
     cs43_app_analysis_height("validation", 12L) >= 12L * 64L &&
     cs43_app_analysis_height("validation", NULL) >= 440L)

scratch <- tempfile("cs43-app-tests-")
dir.create(scratch)
root <- file.path(scratch, "project"); dir.create(file.path(root, "R"), recursive = TRUE)
file.copy("R/app.R", file.path(root, "R", "app.R"))
file.copy("tests/r/fixtures/app_run.R", file.path(root, "run.R"))
writeLines("not a pipeline input", file.path(root, "DO_NOT_COPY"))
dir.create(file.path(root, "data", "clean"), recursive = TRUE)
dir.create(file.path(root, "data", "reports"), recursive = TRUE)
writeLines("original input", file.path(root, "data", "clean", "unchanged.csv"))
writeLines("original report", file.path(root, "data", "reports", "unchanged.csv"))
runs <- file.path(scratch, "runs")
ok("production data cannot be a run directory", rejects(cs43_app_runs_dir(root, file.path(root, "data", "clean", "runs"))))
ok("project ancestor cannot be a run directory", rejects(cs43_app_runs_dir(root, scratch)))
ok("parent traversal cannot bypass the output guard", rejects(cs43_app_runs_dir(root, file.path(root, "runs", "..", "data"))))
ok("sibling output directories are allowed", identical(cs43_app_runs_dir(root, runs), normalizePath(runs, winslash = "/")))
o <- cs43_app_overview(root)
ok("missing source reports are unknown, not zeros", is.na(o$total) && is.na(o$news) && is.na(o$posts))
utils::write.csv(data.frame(section = rep("what the 8 collected documents are", 2),
                            measure = c("MHTML", "not collected"), n = c(8, 2)),
                 file.path(root, "data", "reports", "stage1-rendered-pages.csv"), row.names = FALSE)
o <- cs43_app_overview(root)
ok("overview derives its denominator from the report", o$total == 10 && o$kept == 8 && o$coverage == 80)
utils::write.csv(data.frame(group = paste("k =", 1:3, "years each side"), ratio_mh = c(.5, .4, .3),
  ci_lower = c(.45, .35, .25), ci_upper = c(.55, .45, .35), papers = c(30, 20, 10)),
  file.path(root, "data/reports/citation-impact-headline.csv"), row.names = FALSE)
utils::write.csv(data.frame(period = c("before", "after"), articles = c(50, 40), say_retracted = c(1, 3), share_pct = c(2, 7.5)),
  file.path(root, "data/reports/media-with-notices-by-period.csv"), row.names = FALSE)
utils::write.csv(data.frame(reason = c("R2_image", "R6_paper_mill"), papers = c(8, 10), share_of_papers = c(20, 25)),
  file.path(root, "data/reports/notice-classification-reasons-overall.csv"), row.names = FALSE)
findings <- cs43_app_findings(root)
ok("key findings use the supplied report files and their denominators",
   findings$citations$ratio_mh[1] == .5 && findings$media$articles == 40 && findings$media$share_pct == 7.5 &&
     findings$reasons$reason[1] == "Paper mills")
for (width in c(360L, 800L, 1280L)) {
  grDevices::png(file.path(scratch, paste0("news-", width, ".png")), width = width, height = 340, res = 100)
  plotted <- try(cs43_app_plot(data.frame(measure = c("Altmetric: article", "Altmetric: notice", "All four sources"),
                                        papers = c(12, 9, 18)), "papers"), silent = TRUE)
  grDevices::dev.off()
  ok(paste("news labels are complete and plot renders at", width, "pixels"),
     !inherits(plotted, "try-error") && setequal(gsub("\n", " ", plotted, fixed = TRUE),
                                                 c("Altmetric: article", "Altmetric: notice", "All four sources")))
}
protected <- list.files(file.path(root, "data"), full.names = TRUE, recursive = TRUE)
before <- tools::md5sum(protected)
if (!requireNamespace("shiny", quietly = TRUE) || !requireNamespace("DT", quietly = TRUE))
  stop("Restore Shiny and DT to run dashboard tests")

old_key <- Sys.getenv("ALTMETRIC_API_KEY", unset = NA)
fake_key <- "CS43-synthetic-test-secret-do-not-share"
Sys.setenv(ALTMETRIC_API_KEY = fake_key)
wait_job <- function(j, seconds = 30) {
  deadline <- Sys.time() + seconds
  while (j$status == "running" && Sys.time() < deadline) { cs43_app_poll(j); Sys.sleep(.05) }
  if (j$status == "running") { cs43_app_stop_job(j); stop("Fixture job timed out") }
  j
}
j <- cs43_app_start_job(root, runs, "pipeline", "10.1234/example", FALSE, FALSE)
ok("background process arguments contain no key", !any(grepl(fake_key, j$process$get_cmdline(), fixed = TRUE)))
wait_job(j)
ok("real background Rscript calls run_pipeline", j$status == "completed" && nrow(cs43_app_result(j$dir)) == 1L)
ok("pipeline code is isolated without copying other root files",
   file.exists(file.path(j$dir, "workspace", "run.R")) && !file.exists(file.path(j$dir, "workspace", "DO_NOT_COPY")))
ok("split log secrets are redacted", !any(grepl(fake_key, j$log, fixed = TRUE)) && any(grepl("[REDACTED]", j$log, fixed = TRUE)))
job_files <- list.files(j$dir, full.names = TRUE, recursive = TRUE)
ok("request, log and result files contain no key", !any(vapply(job_files, function(f) any(grepl(fake_key, readLines(f, warn = FALSE), fixed = TRUE)), logical(1))))
mixed_items <- c("10.1234/example", "hello", "10.9999/not-in-csv")
mixed_job <- wait_job(cs43_app_start_job(root, runs, "pipeline", mixed_items, FALSE, FALSE))
mixed <- cs43_app_result(mixed_job$dir)
ok("one bad entry does not prevent valid entries from being collected",
   mixed_job$status == "completed" && identical(mixed$item, mixed_items) &&
     identical(mixed$status, c("done", "not run", "not run")))
ok("invalid and unlisted DOI entries have distinct reasons",
   mixed$reason[mixed$item == "hello"] == "not a DOI" &&
     mixed$reason[mixed$item == "10.9999/not-in-csv"] == "DOI not in the Retraction Watch CSV")
invalid_job <- wait_job(cs43_app_start_job(root, runs, "pipeline", c("hello", "--help"), FALSE, FALSE))
ok("an entirely invalid batch produces not-run rows without invoking a collector",
   invalid_job$status == "completed" && all(cs43_app_result(invalid_job$dir)$status == "not run") &&
     !any(grepl("Fixture running", invalid_job$log, fixed = TRUE)))
compact <- cs43_app_run_summary(mixed)
ok("run table contains only the ten requested summary fields",
   identical(names(compact), CS43_APP_RUN_COLUMNS) && ncol(compact) == 10L &&
     !any(c("csv_title", "extracted_title", "notice_start", "body_chars") %in% names(compact)))
detail <- cs43_app_detail_fields(mixed, 1L)
ok("row details retain titles, notice excerpt and other full-result fields",
   detail[["Retraction Watch title"]] == "Fixture paper title" &&
     grepl("has been retracted", detail[["Retraction notice excerpt"]], fixed = TRUE) &&
     detail[["Body length (characters)"]] == "6000")
ok("invalid row selections do not leak another document's details",
   !length(cs43_app_detail_fields(mixed, 99L)) && !length(cs43_app_detail_fields(mixed, NA_integer_)))
j2 <- wait_job(cs43_app_start_job(root, runs, "pipeline", "record:0", FALSE, FALSE))
ok("worker failures are not displayed as success", j2$status == "failed" && any(grepl("controlled failure", j2$log)))
j3 <- cs43_app_start_job(root, runs, "pipeline", "record:9", FALSE, FALSE)
Sys.sleep(.2); cs43_app_stop_job(j3)
ok("cancellation stops the process tree", j3$status == "cancelled" && !j3$process$is_alive())
j4 <- wait_job(cs43_app_start_job(root, runs, "reproduce"))
ok("isolated reproduction executes all 14 steps without API keys", j4$status == "completed" && nrow(cs43_app_read(file.path(j4$dir, "steps.csv"))) == 14L)
ok("changed results are distinguished from execution failure", sum(cs43_app_result(j4$dir)$outcome == "changed") == 1)
ok("reproduction preserves original inputs and reports", identical(before, tools::md5sum(protected)))
ok("reproduction never copies a production SQLite file", !file.exists(file.path(j4$dir, "workspace", "data", "interim", "pipeline.sqlite")))
hist <- cs43_app_history(runs)
ok("history includes completed, failed and cancelled jobs", nrow(hist) == 6L && all(c("completed", "failed", "cancelled") %in% hist$status))

shiny::testServer(cs43_app_server(root, runs), {
  session$setInputs(topic = "citations", group = "overall", report = "citation-impact-headline.csv", limit = 12)
  session$flushReact()
  ok("server starts without running a worker", is.null(state$pipeline) && is.null(state$reproduce))
  ok("Stop is disabled before a job is started", grepl("disabled", output$pipeline_buttons$html, fixed = TRUE))
  session$setInputs(items = "invalid", start_pipeline = 1, recover = FALSE, media = FALSE, exclude = TRUE)
  session$flushReact()
  wait_job(state$pipeline)
  ok("invalid UI input is represented as a not-run result",
     state$pipeline$status == "completed" && cs43_app_result(state$selected)$status == "not run")
  session$setInputs(group = "country", report = "citation-impact-by-country.csv")
  session$flushReact()
  ok("group changes select only registered files", !nrow(analysis_data()))
  evaluations <- 0L
  observer <- shiny::observe({ analysis_data(); evaluations <<- evaluations + 1L })
  session$flushReact()
  previous <- evaluations
  state$revision <- state$revision + 1L
  session$flushReact()
  ok("worker log polling does not reset analysis tables and filters", evaluations == previous)
  state$selected <- j$dir
  session$flushReact()
  ok("saved run plot renders without calling the DT helper as table", !is.null(output$run_plot))
  session$setInputs(run_results_rows_selected = 1L)
  session$flushReact()
  ok("selected-row detail is displayed outside the compact result table",
     grepl("Fixture paper title", output$run_detail$html, fixed = TRUE) &&
       grepl("Retraction notice excerpt", output$run_detail$html, fixed = TRUE))
  observer$destroy()
})
html <- as.character(cs43_app_ui())
ok("five primary views are present", all(vapply(c("Overview", "Run pipeline", "Analysis results", "Reproduce", "About"),
                                              function(x) grepl(x, html, fixed = TRUE), logical(1))))
ok("both sidebar views stack before their controls become cramped",
   grepl('id="tab-analysis"', html, fixed = TRUE) &&
     grepl("#tab-analysis .col-sm-3,#tab-analysis .col-sm-9{width:100%;float:none}", CS43_APP_CSS, fixed = TRUE))
finding_html <- as.character(cs43_app_findings_ui(root))
ok("reproduction findings show CI, media fraction, notice reasons and source files",
   all(vapply(c("0.500 (0.450 to 0.550)", "7.5%", "Paper mills", "citation-impact-headline.csv",
                "media-with-notices-by-period.csv", "notice-classification-reasons-overall.csv"),
       function(x) grepl(x, finding_html, fixed = TRUE), logical(1))))
ok("reproduction has an analysis navigation button", grepl("view_analysis", html, fixed = TRUE))
ok("remote binding is refused", rejects(cs43_app(host = "0.0.0.0", root = root, runs_dir = runs, run = FALSE)))
if (is.na(old_key)) Sys.unsetenv("ALTMETRIC_API_KEY") else Sys.setenv(ALTMETRIC_API_KEY = old_key)
cat("\n", if (fails) sprintf("%d FAILED", fails) else "all passed", "\n", sep = "")
quit(status = if (fails) 1L else 0L)
