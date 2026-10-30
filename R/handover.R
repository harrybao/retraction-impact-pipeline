# The handover test: the whole pipeline on a few items, in a separate
# directory, with one row per page and a plain-language reason for every
# outcome.
#
#   res <- run_pipeline(c("10.1371/journal.pone.0066275", "10.1155/2016/4835617"))
#
#   items      paper or retraction-notice DOIs, or Record IDs written
#              "record:241". A DOI the CSV does not hold is run all the same,
#              with its details from Crossref for this run only
#              (scripts/run_dois.py)
#   csv        the Retraction Watch CSV (default: data/raw/retraction_watch.csv,
#              or RWPIPE_CSV)
#   data_dir   where the run's pages, database and report go: by default a new
#              folder runs/<date_time> in the project. Not R's temporary
#              folder - R deletes that when it closes, and the client keeps the
#              MHTML and the report.
#   reference  our exports, used to say how our dataset holds a page that
#              cannot be re-fetched from this machine
#   retry_after  seconds to wait before one more try at pages refused by a
#              bot check (those refusals come and go; HTTP 403s are not retried)
#   recover    after the browser, try the routes that built the dataset for the
#              pages a publisher refused: PubMed Central, an open-access copy,
#              the Elsevier API, the Internet Archive
#   media      collect OpenAlex citations, Altmetric posts and PlumX counts for
#              the same items - the client expects the MHTML, the Altmetric
#              data and the analysis
#   exclude    Altmetric sources to leave out of the posts. "twitter" by
#              default: the key's allowance of X post ids is spent, and with it
#              at 0 any DOI with tweets is refused. NULL once it is renewed.
#
# Every step after the crawl is tried on its own: a route or a collector that
# fails - no Elsevier key, a service down - is recorded in attr(res, "steps")
# and the rest still run. The report says, per page, how it was obtained and
# what each collector found.
MAX_ITEMS <- 1000L
MAX_CHARS <- 30000L   # Windows caps a command line at 32,767 characters

run_pipeline <- function(items, csv = NULL,
                         data_dir = file.path(pipeline_dir(), "runs",
                                              format(Sys.time(), "%Y-%m-%d_%H%M%S")),
                         reference = file.path(pipeline_dir(), "data", "clean"),
                         retry_after = 60, recover = TRUE, media = TRUE,
                         exclude = "twitter") {
  # The entries reach Python on one command line, which Windows caps at 32,767
  # characters (about 1,200 DOIs); past that the run would fail before it
  # starts, with a message about creating a process. A run of 1,000 entries
  # takes hours anyway, so batches lose nothing.
  # The limit is in characters; the count is a plain-language stand-in for it.
  if (length(items) > MAX_ITEMS || sum(nchar(items)) + length(items) > MAX_CHARS) {
    stop("give at most ", MAX_ITEMS, " entries per run, and fewer if they are long (this was ",
         length(items), " entries, ", sum(nchar(items)), " characters): run them in batches,",
         " for example\n  for (b in split(items, ceiling(seq_along(items) / 500))) run_pipeline(b)",
         call. = FALSE)
  }
  prep <- c("prepare", if (!is.null(csv)) c("--csv", normalizePath(csv, winslash = "/")), items)
  run_script("run_dois.py", prep, data_dir)
  crawl(data_dir, workers = 1, max_attempts = 1)
  rq <- run_script("run_dois.py", "requeue", data_dir)
  if (!grepl("requeued 0", rq$stdout, fixed = TRUE)) {
    Sys.sleep(retry_after)
    crawl(data_dir, workers = 1, max_attempts = 2)
  }
  verify_archives(data_dir, apply = TRUE)

  steps <- data.frame(step = character(), outcome = character(), seconds = numeric(),
                      stringsAsFactors = FALSE)
  attempt <- function(step, expr) {
    t0 <- Sys.time()
    warns <- character()
    outcome <- withCallingHandlers(
      tryCatch({ force(expr); "ok" },
               error = function(e) paste("failed:", conditionMessage(e))),
      warning = function(w) {
        warns <<- c(warns, conditionMessage(w))
        invokeRestart("muffleWarning")
      })
    if (outcome == "ok" && length(warns)) {
      outcome <- paste("ok, but", paste(unique(warns), collapse = "; "))
    }
    steps[nrow(steps) + 1L, ] <<- list(step, outcome,
                                       round(as.numeric(difftime(Sys.time(), t0, units = "secs"))))
    if (outcome != "ok") message("  ", step, " - ", outcome, " (carrying on)")
  }
  if (recover) {
    attempt("open-access locations (OpenAlex)", find_oa_locations(data_dir))
    attempt("which open-access hosts answer", run_script("probe_oa_hosts.py", c("--hosts", "50"),
                                                         data_dir))
    attempt("PubMed Central", recover_pmc(data_dir))
    attempt("open-access copies", recover_open_access(data_dir))
    attempt("Elsevier API", recover_elsevier(data_dir))
    attempt("Internet Archive", recover_wayback(data_dir))
    attempt("verify recovered pages", verify_archives(data_dir, apply = TRUE))
  }
  extract_text(data_dir)
  if (media) {
    attempt("OpenAlex citations", collect_openalex(data_dir))
    attempt("Altmetric, papers", collect_altmetric(data_dir, exclude = exclude))
    attempt("Altmetric, retraction notices", collect_altmetric_notices(data_dir, exclude = exclude))
    attempt("PlumX, papers", collect_plumx(data_dir))
    attempt("PlumX, retraction notices", collect_plumx_notices(data_dir))
  }
  # NCBI refuses in spells. If it refused above, ask once more now that the
  # rest of the run has gone by, and verify and extract whatever arrives.
  if (recover && any(startsWith(steps$outcome[steps$step == "PubMed Central"], "ok, but"))) {
    attempt("PubMed Central, asked again", recover_pmc(data_dir))
    attempt("verify pages from the second ask", verify_archives(data_dir, apply = TRUE))
    # In attempt(): an optional retry must not cost the report of a whole run.
    attempt("text from the second ask", extract_text(data_dir))
  }

  out <- file.path(normalizePath(data_dir, winslash = "/"), "run_report.csv")
  ref <- if (dir.exists(reference)) c("--reference", normalizePath(reference, winslash = "/"))
  run_script("run_dois.py", c("report", ref, "--out", out), data_dir)
  res <- utils::read.csv(out, fileEncoding = "UTF-8", check.names = FALSE)
  if (nrow(steps)) {
    message("\nsteps after the crawl:")
    for (i in seq_len(nrow(steps))) {
      message(sprintf("  %-34s %s (%d s)", steps$step[i], steps$outcome[i], steps$seconds[i]))
    }
  }
  attr(res, "steps") <- steps
  attr(res, "data_dir") <- data_dir
  res
}

# A captured page as a file a browser opens. Pages are stored gzipped and
# named by a hash (mhtml/<prefix>/<xx>/<sha1>.mhtml.gz); this writes one out
# as <to>/<doi>.mhtml - or .html, or .xml for the PubMed Central and Elsevier
# text - and returns its path. open = TRUE opens it in the default browser
# (Chrome and Edge open MHTML; Firefox does not).
#
#   res <- run_pipeline(...)
#   save_page("10.1371/journal.pone.0066275", attr(res, "data_dir"), open = TRUE)
#
# data_dir NULL reads the collected dataset in data/.
open_saved_page <- function(path, system = Sys.info()[["sysname"]],
                            exists = file.exists, launch = base::system2,
                            browse = utils::browseURL) {
  path <- normalizePath(path, winslash = "/", mustWork = TRUE)
  if (identical(system, "Darwin") && grepl("[.](mhtml|mht)$", path, ignore.case = TRUE)) {
    browsers <- c("Google Chrome", "Microsoft Edge", "Chromium", "Brave Browser")
    found <- browsers[vapply(browsers, function(name)
      any(exists(c(file.path("/Applications", paste0(name, ".app")),
                   path.expand(file.path("~/Applications", paste0(name, ".app")))))), logical(1))]
    if (!length(found)) {
      warning("MHTML needs Chrome, Edge or Chromium on macOS; the saved file was not opened in Safari.", call. = FALSE)
      return(invisible(FALSE))
    }
    launch("open", c("-a", shQuote(found[1L]), shQuote(path)), wait = FALSE)
  } else {
    browse(path)
  }
  invisible(TRUE)
}

save_page <- function(doi, data_dir = NULL,
                      to = file.path(if (is.null(data_dir)) file.path(pipeline_dir(), "runs")
                                     else data_dir, "pages"),
                      open = FALSE) {
  q <- sprintf("SELECT mhtml_path, source_kind FROM documents WHERE doi = '%s' AND usable = 1",
               gsub("'", "''", tolower(doi)))
  hit <- read_db(q, data_dir)
  if (!nrow(hit) || is.na(hit$mhtml_path[1])) {
    stop("no verified page is held for ", doi, call. = FALSE)
  }
  base <- if (is.null(data_dir)) file.path(pipeline_dir(), "data") else data_dir
  src <- file.path(base, sub("^data/", "", chartr("\\", "/", hit$mhtml_path[1])))
  if (!file.exists(src)) {
    # A copy of the dataset can lack a page (a partial copy, or one made
    # without the texts Elsevier and IOP supplied under text-and-data-mining
    # licences); the database still records how each was obtained.
    licensed <- isTRUE(hit$source_kind[1] %in% c("elsevier-api", "iop-sftp"))
    stop("the page for ", doi, " is not in this copy of the dataset",
         if (licensed) paste0(": its full text came from ",
                              if (hit$source_kind[1] == "iop-sftp") "IOP" else "Elsevier",
                              " under a text-and-data-mining licence that does not allow passing it on")
         else paste0(" (expected at ", src, ")"), call. = FALSE)
  }
  con <- if (grepl("[.]gz$", src)) gzfile(src, "rb") else file(src, "rb")
  # In chunks: readBin() reserves memory for all n items up front, so one
  # large n would claim gigabytes for a page of a few kilobytes.
  chunks <- list()
  repeat {
    b <- readBin(con, "raw", n = 1e7)
    if (!length(b)) break
    chunks[[length(chunks) + 1L]] <- b
  }
  close(con)
  raw <- if (length(chunks)) do.call(c, chunks) else raw(0)
  # The content says what it is; the stored name does not always (an Internet
  # Archive copy is filed as html but is MHTML). Matched on bytes: the first
  # 300 bytes can end inside a multi-byte character or be in another encoding,
  # and a character match would then fail on the page.
  first <- raw[seq_len(min(300, length(raw)))]
  head <- rawToChar(first[first != as.raw(0)])
  is <- function(p) grepl(p, head, ignore.case = TRUE, useBytes = TRUE)
  ext <- if (is("^[[:space:]]*(from:|mime-version)")) {
    "mhtml"
  } else if (is("^%PDF-")) {
    "pdf"
  } else if (is("^[[:space:]]*<([?]xml|article|full-text-retrieval)")) {
    "xml"
  } else {
    "html"
  }
  dir.create(to, showWarnings = FALSE, recursive = TRUE)
  out <- file.path(to, paste0(gsub("[^A-Za-z0-9._-]+", "_", doi), ".", ext))
  writeBin(raw, out)
  if (open) open_saved_page(out)
  invisible(out)
}

# Confirm that a copied package arrived unchanged: every file in the list
# (SHA256-cs43.csv or SHA256-cs43-pages.csv, which come beside the two package
# folders) is under `root` with the same SHA-256. Run it right after copying,
# before anything rewrites data/reports. Returns the files that are missing or
# different (none, when all is well).
#
#   check_package("E:/cs43-package/SHA256-cs43.csv")
#   check_package("E:/cs43-package/SHA256-cs43-pages.csv")   # after copying the pages in
check_package <- function(list_csv, root = pipeline_dir()) {
  x <- utils::read.csv(list_csv, colClasses = "character")
  p <- file.path(root, x$path)
  have <- file.exists(p)
  got <- rep(NA_character_, nrow(x))
  if (any(have)) got[have] <- unname(tools::sha256sum(p[have]))
  wrong <- !have | got != x$sha256
  bad <- data.frame(path = x$path[wrong],
                    problem = ifelse(have[wrong], "different", "missing"),
                    stringsAsFactors = FALSE)
  message(sum(!wrong), " of ", nrow(x), " files match",
          if (nrow(bad)) paste0("; ", nrow(bad), " missing or different:\n  ",
                                paste(utils::head(paste(bad$problem, bad$path), 20),
                                      collapse = "\n  "),
                                if (nrow(bad) > 20) "\n  ... (all of them in the returned table)")
          else "")
  invisible(bad)
}
