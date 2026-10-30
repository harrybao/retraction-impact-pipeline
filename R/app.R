# Local dashboard. Workers use separate directories; no collector is given data/.
CS43_APP_KEYS <- c("ALTMETRIC_API_KEY", "ELSEVIER_API_KEY", "ELSEVIER_INSTTOKEN",
                   "OPENALEX_API_KEY", "CORE_API_KEY", "NCBI_API_KEY")

cs43_app_inside <- function(path, parent) {
  norm <- function(x) tolower(normalizePath(x, winslash = "/", mustWork = FALSE))
  x <- norm(path); p <- sub("/+$", "", norm(parent))
  identical(x, p) || startsWith(x, paste0(p, "/"))
}

cs43_app_runs_dir <- function(root, runs_dir) {
  root <- normalizePath(root, winslash = "/", mustWork = TRUE)
  if (length(runs_dir) != 1L || is.na(runs_dir) || !nzchar(runs_dir) ||
      grepl("(^|/)[.][.](/|$)", chartr("\\", "/", runs_dir)))
    stop("Choose a run directory without parent-directory traversal.", call. = FALSE)
  # Reject both a protected directory and a parent of the project.
  protected <- file.path(root, c("data", "R", "analysis", "scripts", "src", "tests", "renv"))
  if (cs43_app_inside(root, runs_dir) ||
      any(vapply(protected, function(p) cs43_app_inside(runs_dir, p), logical(1)))) {
    stop("Choose a run directory outside the project's source and data directories.", call. = FALSE)
  }
  dir.create(runs_dir, recursive = TRUE, showWarnings = FALSE)
  normalizePath(runs_dir, winslash = "/", mustWork = TRUE)
}

cs43_app_items <- function(text) {
  if (length(text) != 1L || is.na(text) || nchar(text) > 30000L)
    stop("Enter at most 1,000 DOIs or record IDs (30,000 characters).", call. = FALSE)
  # Semicolons, commas and angle brackets occur in real (notably SICI) DOIs.
  x <- strsplit(trimws(text), "[[:space:]]+")[[1]]
  x <- unique(tolower(sub("^https?://(dx[.])?doi[.]org/", "", x, ignore.case = TRUE)))
  x <- sub("^doi:", "", x)
  x <- x[nzchar(x)]
  if (!length(x) || length(x) > 1000L || sum(nchar(x)) + length(x) > 30000L)
    stop("Enter between 1 and 1,000 DOIs or record IDs.", call. = FALSE)
  x
}

cs43_app_valid_items <- function(items) {
  grepl("^(10[.][0-9]{4,9}/[^[:space:]]+|record:[0-9]+)$", items)
}

cs43_app_merge_results <- function(result, items) {
  if (!"item" %in% names(result)) result$item <- if ("doi" %in% names(result)) result$doi else character(nrow(result))
  missing <- items[!cs43_app_valid_items(items) & !items %in% result$item]
  required <- c("item", "doi", "page", "status", "quality", "obtained_via", "reason",
                "openalex_citations", "altmetric_posts", "altmetric_news", "verified")
  for (name in setdiff(required, names(result))) result[[name]] <- rep(NA_character_, nrow(result))
  if (length(missing)) {
    start <- nrow(result)
    for (i in seq_along(missing)) {
      row <- start + i
      result[row, ] <- NA
      result$item[row] <- missing[i]
      result$status[row] <- "not run"
      result$reason[row] <- "not a DOI"
    }
  }
  result[order(match(result$item, items)), , drop = FALSE]
}

CS43_APP_RUN_COLUMNS <- c("item", "doi", "page", "status", "quality", "obtained_via", "reason",
                          "openalex_citations", "altmetric_posts", "altmetric_news")

cs43_app_run_summary <- function(result) {
  for (name in setdiff(CS43_APP_RUN_COLUMNS, names(result))) result[[name]] <- rep(NA_character_, nrow(result))
  result[CS43_APP_RUN_COLUMNS]
}

cs43_app_detail_fields <- function(result, row = 1L) {
  if (length(row) != 1L || is.na(row) || row < 1L || row > nrow(result)) return(character())
  fields <- setdiff(names(result), CS43_APP_RUN_COLUMNS)
  values <- vapply(result[row, fields, drop = FALSE], function(x) {
    if (is.na(x) || !nzchar(as.character(x))) "Not available" else as.character(x)
  }, "")
  stats::setNames(values, cs43_app_labels(fields))
}

cs43_app_secrets <- function(env = Sys.getenv()) {
  x <- unname(env[grepl("KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", names(env), ignore.case = TRUE)])
  unique(x[!is.na(x) & nzchar(x)])
}

cs43_app_redact <- function(x, secrets = cs43_app_secrets()) {
  x <- as.character(x)
  x <- gsub("\033\\[[0-9;?]*[A-Za-z]", "", x, perl = TRUE)
  for (key in secrets[order(nchar(secrets), decreasing = TRUE)]) {
    variants <- unique(c(key, utils::URLencode(key, reserved = TRUE),
                         gsub("\"", "\\\"", key, fixed = TRUE)))
    for (v in variants) x <- gsub(v, "[REDACTED]", x, fixed = TRUE)
  }
  x <- gsub("(?i)([?&](?:api[_-]?key|key|token|insttoken|access_token)=)[^&[:space:]\"<>]+",
            "\\1[REDACTED]", x, perl = TRUE)
  x <- gsub("(?i)(authorization[=: ]+)(?:bearer[ ]+)?[^[:space:]\"<>]+",
            "\\1[REDACTED]", x, perl = TRUE)
  x
}

cs43_app_clean_table <- function(x, secrets = cs43_app_secrets()) {
  for (n in names(x)) if (is.character(x[[n]])) x[[n]] <- cs43_app_redact(x[[n]], secrets)
  x
}

cs43_app_read <- function(path) {
  if (!file.exists(path)) return(data.frame())
  tryCatch(utils::read.csv(path, stringsAsFactors = FALSE, check.names = FALSE,
                           fileEncoding = "UTF-8-BOM"), error = function(e) data.frame())
}

cs43_app_report <- function(root, name) {
  if (length(name) != 1L || !grepl("^[a-zA-Z0-9_-]+[.]csv$", name)) return(data.frame())
  cs43_app_read(file.path(root, "data", "reports", name))
}

cs43_app_num <- function(x) suppressWarnings(as.numeric(x))
cs43_app_number <- function(x) {
  if (length(x) != 1L || !is.finite(x)) return("Not available")
  format(round(x), big.mark = ",", scientific = FALSE, trim = TRUE)
}

cs43_app_overview <- function(root) {
  pages <- cs43_app_report(root, "stage1-rendered-pages.csv")
  routes <- if (all(c("section", "measure", "n") %in% names(pages)))
    pages[startsWith(pages$section, "what the "), , drop = FALSE] else data.frame()
  total <- kept <- NA_real_
  if (nrow(routes) && !anyNA(cs43_app_num(routes$n)) && sum(routes$measure == "not collected") == 1L) {
    total <- sum(cs43_app_num(routes$n))
    kept <- total - cs43_app_num(routes$n[routes$measure == "not collected"])
  }
  news <- cs43_app_report(root, "news-coverage-union.csv")
  news_n <- if (all(c("measure", "papers") %in% names(news)) && sum(news$measure == "ALL FOUR") == 1L)
    cs43_app_num(news$papers[news$measure == "ALL FOUR"]) else NA_real_
  social <- cs43_app_report(root, "social-media-source-universe.csv")
  posts <- if ("posts" %in% names(social) && nrow(social) && !anyNA(cs43_app_num(social$posts)))
    sum(cs43_app_num(social$posts)) else NA_real_
  list(total = total, kept = kept, coverage = if (is.finite(total) && total > 0) 100 * kept / total else NA_real_,
       news = news_n, posts = posts, routes = routes, news_table = news, social = social)
}

cs43_app_analysis_files <- function(topic, group = "overall") {
  grouped <- c(reason = "reason", country = "country", publisher = "publisher", discipline = "discipline")
  if (topic == "citations") {
    if (group %in% names(grouped)) return(paste0("citation-impact-by-", grouped[[group]], ".csv"))
    return(c("citation-impact-headline.csv", "citation-impact-event-study.csv", "citation-impact-placebo.csv",
             "citation-impact-robustness.csv", "citation-impact-funnel.csv"))
  }
  if (topic == "media") {
    if (group %in% names(grouped)) return(paste0("media-by-", grouped[[group]], ".csv"))
    return(c("media-with-notices.csv", "media-with-notices-by-kind.csv", "news-coverage-union.csv",
             "social-media-source-universe.csv", "social-media-after-by-kind.csv",
             "social-media-validated-vs-not.csv"))
  }
  if (topic == "notices") {
    if (group %in% names(grouped)) return(paste0("notice-classification-by-", grouped[[group]], ".csv"))
    return(c("notice-classification-reasons-overall.csv", "notice-classification-clarity-overall.csv"))
  }
  c("llm-validation.csv", "labelling-agreement.csv", "labelling-round2-model.csv", "labelling-round2.csv")
}

cs43_app_metrics <- function(d) {
  names(d)[vapply(d, function(x) is.numeric(x) && any(is.finite(x)), logical(1))]
}

cs43_app_labels <- function(x) {
  known <- c(ratio_mh = "Matched citation ratio", ratio_pooled = "Pooled citation ratio", n = "Documents",
             Freq = "Count", kind = "Source", with_text = "Posts with text", share_pct = "Share (%)",
             share_of_papers = "Share of papers (%)", papers_ever_told_pct = "Papers acknowledging retraction (%)",
             mentions_told_pct = "Mentions acknowledging retraction (%)", ci_lower = "Lower confidence limit",
             ci_upper = "Upper confidence limit", says_retracted = "Acknowledgement validation",
             five_categories = "Five-category validation", R0_no_reason = "No reason given (%)",
             item = "Entry", doi = "DOI", page = "Page type", status = "Status", quality = "Text quality",
             obtained_via = "Obtained via", reason = "Reason", openalex_citations = "Citations",
             altmetric_posts = "Altmetric posts", altmetric_news = "Altmetric news",
             csv_title = "Title on record (Retraction Watch or Crossref)", extracted_title = "Extracted title",
             notice_start = "Retraction notice excerpt", notice_chars = "Notice length (characters)",
             body_chars = "Body length (characters)", http_status = "HTTP status", verified = "Identity verified",
             final_host = "Final website", capture_format = "Capture format", our_dataset = "Held in the research dataset",
             records = "Linked records", journal = "Journal", publisher = "Publisher",
             altmetric_record = "Altmetric record", plumx_record = "PlumX record", plumx_news = "PlumX news",
             notices = "Notices", posts = "Posts", section = "Section", script = "Analysis script",
             what = "Analysis", seconds = "Time (seconds)", log = "Log file", file = "Result file",
             written_this_run = "Generated in this run", outcome = "Comparison result",
             t_pre = "Retracted papers' citations, before", t_post = "Retracted papers' citations, after",
             c_pre = "Control papers' citations, before", c_post = "Control papers' citations, after",
             strata = "Matched groups", controls = "Control papers", papers = "Papers",
             treated_change = "Retracted papers' citation growth", control_change = "Control papers' citation growth",
             naive_wrong = "Unadjusted mean ratio (not used)", citations_lost = "Estimated citation shortfall",
             real_effect = "Observed citation ratio", effect_net_of_drift = "Trend-adjusted citation ratio",
             k = "Years each side", group = "Comparison", value = "Value", task = "Task", measure = "Measure",
             scope = "Scope", model = "Model", articles = "Media items", say_retracted = "Items acknowledging retraction",
             R1_fabrication = "Fabrication or falsification (%)", R2_image = "Image problems (%)",
             R3_plagiarism = "Plagiarism (%)", R4_duplicate = "Duplicate publication (%)",
             R5_peer_review = "Peer-review manipulation (%)", R6_paper_mill = "Paper mills (%)",
             R7_honest_error = "Honest error (%)", R8_authorship_ethics = "Authorship or ethics (%)",
             R9_other = "Other stated reasons (%)")
  out <- gsub("_", " ", x, fixed = TRUE)
  hit <- x %in% names(known)
  out[hit] <- unname(known[x[hit]])
  out
}

cs43_app_display_values <- function(d) {
  for (name in names(d)) if (is.character(d[[name]])) {
    hit <- grepl("^k = [0-9]+ years each side$", d[[name]]) & !is.na(d[[name]])
    years <- as.integer(sub("^k = ([0-9]+) years each side$", "\\1", d[[name]][hit]))
    d[[name]][hit] <- paste(years, ifelse(years == 1L, "year each side", "years each side"))
    if (name == "reason") {
      codes <- !is.na(d[[name]]) & grepl("^R[0-9]_[a-z_]+$", d[[name]])
      d[[name]][codes] <- sub(" [(]%[)]$", "", cs43_app_labels(d[[name]][codes]))
    }
  }
  d
}

cs43_app_findings <- function(root) {
  citations <- cs43_app_report(root, "citation-impact-headline.csv")
  needed <- c("group", "ratio_mh", "ci_lower", "ci_upper", "papers")
  if (all(needed %in% names(citations))) citations <- cs43_app_display_values(citations[needed]) else citations <- data.frame()
  media <- cs43_app_report(root, "media-with-notices-by-period.csv")
  if (all(c("period", "articles", "say_retracted", "share_pct") %in% names(media)))
    media <- media[media$period == "after", , drop = FALSE] else media <- data.frame()
  reasons <- cs43_app_report(root, "notice-classification-reasons-overall.csv")
  if (all(c("reason", "papers", "share_of_papers") %in% names(reasons))) {
    reasons <- utils::head(reasons[order(-cs43_app_num(reasons$papers)), ], 5L)
    reasons$reason <- sub(" [(]%[)]$", "", cs43_app_labels(reasons$reason))
  } else reasons <- data.frame()
  list(citations = citations, media = media, reasons = reasons)
}

cs43_app_plot_width <- function(width) {
  if (length(width) != 1L || !is.finite(width)) return(360L)
  max(360L, as.integer(width))
}

cs43_app_analysis_height <- function(topic, groups) {
  if (!identical(topic, "validation")) return(440L)
  if (length(groups) != 1L || !is.finite(groups)) groups <- 12L
  max(440L, min(30L, max(1L, as.integer(groups))) * 64L + 80L)
}

cs43_app_validation_chart <- function(d, family = "agreement") {
  if (!nrow(d) || !"measure" %in% names(d)) return(data.frame())
  if (length(family) != 1L) family <- "agreement"
  keep <- switch(family, agreement = grepl("alpha|kappa", d$measure, ignore.case = TRUE),
                 model = grepl("^model = majority", d$measure), items = d$measure == "items",
                 rep(FALSE, nrow(d)))
  d <- d[keep, , drop = FALSE]
  scope <- if ("task" %in% names(d)) d$task else if ("scope" %in% names(d)) d$scope else rep("", nrow(d))
  if ("model" %in% names(d)) scope <- paste(d$model, scope, sep = ": ")
  # A chart must not compare sample counts to agreement/accuracy fractions.
  data.frame(label = paste(scope, d$measure, sep = ": "), value = cs43_app_num(d$value))
}

cs43_app_plot <- function(d, metric, title = "", limit = 12L, reference = NULL) {
  if (!nrow(d) || length(metric) != 1L || !metric %in% names(d)) {
    graphics::plot.new(); graphics::text(.5, .5, "No data available"); return(invisible(NULL))
  }
  d <- cs43_app_display_values(d)
  values <- cs43_app_num(d[[metric]])
  label_cols <- names(d)[!vapply(d, is.numeric, logical(1))]
  label_col <- if ("section" %in% label_cols && "measure" %in% label_cols) "measure"
               else if (length(label_cols)) label_cols[1] else names(d)[1]
  labels <- as.character(d[[label_col]])
  if ("measure" %in% names(d) && label_col != "measure") labels <- paste(labels, d$measure, sep = ": ")
  take <- which(is.finite(values))
  take <- utils::head(take, limit)
  if (!length(take)) {
    graphics::plot.new(); graphics::text(.5, .5, "No numeric observations"); return(invisible(NULL))
  }
  old <- graphics::par(c("mai", "family", "fg", "col.axis"))
  on.exit(graphics::par(old))
  graphics::par(family = "sans", fg = "#303a38", col.axis = "#64706b")
  v <- rev(values[take]); lab <- rev(labels[take])
  wrap <- max(18L, min(35L, floor((graphics::par("din")[1] - 1.5) * 10)))
  lab <- vapply(lab, function(x) paste(strwrap(x, width = wrap), collapse = "\n"), "")
  lines <- unlist(strsplit(lab, "\n", fixed = TRUE))
  left <- max(1, max(graphics::strwidth(lines, units = "inches", cex = .78)) + .4)
  left <- min(left, graphics::par("din")[1] - 1.3)
  graphics::par(mai = c(.6, left, .25, .15))
  top <- max(c(v, reference, if (metric == "ratio_mh") cs43_app_num(d$ci_upper[take]), 0), na.rm = TRUE)
  lo <- min(c(v, 0), na.rm = TRUE)
  pos <- graphics::barplot(v, names.arg = lab, horiz = TRUE, las = 1, border = NA,
                           col = rep(c("#237b68", "#6286a2", "#a68148"), length.out = length(v)),
                           cex.names = .78, xlim = c(lo, if (top == 0) 1 else top * 1.15), axes = FALSE,
                           xlab = cs43_app_labels(metric), main = title)
  ticks <- pretty(c(lo, if (top == 0) 1 else top), n = 3)
  graphics::axis(1, at = ticks, labels = format(ticks, big.mark = ",", scientific = FALSE, trim = TRUE), cex.axis = .8)
  if (!is.null(reference)) graphics::abline(v = reference, col = "#b05750", lty = 2)
  if (metric == "ratio_mh" && all(c("ci_lower", "ci_upper") %in% names(d))) {
    lower <- rev(cs43_app_num(d$ci_lower[take])); upper <- rev(cs43_app_num(d$ci_upper[take]))
    have <- is.finite(lower) & is.finite(upper)
    graphics::segments(lower[have], pos[have], upper[have], pos[have], lwd = 1.5)
  }
  invisible(lab)
}

# The request contains inputs and paths only. Credentials exist only in env.
cs43_app_start_job <- function(root, runs_dir, kind = c("pipeline", "reproduce"), items = character(),
                                recover = TRUE, media = TRUE, exclude = TRUE) {
  kind <- match.arg(kind)
  runs_dir <- cs43_app_runs_dir(root, runs_dir)
  if (kind == "pipeline") items <- cs43_app_items(paste(items, collapse = "\n"))
  job_dir <- tempfile(paste0(format(Sys.time(), "%Y%m%d-%H%M%S"), "-", kind, "-"), tmpdir = runs_dir)
  dir.create(job_dir)
  spec <- list(kind = kind, root = normalizePath(root, winslash = "/"), job_dir = job_dir,
               items = items, recover = isTRUE(recover), media = isTRUE(media), exclude = isTRUE(exclude),
               libraries = .libPaths(), created = format(Sys.time(), "%Y-%m-%d %H:%M:%S %Z"))
  request <- file.path(job_dir, "request.json")
  jsonlite::write_json(spec, request, auto_unbox = TRUE, pretty = TRUE)
  secret <- cs43_app_secrets()
  # R --vanilla does not read .Renviron or .Rprofile. Clear inherited output
  # redirects so nested Python/R processes cannot select the production data.
  env <- c("current", CS43_PIPELINE = spec$root, CS43_REPORTS = NA_character_,
           RWPIPE_DATA = file.path(job_dir, "data"), R_PROFILE_USER = NA_character_,
           R_ENVIRON_USER = NA_character_, PYTHON_DOTENV_DISABLED = "1", PYTHONUNBUFFERED = "1")
  chosen <- getOption("cs43.python", Sys.getenv("CS43_PYTHON", ""))
  if (nzchar(chosen)) env <- c(env, CS43_PYTHON = chosen)
  if (kind == "reproduce") {
    env <- c(env, CS43_LLM_HOST = "http://127.0.0.1:9", CS43_LABELS_ROUND1 = NA_character_,
             CS43_LABELS_ROUND2 = NA_character_)
    for (name in unique(c(CS43_APP_KEYS, names(Sys.getenv())[grepl("KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL",
                                                                  names(Sys.getenv()), ignore.case = TRUE)])))
      env <- c(env, stats::setNames(NA_character_, name))
  }
  boot <- paste0("q<-jsonlite::read_json(commandArgs(TRUE)[1],simplifyVector=TRUE);",
                 ".libPaths(q$libraries);source(file.path(q$root,'R','app.R'));",
                 "cs43_app_worker(commandArgs(TRUE)[1])")
  # jsonlite must be on the child library path before the bootstrap reads q.
  env <- c(env, R_LIBS = paste(.libPaths(), collapse = .Platform$path.sep))
  if (.Platform$OS.type == "windows")
    env <- c(env, LC_ALL = NA_character_, LC_CTYPE = "English_United States.utf8")
  # processx 3.9 rejects NA removals mixed with the unnamed "current" entry.
  overrides <- env[nzchar(names(env))]
  inherited <- Sys.getenv()
  env <- c(inherited[!names(inherited) %in% names(overrides)], overrides)
  env <- env[!duplicated(names(env), fromLast = TRUE) & !is.na(env)]
  p <- processx::process$new(file.path(R.home("bin"), if (.Platform$OS.type == "windows") "Rscript.exe" else "Rscript"),
                             c("--vanilla", "-e", boot, request), wd = root, env = env,
                             stdout = "|", stderr = "|", cleanup_tree = TRUE,
                             windows_hide_window = TRUE, encoding = "UTF-8")
  job <- new.env(parent = emptyenv())
  job$process <- p; job$dir <- job_dir; job$spec <- spec; job$secrets <- secret
  job$pending <- c(output = "", error = ""); job$log <- character(); job$status <- "running"
  job$progress <- 0L; job$phase <- "Starting"; job
}

cs43_app_poll <- function(job) {
  if (is.null(job) || job$status != "running") return(invisible(job))
  p <- job$process
  p$poll_io(0)
  alive <- p$is_alive()
  for (stream in c("output", "error")) {
    chunk <- if (!alive) {
      if (stream == "output") p$read_all_output() else p$read_all_error()
    } else if (stream == "output") p$read_output() else p$read_error()
    text <- paste0(job$pending[[stream]], chunk)
    # Retain partial lines: a key split over two polls must never be emitted.
    lines <- strsplit(paste0(text, "\001"), "\n", fixed = TRUE)[[1]]
    last <- sub("\001$", "", utils::tail(lines, 1))
    done <- utils::head(lines, -1)
    if (!alive && nzchar(last)) { done <- c(done, last); last <- "" }
    if (nchar(last) > 1e6) { done <- c(done, "[Oversized log line omitted]"); last <- "" }
    job$pending[[stream]] <- last
    if (length(done)) {
      clean <- cs43_app_redact(gsub("\r", "", done, fixed = TRUE), job$secrets)
      job$log <- utils::tail(c(job$log, clean), 1200L)
      cat(paste0(clean, "\n"), file = file.path(job$dir, "console.log"), append = TRUE, sep = "")
      markers <- regmatches(clean, regexec("^CS43_PROGRESS ([0-9]+)/([0-9]+) (.*)$", clean))
      for (m in markers) if (length(m)) {
        job$progress <- round(100 * as.numeric(m[2]) / as.numeric(m[3])); job$phase <- m[4]
      }
    }
  }
  if (!alive) {
    code <- p$get_exit_status()
    job$status <- if (identical(code, 0L)) "completed" else "failed"
    if (job$status == "completed") job$progress <- 100L
    meta <- list(status = job$status, exit_status = code, finished = format(Sys.time(), "%Y-%m-%d %H:%M:%S %Z"))
    jsonlite::write_json(meta, file.path(job$dir, "status.json"), auto_unbox = TRUE)
  }
  invisible(job)
}

cs43_app_stop_job <- function(job) {
  if (!is.null(job) && identical(job$status, "running")) {
    cs43_app_poll(job)
    try(job$process$kill_tree(), silent = TRUE)
    job$process$wait(2000)
    # kill_tree() closes the pipes in processx 3.9. Discard any unfinished
    # line rather than publishing a possibly truncated credential.
    job$pending <- c(output = "", error = "")
    job$status <- "cancelled"
    jsonlite::write_json(list(status = "cancelled", finished = as.character(Sys.time())),
                         file.path(job$dir, "status.json"), auto_unbox = TRUE)
  }
  invisible(job)
}

cs43_app_copy_inputs <- function(root, sandbox) {
  dir.create(sandbox, recursive = TRUE, showWarnings = FALSE)
  # Copies, not hard links or junctions: analyses may update caches beside inputs.
  paths <- c("R", "analysis", "run.R", "requirements.txt", "data/reports_reference.csv",
             "data/clean", "data/controls", "data/llm", "data/labelling", "data/reports",
             "data/share/publisher_families.csv")
  for (rel in paths) {
    src <- file.path(root, rel)
    if (!file.exists(src)) next
    dst <- file.path(sandbox, rel)
    dir.create(dirname(dst), recursive = TRUE, showWarnings = FALSE)
    ok <- file.copy(src, dirname(dst), recursive = dir.exists(src), copy.mode = FALSE, copy.date = TRUE)
    if (!all(ok)) stop("Could not copy reproduction input: ", rel, call. = FALSE)
  }
  if (file.exists(file.path(sandbox, "data", "interim", "pipeline.sqlite")))
    stop("The reproduction workspace must not contain a production database.", call. = FALSE)
  sandbox
}

cs43_app_copy_source <- function(root, sandbox) {
  dir.create(sandbox, recursive = TRUE, showWarnings = FALSE)
  # Explicit allowlist: notably no .env, .Renviron, .Rprofile or production DB.
  for (rel in c("R", "scripts", "src", "run.R", "requirements.txt")) {
    src <- file.path(root, rel)
    if (file.exists(src) && !all(file.copy(src, sandbox, recursive = dir.exists(src), copy.mode = FALSE)))
      stop("Could not copy pipeline source: ", rel, call. = FALSE)
  }
  sandbox
}

cs43_app_worker <- function(request) {
  q <- jsonlite::read_json(request, simplifyVector = TRUE)
  secret <- cs43_app_secrets()
  progress <- function(i, total, label) { cat(sprintf("CS43_PROGRESS %d/%d %s\n", i, total, label)); flush.console() }
  clean_write <- function(x, name) utils::write.csv(cs43_app_clean_table(x, secret),
                                                   file.path(q$job_dir, name), row.names = FALSE, fileEncoding = "UTF-8")
  status <- tryCatch({
    if (q$kind == "pipeline") {
      sandbox <- cs43_app_copy_source(q$root, file.path(q$job_dir, "workspace"))
      setwd(sandbox); Sys.setenv(CS43_PIPELINE = sandbox); source("run.R")
      raw_csv <- c(Sys.getenv("RWPIPE_CSV", ""), file.path(q$root, "data/raw/retraction_watch.csv"),
                   file.path(dirname(q$root), "Data/retraction_watch.csv"))
      raw_csv <- raw_csv[nzchar(raw_csv) & file.exists(raw_csv)]
      progress(1, 5, "Collecting and verifying pages")
      valid <- q$items[cs43_app_valid_items(q$items)]
      # Invalid tokens are results, never shell/CLI options or queued requests.
      res <- cs43_app_merge_results(data.frame(), q$items)
      clean_write(res, "results.csv")
      if (length(valid)) res <- run_pipeline(valid, csv = if (length(raw_csv)) raw_csv[1] else NULL,
                           data_dir = file.path(q$job_dir, "data"),
                           reference = file.path(q$root, "data", "clean"), retry_after = 0,
                           recover = q$recover, media = q$media, exclude = if (q$exclude) "twitter" else NULL)
      steps <- attr(res, "steps")
      res <- cs43_app_merge_results(res, q$items)
      clean_write(res, "results.csv")
      clean_write(steps, "steps.csv")
      progress(5, 5, "Pipeline finished")
    } else {
      progress(0, 14, "Copying inputs into an isolated workspace")
      sandbox <- cs43_app_copy_inputs(q$root, file.path(q$job_dir, "workspace"))
      setwd(sandbox); Sys.setenv(CS43_PIPELINE = sandbox, CS43_LLM_HOST = "http://127.0.0.1:9")
      Sys.unsetenv(c("CS43_REPORTS", "RWPIPE_DATA")); source("run.R")
      steps <- list(); reports <- list()
      # Use the existing runner, once per registered step, for truthful progress.
      for (i in seq_along(REPRODUCE_STEPS)) {
        label <- names(REPRODUCE_STEPS)[i]
        progress(i - 1L, length(REPRODUCE_STEPS), label)
        r <- reproduce_results(steps = label)
        steps[[i]] <- r$steps
        reports[[i]] <- r$reports[r$reports$written_this_run %in% TRUE, , drop = FALSE]
        clean_write(do.call(rbind, steps), "steps.csv")
        combined <- do.call(rbind, reports)
        combined <- combined[!duplicated(combined$file, fromLast = TRUE), , drop = FALSE]
        clean_write(combined, "results.csv")
      }
      progress(14, 14, "Reproduction finished")
      if (any(do.call(rbind, steps)$status != "ok")) stop("One or more analysis steps failed; see the step table.", call. = FALSE)
    }
    0L
  }, error = function(e) {
    cat("Job failed: ", cs43_app_redact(conditionMessage(e), secret), "\n", sep = "", file = stderr())
    1L
  })
  quit(save = "no", status = status)
}

cs43_app_history <- function(runs_dir) {
  dirs <- list.dirs(runs_dir, recursive = FALSE, full.names = TRUE)
  rows <- lapply(dirs, function(d) {
    tryCatch({
      req <- jsonlite::read_json(file.path(d, "request.json"), simplifyVector = TRUE)
      status <- if (file.exists(file.path(d, "status.json")))
        jsonlite::read_json(file.path(d, "status.json"))$status else "interrupted / active session"
      data.frame(id = basename(d), kind = req$kind, created = req$created, status = status,
                 items = length(req$items), stringsAsFactors = FALSE)
    }, error = function(e) NULL)
  })
  if (!length(Filter(Negate(is.null), rows))) return(data.frame())
  x <- do.call(rbind, rows); x[order(x$created, decreasing = TRUE), , drop = FALSE]
}

cs43_app_result <- function(dir, secrets = cs43_app_secrets()) {
  if (is.null(dir) || !nzchar(dir)) return(data.frame())
  cs43_app_clean_table(cs43_app_read(file.path(dir, "results.csv")), secrets)
}

cs43_app_stamp <- function(dir, name) {
  if (is.null(dir)) return("")
  path <- file.path(dir, name)
  info <- file.info(path)
  paste(path, info$size, as.numeric(info$mtime), collapse = "|")
}

CS43_APP_CSS <- paste0(
  ":root{--ink:#25312d;--muted:#67736d;--line:#dde4df;--green:#237b68;}",
  "*{box-sizing:border-box;letter-spacing:0}body{background:#f7f9f7;color:var(--ink);",
  "font-family:'Segoe UI',Arial,sans-serif;font-size:14px}.navbar{border:0;border-bottom:1px solid var(--line);",
  "background:#fff;margin-bottom:0}.navbar-brand{font-weight:700;color:var(--ink)!important;font-size:18px}",
  ".navbar-default .navbar-nav>.active>a{background:#eaf3ee;color:#17634f;border-bottom:3px solid var(--green)}",
  ".container-fluid>.tab-content{max-width:1440px;margin:auto;padding:28px 22px 50px}",
  "h1{font-size:27px;font-weight:650;margin:0 0 8px}h2{font-size:19px;margin:0 0 16px}",
  "h3{font-size:16px;margin:20px 0 12px}.subhead{color:var(--muted);margin-bottom:24px}",
  ".metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin:20px 0 28px}",
  ".metric{background:#fff;border:1px solid var(--line);border-top:3px solid var(--green);border-radius:6px;padding:18px}",
  ".metric:nth-child(2){border-top-color:#6286a2}.metric:nth-child(3){border-top-color:#a68148}",
  ".metric:nth-child(4){border-top-color:#aa6760}.metric-value{font-size:29px;font-weight:650;font-variant-numeric:tabular-nums}",
  ".metric-label{font-size:13px;color:var(--muted);margin-top:4px}.metric-detail{font-size:12px;color:var(--muted);margin-top:8px}",
  ".band{border-top:1px solid var(--line);padding-top:22px;margin-top:24px}.well{background:#eef3ef;border:0;box-shadow:none;border-radius:6px}",
  ".btn{border-radius:4px}.btn-primary{background:var(--green);border-color:var(--green)}",
  ".btn-primary:hover{background:#195c4d;border-color:#195c4d}.btn[disabled]{opacity:.4}",
  ".form-control{border-color:#cbd6ce;border-radius:4px;box-shadow:none}label{font-weight:600;font-size:13px}",
  ".shiny-plot-output{background:#fff;max-width:100%}.source-line{color:var(--muted);font-size:12px;margin:10px 0 18px;overflow-wrap:anywhere}",
  ".overview-charts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px}.overview-charts>div{min-width:0}",
  ".overview-charts .shiny-plot-output{min-height:340px;width:100%}",
  "#run_results table.dataTable{min-width:1400px}#run_results td,#run_results th{overflow-wrap:normal;word-break:normal}",
  "#run_results td{min-width:100px}#run_results th{white-space:nowrap}#run_results tr.selected{background:#dceee5}",
  ".document-details{display:grid;grid-template-columns:minmax(140px,210px) minmax(0,1fr);gap:10px 18px;max-width:100%}",
  ".document-details dt{font-size:13px;color:var(--muted)}.document-details dd{overflow-wrap:anywhere;margin:0;line-height:1.6}",
  ".finding-table{width:100%;border-collapse:collapse;margin:14px 0}.finding-table th,.finding-table td{padding:9px;border-bottom:1px solid var(--line);text-align:left}",
  ".finding-table th{font-size:13px;color:var(--muted)}.finding-table td{font-variant-numeric:tabular-nums}",
  "pre.log{background:#202925;color:#d9e5df;border:0;border-radius:6px;min-height:190px;max-height:340px;overflow:auto;white-space:pre-wrap;font-size:12px}",
  ".status-row{display:flex;gap:16px;align-items:center;margin:15px 0;flex-wrap:wrap}.status-pill{background:#e5eee8;color:#235b46;padding:5px 10px;border-radius:4px}",
  ".progress{margin:12px 0;height:8px;box-shadow:none}.progress-bar{background:#237b68}",
  ".table-responsive{overflow-x:auto}table.dataTable{font-size:13px}table.dataTable td{overflow-wrap:anywhere}",
  "table.dataTable td.dt-right{white-space:nowrap;overflow-wrap:normal}",
  ".notice{padding:12px 15px;border-left:3px solid #b18b4c;background:#fbf5e8;color:#6f562d;margin:18px 0}",
  ".about{max-width:850px}.about p,.about li{line-height:1.8}.controls{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0}",
  "@media(max-width:1100px){.overview-charts{grid-template-columns:minmax(0,1fr)}}",
  "@media(max-width:991px){#tab-pipeline .col-sm-3,#tab-pipeline .col-sm-9,#tab-analysis .col-sm-3,#tab-analysis .col-sm-9{width:100%;float:none}}",
  "@media(max-width:767px){.metrics{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.metric{padding:13px}",
  ".document-details{grid-template-columns:minmax(0,1fr);gap:5px}.document-details dd{margin-bottom:12px}",
  ".metric-value{font-size:24px}.container-fluid>.tab-content{padding:22px 12px}.navbar-brand{font-size:16px}}",
  "@media(max-width:380px){.metrics{grid-template-columns:minmax(0,1fr)}}")

cs43_app_shiny <- function() {
  exports <- getNamespaceExports("shiny")
  stats::setNames(lapply(exports, function(n) getExportedValue("shiny", n)), exports)
}

cs43_app_findings_ui <- function(root) {
  s <- cs43_app_shiny()
  findings <- cs43_app_findings(root)
  source <- function(file) s$p(class = "source-line", "Source: ", s$code(paste0("data/reports/", file)))
  small_table <- function(d) s$tags$table(class = "finding-table",
    s$tags$thead(s$tags$tr(lapply(names(d), s$tags$th))),
    s$tags$tbody(lapply(seq_len(nrow(d)), function(i) s$tags$tr(lapply(d[i, ], s$tags$td)))))
  number <- function(x) ifelse(is.finite(x), sprintf("%.3f", x), "Not available")
  citations <- findings$citations
  citation_ui <- if (nrow(citations)) small_table(data.frame(
    "Window" = citations$group,
    "Citation ratio (95% CI)" = paste0(number(citations$ratio_mh), " (", number(citations$ci_lower), " to ", number(citations$ci_upper), ")"),
    "Papers" = vapply(citations$papers, cs43_app_number, ""), check.names = FALSE))
    else s$p("Citation results are not available for this run.")
  media <- findings$media
  media_ui <- if (nrow(media) == 1L && is.finite(cs43_app_num(media$share_pct)))
    s$p(s$strong(sprintf("%.1f%%", media$share_pct)), " of post-event media items acknowledge the status: ",
      cs43_app_number(media$say_retracted), " of ", cs43_app_number(media$articles), " items (article and notice DOIs combined).")
    else s$p("Post-event media results are not available for this run.")
  reasons <- findings$reasons
  reasons_ui <- if (nrow(reasons)) small_table(data.frame(
    "Reason" = reasons$reason, "Papers" = vapply(reasons$papers, cs43_app_number, ""),
    "Share of papers" = sprintf("%.1f%%", reasons$share_of_papers), check.names = FALSE))
    else s$p("Notice-reason results are not available for this run.")
  s$div(class = "band", s$h2("Key findings"),
    s$p(class = "source-line", "Read from this reproduction's report CSVs in its isolated workspace."),
    s$h3("Citation changes relative to matched controls"), citation_ui,
    s$p("The groups have different eligible samples. These ratios are not pure causal effects."),
    source("citation-impact-headline.csv"),
    s$h3("Media acknowledging the retraction after the event"), media_ui,
    s$p("Model-labelled acknowledgement includes withdrawal or concern language. Not every tracked source has been validated."),
    source("media-with-notices-by-period.csv"),
    s$h3("Most frequent notice reasons"), reasons_ui,
    s$p("Reasons are multi-label; percentages need not sum to 100%."),
    source("notice-classification-reasons-overall.csv"))
}

cs43_app_ui <- function() {
  s <- cs43_app_shiny()
  s$navbarPage("CS-43 | Retraction Observatory", id = "page", collapsible = TRUE,
    header = s$tags$head(s$tags$style(s$HTML(CS43_APP_CSS)),
      s$tags$script(s$HTML("$(document).on('shiny:value',function(e){if(e.name==='pipeline_log'||e.name==='reproduce_log'){setTimeout(function(){var p=document.getElementById(e.name);if(p&&p.parentElement)p.parentElement.scrollTop=p.parentElement.scrollHeight;},0);}});"))),
    s$tabPanel("Overview", value = "overview",
      s$h1("The retracted research record"), s$p(class = "subhead", "Collection, public attention and the evidence behind the analysis."),
      s$uiOutput("overview_cards"),
      s$div(class = "overview-charts",
        s$div(s$h2("Document collection"), s$plotOutput("overview_routes", height = "340px")),
        s$div(s$h2("News coverage"), s$plotOutput("overview_news", height = "340px"))),
      s$p(class = "source-line", "Sources: stage1-rendered-pages.csv; news-coverage-union.csv. Identity checks do not establish full-text completeness. News coverage is DOI-linked attention, not necessarily news about a retraction."),
      s$div(class = "band", s$h2("Social and media sources"), DT::DTOutput("overview_social")),
      s$div(class = "controls", s$actionButton("refresh", "Refresh reports", icon = s$icon("rotate"))),
      s$textOutput("report_date")),
    s$tabPanel("Run pipeline", value = "pipeline",
      s$h1("Collect and inspect documents"), s$p(class = "subhead", "Each run is saved separately from the research dataset."),
      s$div(id = "tab-pipeline", s$fluidRow(s$column(3, s$wellPanel(
        s$textAreaInput("items", "DOIs or Retraction Watch record IDs", rows = 7,
                        placeholder = "10.1371/journal.pone.0066275\nrecord:70871"),
        s$checkboxInput("recover", "Try alternative access routes", TRUE),
        s$checkboxInput("media", "Collect citation and attention data", TRUE),
        s$checkboxInput("exclude", "Exclude X / Twitter posts", TRUE),
        s$uiOutput("pipeline_buttons"), s$uiOutput("key_status"),
        s$selectInput("history", "Saved runs", choices = c("Select a run" = "")),
        s$actionButton("load_history", "Open run", icon = s$icon("folder-open")))),
      s$column(9, s$uiOutput("pipeline_status"), s$uiOutput("run_cards"),
        s$plotOutput("run_plot", height = "240px"), DT::DTOutput("run_results"),
        s$div(class = "controls", s$downloadButton("download_run", "Download full CSV")),
        s$h3("Document details"), s$uiOutput("run_detail"),
        s$selectInput("page_doi", "Saved document", choices = character()),
        s$div(class = "controls", s$actionButton("open_page", "Open saved page", icon = s$icon("arrow-up-right-from-square")),
              s$downloadButton("download_page", "Download page")),
        s$h3("Live log"), s$tags$pre(class = "log", s$textOutput("pipeline_log")),
        s$h3("Recovery and collection steps"), DT::DTOutput("run_steps")))),
      s$div(class = "band", s$h2("Run history"), DT::DTOutput("history_table"))),
    s$tabPanel("Analysis results", value = "analysis",
      s$h1("Inspect the results"), s$p(class = "subhead", "Saved estimates, subgroup comparisons and validation evidence."),
      s$fluidRow(id = "tab-analysis", s$column(3, s$wellPanel(
        s$selectInput("topic", "Analysis", c("Citations" = "citations", "Media and social attention" = "media",
                                               "Retraction reasons" = "notices", "Annotation and model validation" = "validation")),
        s$conditionalPanel("input.topic !== 'validation'",
          s$selectInput("group", "Breakdown", c("Overall" = "overall", "Reason" = "reason", "Country" = "country",
                                                   "Publisher" = "publisher", "Discipline" = "discipline"))),
        s$conditionalPanel("input.topic === 'validation'",
          s$selectInput("validation_measure", "Chart measure", c("Labeller agreement" = "agreement",
                            "Model matches majority" = "model", "Sample size" = "items"))),
        s$selectInput("report", "Result table", choices = character()),
        s$selectInput("metric", "Measure", choices = character()),
        s$sliderInput("limit", "Groups shown", min = 5, max = 30, value = 12, step = 1))),
      s$column(9, s$uiOutput("analysis_note"), s$uiOutput("analysis_chart"),
        s$textOutput("analysis_source"), DT::DTOutput("analysis_table"),
        s$div(class = "controls", s$downloadButton("download_analysis", "Download CSV"))))),
    s$tabPanel("Reproduce", value = "reproduce",
      s$h1("Reproduce the analysis"), s$p(class = "subhead", "All 14 registered steps, checked against the reference results in an isolated workspace."),
      s$uiOutput("reproduce_buttons"), s$uiOutput("reproduce_status"),
      s$uiOutput("reproduce_summary"), s$uiOutput("reproduce_findings"),
      s$div(class = "controls", s$actionButton("view_analysis", "View analysis results", icon = s$icon("chart-column"))),
      DT::DTOutput("reproduce_steps"),
      s$h3("Result comparison"), DT::DTOutput("reproduce_results"),
      s$div(class = "controls", s$downloadButton("download_reproduce", "Download comparison")),
      s$h3("Live log"), s$tags$pre(class = "log", s$textOutput("reproduce_log"))),
    s$tabPanel("About", value = "about", s$div(class = "about",
      s$h1("About this project"), s$p(class = "subhead", "University of Sydney capstone | Retraction Impact Pipeline"),
      s$h2("Scope"), s$p("The pipeline connects Retraction Watch records, article and notice captures, OpenAlex citation histories, Altmetric posts and PlumX counts. Collection and text extraction are driven from R through Python; the reported analyses are implemented in R."),
      s$h2("Reading the evidence"), s$tags$ul(
        s$tags$li("Records, papers and documents are different units. One paper can have several notices; a mass notice can relate to many papers."),
        s$tags$li("Identity-checked captures are not necessarily complete article texts. Unavailable pages and failed lookups are missing, not zero."),
        s$tags$li("Citation estimates describe changes relative to matched controls. Pre-existing trends and sample selection limit causal interpretation."),
        s$tags$li("News counts are not unique news stories. PlumX counts do not supply post text or publication dates. X / Twitter IDs do not supply readable text."),
        s$tags$li("Model labels are validated only for the sampled sources, tasks and prompt versions. Retraction reasons are multi-label, so percentages need not sum to 100%.")),
      s$h2("Local execution and credentials"),
      s$p("The dashboard listens on this computer only. Pipeline runs and reproduction workspaces have their own output directories. The production database and existing exports are not updated by dashboard jobs."),
      s$p("Optional API credentials are inherited from the launching R session as environment variables. Values are never included in job requests, command-line arguments or the dashboard. Logs and displayed results are redacted."),
      s$h2("Project documentation"), s$p("README.md and HANDOVER.md describe the data sources, interpretation limits and full rebuild workflow.")))
  )
}

cs43_app_server <- function(root, runs_dir) {
  force(root); force(runs_dir)
  function(input, output, session) {
    s <- cs43_app_shiny()
    state <- s$reactiveValues(pipeline = NULL, reproduce = NULL, selected = NULL, revision = 0L, reports_revision = 0L)
    session$onSessionEnded(function() s$isolate({ cs43_app_stop_job(state$pipeline); cs43_app_stop_job(state$reproduce) }))
    tick <- s$observe({
      s$invalidateLater(500, session)
      for (kind in c("pipeline", "reproduce")) {
        job <- s$isolate(state[[kind]])
        if (!is.null(job) && job$status == "running") { cs43_app_poll(job); state$revision <- s$isolate(state$revision) + 1L }
      }
    })
    s$observeEvent(input$refresh, { state$reports_revision <- state$reports_revision + 1L })
    overview <- s$reactive({ state$reports_revision; cs43_app_overview(root) })
    card <- function(value, label, detail = "") s$div(class = "metric", s$div(class = "metric-value", value),
                                                       s$div(class = "metric-label", label), s$div(class = "metric-detail", detail))
    cards <- function(...) s$div(class = "metrics", ...)
    table <- function(x, rows = 10L, selection = "none") {
      x <- cs43_app_display_values(x)
      if (!ncol(x)) x <- data.frame(Status = character())
      nums <- names(x)[vapply(x, is.numeric, logical(1))]
      widget <- DT::datatable(x, colnames = cs43_app_labels(names(x)), rownames = FALSE, escape = TRUE,
        selection = selection, width = "100%",
        options = list(pageLength = rows, scrollX = TRUE, autoWidth = FALSE,
                       columnDefs = list(list(targets = match(nums, names(x)) - 1L, className = "dt-right")),
                       lengthMenu = c(5, 10, 25, 50), language = list(emptyTable = "No saved data available")))
      for (name in nums) {
        digits <- if (any(is.finite(x[[name]]) & abs(x[[name]] - round(x[[name]])) > 1e-8)) 3L else 0L
        widget <- DT::formatRound(widget, columns = name, digits = digits, mark = ",")
      }
      widget
    }
    watch_table <- function(dir, name) s$reactivePoll(750, session,
      checkFunc = function() cs43_app_stamp(dir(), name),
      valueFunc = function() if (is.null(dir())) data.frame() else cs43_app_clean_table(cs43_app_read(file.path(dir(), name))))
    status_ui <- function(job) {
      if (is.null(job)) return(s$div(class = "status-row", s$span(class = "status-pill", "Ready")))
      s$tagList(s$div(class = "status-row", s$span(class = "status-pill", job$status),
                     s$span(if (job$status == "running") job$phase else basename(job$dir))),
        s$div(class = "progress", role = "progressbar", `aria-valuenow` = job$progress,
               `aria-valuemin` = 0, `aria-valuemax` = 100,
               s$div(class = "progress-bar", style = sprintf("width:%d%%", job$progress))))
    }
    output$overview_cards <- s$renderUI({ o <- overview(); cards(
      card(cs43_app_number(o$total), "DOI document targets", "Articles and notices, deduplicated by DOI"),
      card(cs43_app_number(o$kept), "Identity-checked captures", if (is.finite(o$coverage)) sprintf("%.2f%% of document targets", o$coverage) else "Report unavailable"),
      card(cs43_app_number(o$news), "Papers with news-linked attention", "Article + notice DOIs; Altmetric + PlumX"),
      card(cs43_app_number(o$posts), "Tracked media and social posts", "Includes sources without readable text")) })
    output$overview_routes <- s$renderPlot({ d <- overview()$routes
      labels <- c("browser-created MHTML" = "Browser MHTML", "browser-rendered HTML" = "WebKit HTML",
                  "HTML from the University" = "Campus HTML", "HTML from a plain-request" = "Plain-request HTML",
                  "publisher/PMC" = "Publisher / PMC text", "explicitly tagged" = "OA / archive copies",
                  "other: pdf" = "Other PDF", "not collected" = "Not collected")
      if (nrow(d)) for (pattern in names(labels)) d$measure[startsWith(d$measure, pattern)] <- labels[[pattern]]
      cs43_app_plot(d, "n", limit = 8) }, res = 100, execOnResize = TRUE,
      width = function() cs43_app_plot_width(session$clientData$output_overview_routes_width), height = 340)
    output$overview_news <- s$renderPlot({ d <- overview()$news_table
      labels <- c("Altmetric, article DOI" = "Altmetric: article", "Altmetric, notice DOI" = "Altmetric: notice",
                  "PlumX, article DOI (count only)" = "PlumX: article", "PlumX, notice DOI (count only)" = "PlumX: notice",
                  "Altmetric article or notice" = "Altmetric union", "PlumX article or notice" = "PlumX union", "ALL FOUR" = "All four sources")
      if (nrow(d)) { d <- d[d$measure %in% names(labels), , drop = FALSE]; d$measure <- unname(labels[d$measure]) }
      cs43_app_plot(d, "papers", limit = 8) }, res = 100, execOnResize = TRUE,
      width = function() cs43_app_plot_width(session$clientData$output_overview_news_width), height = 340)
    output$overview_social <- DT::renderDT({ d <- overview()$social
      if (nrow(d)) d <- d[intersect(c("kind", "posts", "papers", "with_text", "says_retracted", "five_categories"), names(d))]
      table(d, 5L) })
    output$report_date <- s$renderText({ state$reports_revision; p <- file.path(root, "data/reports/stage1-rendered-pages.csv")
      if (file.exists(p)) paste("Collection report updated:", format(file.info(p)$mtime, "%Y-%m-%d %H:%M")) else "Collection report not available" })
    output$key_status <- s$renderUI({ names <- c(Altmetric = "ALTMETRIC_API_KEY", Elsevier = "ELSEVIER_API_KEY", OpenAlex = "OPENALEX_API_KEY")
      s$div(class = "source-line", paste(paste(names(names), ifelse(nzchar(Sys.getenv(names)), "configured", "not configured"), sep = ": "), collapse = " | ")) })
    busy <- s$reactive({ state$revision; any(vapply(c("pipeline", "reproduce"), function(k) {
      j <- state[[k]]; !is.null(j) && j$status == "running"
    }, logical(1))) })
    output$pipeline_buttons <- s$renderUI({ s$div(class = "controls",
      s$actionButton("start_pipeline", "Run pipeline", icon = s$icon("play"), class = "btn-primary", disabled = busy()),
      s$actionButton("stop_pipeline", "Stop", icon = s$icon("stop"), disabled = is.null(state$pipeline) || state$pipeline$status != "running")) })
    output$reproduce_buttons <- s$renderUI({ s$div(class = "controls",
      s$actionButton("start_reproduce", "Run all 14 steps", icon = s$icon("play"), class = "btn-primary", disabled = busy()),
      s$actionButton("stop_reproduce", "Stop", icon = s$icon("stop"), disabled = is.null(state$reproduce) || state$reproduce$status != "running")) })
    s$observeEvent(input$start_pipeline, {
      if (busy()) return()
      tryCatch({
        items <- cs43_app_items(input$items)
        state$pipeline <- cs43_app_start_job(root, runs_dir, "pipeline", items, input$recover, input$media, input$exclude)
        state$selected <- state$pipeline$dir; state$revision <- state$revision + 1L
      }, error = function(e) s$showNotification(cs43_app_redact(conditionMessage(e)), type = "error", duration = 10))
    })
    s$observeEvent(input$start_reproduce, {
      if (busy()) return()
      tryCatch({ state$reproduce <- cs43_app_start_job(root, runs_dir, "reproduce"); state$revision <- state$revision + 1L },
               error = function(e) s$showNotification(cs43_app_redact(conditionMessage(e)), type = "error"))
    })
    s$observeEvent(input$stop_pipeline, { cs43_app_stop_job(state$pipeline); state$revision <- state$revision + 1L })
    s$observeEvent(input$stop_reproduce, { cs43_app_stop_job(state$reproduce); state$revision <- state$revision + 1L })
    history <- s$reactivePoll(1000, session, checkFunc = function() {
      dirs <- list.dirs(runs_dir, recursive = FALSE, full.names = TRUE)
      paste(vapply(dirs, function(d) paste(cs43_app_stamp(d, "request.json"), cs43_app_stamp(d, "status.json")), ""), collapse = "|")
    }, valueFunc = function() cs43_app_history(runs_dir))
    s$observe({ h <- history(); s$updateSelectInput(session, "history", choices = c("Select a run" = "", if (nrow(h)) stats::setNames(h$id, paste(h$created, h$kind, h$status, sep = " | "))), selected = s$isolate(input$history)) })
    s$observeEvent(input$load_history, {
      if (busy()) { s$showNotification("Stop or finish the active run before opening history."); return() }
      h <- history(); s$req(input$history %in% h$id)
      dir <- file.path(runs_dir, input$history)
      req <- jsonlite::read_json(file.path(dir, "request.json"))
      if (req$kind == "pipeline") state$selected <- dir else {
        job <- new.env(parent = emptyenv()); job$dir <- dir; job$status <- h$status[h$id == input$history]
        job$progress <- if (job$status == "completed") 100L else 0L; job$phase <- "Saved run"
        job$log <- if (file.exists(file.path(dir, "console.log"))) readLines(file.path(dir, "console.log"), warn = FALSE) else character()
        state$reproduce <- job
      }
    })
    output$history_table <- DT::renderDT({ table(history()) })
    selected_dir <- s$reactive(state$selected)
    run_result <- watch_table(selected_dir, "results.csv")
    output$run_results <- DT::renderDT({ table(cs43_app_run_summary(run_result()),
       selection = list(mode = "single", selected = 1L, target = "row")) })
    output$run_detail <- s$renderUI({
      d <- run_result()
      if (!nrow(d)) return(s$p(class = "source-line", "No document selected."))
      row <- input$run_results_rows_selected
      if (length(row) != 1L || is.na(row) || row > nrow(d) || row < 1L) row <- 1L
      fields <- cs43_app_detail_fields(d, row)
      s$tagList(s$p(s$strong(d$item[row])), s$tags$dl(class = "document-details",
        lapply(seq_along(fields), function(i) s$tagList(s$tags$dt(names(fields)[i]), s$tags$dd(fields[[i]])))))
    })
    output$run_cards <- s$renderUI({ d <- run_result(); good <- if ("verified" %in% names(d)) sum(d$verified %in% 1) else 0L
      cards(card(nrow(d), "Result rows"), card(good, "Verified captures"),
             card(if ("quality" %in% names(d)) sum(d$quality %in% "full") else 0, "Long-text extractions"),
             card(if ("quality" %in% names(d)) sum(d$quality %in% "notice-only") else 0, "Notice-only extractions")) })
    output$run_plot <- s$renderPlot({ d <- run_result();
      if ("quality" %in% names(d)) { x <- as.data.frame(base::table(ifelse(is.na(d$quality) | d$quality == "", "Not extracted", d$quality))); cs43_app_plot(x, "Freq") }
      else cs43_app_plot(data.frame(), "n") }, res = 100, execOnResize = TRUE,
      width = function() cs43_app_plot_width(session$clientData$output_run_plot_width), height = 240)
    output$pipeline_status <- s$renderUI({ state$revision
      if (!is.null(state$selected) && (is.null(state$pipeline) || !identical(state$selected, state$pipeline$dir))) {
        path <- file.path(state$selected, "status.json")
        meta <- if (file.exists(path)) jsonlite::read_json(path) else list(status = "Saved run")
        return(s$div(class = "status-row", s$span(class = "status-pill", meta$status), s$span(basename(state$selected))))
      }
      status_ui(state$pipeline) })
    output$pipeline_log <- s$renderText({ state$revision;
      if (!is.null(state$pipeline) && identical(state$selected, state$pipeline$dir)) paste(state$pipeline$log, collapse = "\n")
      else if (!is.null(state$selected) && file.exists(file.path(state$selected, "console.log")))
        paste(cs43_app_redact(utils::tail(readLines(file.path(state$selected, "console.log"), warn = FALSE), 1200)), collapse = "\n")
      else "No run selected." })
    run_steps <- watch_table(selected_dir, "steps.csv")
    output$run_steps <- DT::renderDT({ table(run_steps()) })
    output$download_run <- s$downloadHandler(filename = function() "pipeline-results.csv", content = function(file) {
      s$req(nrow(run_result()) > 0); utils::write.csv(run_result(), file, row.names = FALSE, fileEncoding = "UTF-8") })
    s$observe({ d <- run_result(); choices <- if (all(c("doi", "verified") %in% names(d))) unique(d$doi[d$verified %in% 1]) else character()
      s$updateSelectInput(session, "page_doi", choices = choices, selected = s$isolate(input$page_doi)) })
    prepare_page <- function() {
      d <- run_result(); s$req(!is.null(state$selected), input$page_doi %in% d$doi[d$verified %in% 1])
      save_page(input$page_doi, data_dir = file.path(state$selected, "data"),
                 to = file.path(state$selected, "pages"), open = FALSE)
    }
    s$observeEvent(input$open_page, { tryCatch(open_saved_page(prepare_page()), error = function(e)
      s$showNotification(cs43_app_redact(conditionMessage(e)), type = "error")) })
    output$download_page <- s$downloadHandler(filename = function() basename(prepare_page()), content = function(file) {
      if (!file.copy(prepare_page(), file)) stop("Could not copy the saved document.") }, contentType = "application/octet-stream")
    s$observe({ choices <- cs43_app_analysis_files(input$topic, input$group)
      s$updateSelectInput(session, "report", choices = stats::setNames(choices, sub("[.]csv$", "", choices))) })
    analysis_data <- s$reactive({ state$reports_revision; s$req(input$report %in% cs43_app_analysis_files(input$topic, input$group)); cs43_app_report(root, input$report) })
    s$observe({ metrics <- cs43_app_metrics(analysis_data()); preferred <- intersect(c("ratio_mh", "papers_ever_told_pct", "share_of_papers", "value", "papers"), metrics)
      s$updateSelectInput(session, "metric", choices = stats::setNames(metrics, cs43_app_labels(metrics)), selected = if (length(preferred)) preferred[1] else utils::head(metrics, 1)) })
    output$analysis_chart <- s$renderUI(s$plotOutput("analysis_plot",
      height = paste0(cs43_app_analysis_height(input$topic, input$limit), "px")))
    output$analysis_plot <- s$renderPlot({ d <- analysis_data()
      if (identical(input$topic, "validation")) {
        d <- cs43_app_validation_chart(d, input$validation_measure)
        cs43_app_plot(d, "value", limit = input$limit)
      } else cs43_app_plot(d, input$metric, limit = input$limit,
        reference = if (identical(input$metric, "ratio_mh")) 1 else NULL) }, res = 100, execOnResize = TRUE,
        width = function() cs43_app_plot_width(session$clientData$output_analysis_plot_width),
        height = function() cs43_app_analysis_height(input$topic, input$limit))
    output$analysis_table <- DT::renderDT({ table(analysis_data()) })
    output$analysis_source <- s$renderText({ s$req(input$report); p <- file.path(root, "data/reports", input$report)
      paste("Source:", input$report, if (file.exists(p)) paste("| Updated", format(file.info(p)$mtime, "%Y-%m-%d %H:%M")) else "| File unavailable") })
    output$analysis_note <- s$renderUI({ note <- switch(input$topic,
      citations = "Citation ratios are relative to matched controls, not pure causal effects. Groups may overlap and have different eligible samples.",
      media = "Attention counts are not unique stories. Sources without text cannot be classified; validation does not extend to every social source.",
      notices = "Reasons are multi-label and based on extracted notices and model answers; percentages need not sum to 100%.",
      "Validation scores apply to the listed task, model, prompt and labelled sample. Group filters apply only to citation, media and notice summaries.")
      s$div(class = "notice", note) })
    output$download_analysis <- s$downloadHandler(filename = function() input$report, content = function(file) {
      s$req(nrow(analysis_data()) > 0); utils::write.csv(analysis_data(), file, row.names = FALSE, fileEncoding = "UTF-8") })
    reproduction <- s$reactive({ if (is.null(state$reproduce)) NULL else state$reproduce$dir })
    output$reproduce_status <- s$renderUI({ state$revision; status_ui(state$reproduce) })
    output$reproduce_log <- s$renderText({ state$revision; if (is.null(state$reproduce)) "No reproduction started." else paste(state$reproduce$log, collapse = "\n") })
    reproduction_steps <- watch_table(reproduction, "steps.csv")
    output$reproduce_steps <- DT::renderDT({ table(reproduction_steps()) })
    repro_result <- watch_table(reproduction, "results.csv")
    output$reproduce_results <- DT::renderDT({ table(repro_result()) })
    output$reproduce_summary <- s$renderUI({ d <- repro_result(); dir <- reproduction()
      steps <- if (is.null(dir)) data.frame() else cs43_app_read(file.path(dir, "steps.csv"))
      if (!nrow(steps)) return(NULL)
      summary <- if (any(steps$status != "ok")) "Some analysis steps failed; inspect the step table and log."
                 else if (nrow(steps) < 14L) "Reproduction in progress."
                 else if ("outcome" %in% names(d) && any(d$outcome != "identical"))
                   "All 14 steps completed; some regenerated results differ from the reference."
                 else "All 14 steps completed; the regenerated results match the reference."
      s$tagList(s$p(summary), cards(card(sum(steps$status == "ok"), "Steps succeeded", "of 14 registered steps"),
        card(if ("outcome" %in% names(d)) sum(d$outcome == "identical") else 0, "Identical files"),
        card(if ("outcome" %in% names(d)) sum(d$outcome == "changed") else 0, "Changed files", "Changed is not the same as failed"),
        card(if ("outcome" %in% names(d)) sum(d$outcome == "new") else 0, "New files")),
        s$p(class = "source-line", "Only files regenerated by this run are compared; older files that were not regenerated are not counted.")) })
    output$reproduce_findings <- s$renderUI({
      state$revision
      dir <- reproduction()
      if (is.null(dir) || is.null(state$reproduce) || state$reproduce$status != "completed") return(NULL)
      steps <- reproduction_steps()
      if (nrow(steps) != 14L || any(steps$status != "ok")) return(NULL)
      cs43_app_findings_ui(file.path(dir, "workspace"))
    })
    s$observeEvent(input$view_analysis, { s$updateNavbarPage(session, "page", selected = "analysis") })
    output$download_reproduce <- s$downloadHandler(filename = function() "reproduction-comparison.csv", content = function(file) {
      s$req(nrow(repro_result()) > 0); utils::write.csv(repro_result(), file, row.names = FALSE, fileEncoding = "UTF-8") })
  }
}

cs43_app <- function(host = "127.0.0.1", port = NULL, launch.browser = interactive(),
                     root = pipeline_dir(), runs_dir = file.path(root, "runs", "app"), run = TRUE) {
  if (!host %in% c("127.0.0.1", "localhost", "::1"))
    stop("The dashboard is local-only; use a loopback host.", call. = FALSE)
  for (p in c("shiny", "DT")) if (!requireNamespace(p, quietly = TRUE))
    stop("Restore the project packages with renv::restore() before starting cs43_app().", call. = FALSE)
  root <- normalizePath(root, winslash = "/", mustWork = TRUE)
  runs_dir <- cs43_app_runs_dir(root, runs_dir)
  app <- shiny::shinyApp(cs43_app_ui(), cs43_app_server(root, runs_dir))
  if (!run) return(app)
  shiny::runApp(app, host = host, port = port, launch.browser = launch.browser)
}
