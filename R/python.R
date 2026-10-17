# The Python side of the pipeline, driven from R.
#
# The client runs only R. Stage 1 (collection) and Stage 2 (text extraction)
# are the Python package in src/ and the scripts in scripts/; they are not
# rewritten, they are called:
#   * reticulate builds the Python environment from requirements.txt, with a
#     uv-managed interpreter, so nobody installs Python by hand;
#   * each script runs as a child process with that interpreter, so a crash in
#     Python cannot take the R session down;
#   * data_dir sets RWPIPE_DATA for the child. NULL means the collected
#     dataset in data/; anything else is a separate directory, which is how
#     tests and the handover run leave the dataset untouched.

pipeline_dir <- function() {
  root <- Sys.getenv("CS43_PIPELINE", "")
  if (!nzchar(root)) root <- rprojroot::find_root(rprojroot::has_file("requirements.txt"))
  normalizePath(root, winslash = "/", mustWork = TRUE)
}

# Build (or reuse) the Python environment and return its interpreter.
#
# The default route is a uv-managed interpreter built from requirements.txt, so
# nobody has to install Python by hand and everybody gets the same versions.
# Environment discovery can fail even when Python itself is installed.
# An explicit interpreter and an existing project environment are supported
# alternatives; each is checked before any collector is started.
#
# So there are two ways past it, in order:
#
#   * CS43_PYTHON - an interpreter to use as-is. Set this when you already have
#     one that can run the pipeline.
#   * the project's own .venv, if somebody has made one.
#
# Either way the interpreter is checked before it is returned, because an
# interpreter that cannot import what the scripts need fails later, further
# away, and less clearly.
PIPELINE_IMPORTS <- c("httpx", "playwright", "pandas", "pypdf", "bs4")

python_can_run <- function(python) {
  if (!nzchar(python) || !file.exists(python)) return("no such interpreter")
  code <- sprintf("import %s", paste(PIPELINE_IMPORTS, collapse = ", "))
  res <- tryCatch(processx::run(python, c("-c", code), error_on_status = FALSE),
                  error = function(e) list(status = 1L, stderr = conditionMessage(e)))
  if (identical(res$status, 0L)) return(NULL)
  paste0("cannot import what the pipeline needs (",
         paste(PIPELINE_IMPORTS, collapse = ", "), "): ",
         trimws(substr(res$stderr, 1, 300)))
}

project_venv <- function() {
  root <- pipeline_dir()
  for (p in c(file.path(root, ".venv", "Scripts", "python.exe"),
              file.path(root, ".venv", "bin", "python"))) {
    if (file.exists(p)) return(normalizePath(p, winslash = "/"))
  }
  NULL
}

# --- Windows: Python's cache under a plain path ------------------------------
#
# reticulate keeps uv, the Python it downloads and the environment it builds
# under R_USER_CACHE_DIR - by default AppData\Local\R\cache inside the user's
# profile, so a user name with letters outside A-Z is part of that path. There
# the uv route failed on the development machine (project log 2026-09-30), and the
# guide asked such a user to type Sys.setenv(R_USER_CACHE_DIR = "C:/cs43-cache")
# first in every R session; the acceptance test of 2026-10-09 ran that way.
#
# The path probe distinguishes interpreter installation from discovery.
# An ASCII cache avoids the known discovery-path problem; it does not prove
# installation will succeed on a new machine or with every local environment.
#
# So the guide's step is done here, before the uv route, and the same way in
# every session: a later session that only calls save_page() has to find the
# Python an earlier one installed. On Windows, when R_USER_CACHE_DIR is not set
# and R's default cache path is not plain ASCII, it becomes C:/cs43-cache, or
# C:/Users/Public/cs43-cache where C:/ cannot be written. A folder already
# there wins over one that would have to be made, so a session that had to
# fall back keeps falling back. A value the user set is never changed.
has_non_ascii <- function(x) {
  cp <- utf8ToInt(enc2utf8(x))
  anyNA(cp) || any(cp > 127L)
}

cs43_cache_candidates <- function() {
  x <- c(file.path(Sys.getenv("SystemDrive", "C:"), "cs43-cache"),
         file.path(Sys.getenv("PUBLIC", "C:/Users/Public"), "cs43-cache"))
  x <- unique(gsub("\\\\", "/", x))
  x[!vapply(x, has_non_ascii, logical(1)) & !grepl(" ", x)]
}

# Can files be written in `d`? Makes the folder when it is missing.
dir_usable <- function(d) {
  dir.create(d, recursive = TRUE, showWarnings = FALSE)
  if (!dir.exists(d)) return(FALSE)
  f <- file.path(d, sprintf(".cs43-write-test-%d", Sys.getpid()))
  ok <- isTRUE(suppressWarnings(file.create(f, showWarnings = FALSE)))
  if (ok) unlink(f)
  ok
}

# The folder R_USER_CACHE_DIR should be set to: NULL when nothing needs doing,
# NA when it should be moved and no candidate can be written. Every input is
# an argument so that tests/r/test_python_env.R can decide without touching
# this machine.
ascii_cache_dir <- function(windows = .Platform$OS.type == "windows",
                            current = Sys.getenv("R_USER_CACHE_DIR"),
                            default = tools::R_user_dir("reticulate", "cache"),
                            candidates = cs43_cache_candidates(),
                            exists = dir.exists, usable = dir_usable) {
  if (!windows || nzchar(current) || !has_non_ascii(default)) return(NULL)
  there <- vapply(candidates, function(d) isTRUE(exists(d)), logical(1))
  for (d in c(candidates[there], candidates[!there])) if (isTRUE(usable(d))) return(d)
  NA_character_
}

use_ascii_cache_dir <- function(...) {
  d <- ascii_cache_dir(...)
  if (is.null(d)) return(invisible(NULL))
  if (is.na(d)) {
    message("R's cache folder has letters outside A-Z, and none of ",
            paste(cs43_cache_candidates(), collapse = ", "), " could be written; ",
            "if Python cannot be installed, set R_USER_CACHE_DIR to a plain folder ",
            "you can write to, the same one in every session")
    return(invisible(NULL))
  }
  Sys.setenv(R_USER_CACHE_DIR = d)
  message("Python for this project is kept in ", d, " (R_USER_CACHE_DIR), because ",
          "the usual folder's path has letters outside A-Z. Every session does ",
          "the same, so it is found again.")
  invisible(d)
}

# The uv route itself. reticulate 1.47.0 learns which interpreter uv built by
# having that Python write sys.executable to a file - open() with no encoding,
# so in the Windows code page - and reads the file back as UTF-8. A path with
# letters outside A-Z comes back garbled and reticulate reports that it found
# no Python (the probe above: GBK on the development machine). Python's UTF-8
# mode is intended to avoid that discovery mismatch even for an explicitly
# chosen cache. This does not establish fresh-machine installation success.
# It is set only while the environment is built and Python starts.
uv_python <- function(req, python_version,
                      require = reticulate::py_require, config = reticulate::py_config) {
  old <- Sys.getenv("PYTHONUTF8", unset = NA)
  Sys.setenv(PYTHONUTF8 = "1")
  on.exit(if (is.na(old)) Sys.unsetenv("PYTHONUTF8") else Sys.setenv(PYTHONUTF8 = old))
  require(req, python_version = python_version)
  normalizePath(config()$python, winslash = "/")
}

use_pipeline_python <- function(python_version = "3.12.10") {
  chosen <- NULL
  named <- Sys.getenv("CS43_PYTHON", "")
  if (nzchar(named)) {
    bad <- python_can_run(named)
    if (!is.null(bad)) {
      stop("CS43_PYTHON is set to ", named, " but it ", bad, call. = FALSE)
    }
    chosen <- normalizePath(named, winslash = "/")
  }
  if (is.null(chosen)) {
    venv <- project_venv()
    if (!is.null(venv) && is.null(python_can_run(venv))) chosen <- venv
  }
  if (is.null(chosen)) {
    use_ascii_cache_dir()
    req <- readLines(file.path(pipeline_dir(), "requirements.txt"), warn = FALSE)
    req <- trimws(sub("#.*$", "", req))
    req <- req[nzchar(req)]
    # A managed interpreter, never whatever Python happens to be installed:
    # the client's machine may have none, or a different one.
    Sys.setenv(UV_PYTHON_PREFERENCE = "only-managed")
    chosen <- tryCatch(uv_python(req, python_version), error = function(e) {
      stop("could not build the Python environment with uv: ", conditionMessage(e),
           "\nuv keeps Python under R_USER_CACHE_DIR (now ",
           Sys.getenv("R_USER_CACHE_DIR", "not set"), "). If that folder is ",
           "redirected or not on a local drive, point R_USER_CACHE_DIR at a ",
           "plain folder such as C:/cs43-cache, or set CS43_PYTHON to an ",
           "interpreter that already has the packages in requirements.txt.",
           call. = FALSE)
    })
  }
  # The interpreter chosen here is the one every script runs with, and the one
  # reticulate uses for read_db(). Returning its path was not enough: a script
  # ran with reticulate::py_exe(), and reticulate picks its own interpreter
  # unless told - on the development machine it picked an unrelated system
  # Python that happened to have the packages. So it is recorded, and
  # reticulate is pointed at it before anything initialises Python.
  options(cs43.python = chosen)
  Sys.setenv(RETICULATE_PYTHON = chosen)
  if (reticulate::py_available(initialize = FALSE)) {
    now <- normalizePath(reticulate::py_config()$python, winslash = "/")
    if (!identical(tolower(now), tolower(chosen))) {
      warning("reticulate is already running ", now, " in this R session; scripts ",
              "will use ", chosen, ", but read_db() needs a fresh session to follow",
              call. = FALSE)
    }
  } else {
    reticulate::use_python(chosen, required = TRUE)
  }
  chosen
}

# The interpreter the scripts run with: the one use_pipeline_python() chose,
# which it chooses on first use if nobody has called it yet.
pipeline_python <- function() {
  p <- getOption("cs43.python")
  if (is.null(p)) p <- use_pipeline_python()
  p
}

# The contact address sent to OpenAlex and the other APIs (mailto, User-Agent):
# RWPIPE_CONTACT, or the address of the student who built the pipeline. The
# Python side reads the same variable with the same default
# (src/rwpipe/config.py, POLITE_CONTACT), and every script R starts inherits
# it, so one Sys.setenv(RWPIPE_CONTACT = "you@example.org") covers both.
CS43_DEFAULT_CONTACT <- "xbao0816@uni.sydney.edu.au"

polite_contact <- function() {
  # Python str.strip(): Unicode whitespace plus the four C0 separators.
  whitespace <- "[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]"
  x <- trimws(Sys.getenv("RWPIPE_CONTACT", ""), whitespace = whitespace)
  if (!nzchar(x)) x <- CS43_DEFAULT_CONTACT
  pieces <- strsplit(x, "@", fixed = TRUE)[[1L]]
  valid <- all(utf8ToInt(x) >= 33L & utf8ToInt(x) <= 126L) &&
    sum(utf8ToInt(x) == 64L) == 1L &&
    length(pieces) == 2L && nzchar(pieces[1L]) &&
    grepl("^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+$", pieces[1L])
  if (valid) {
    labels <- strsplit(pieces[2L], ".", fixed = TRUE)[[1L]]
    valid <- length(labels) >= 2L && !grepl("[.]$", pieces[2L]) &&
      all(grepl("^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?$", labels))
  }
  if (!valid) stop("RWPIPE_CONTACT must be one ASCII email address without spaces or control characters", call. = FALSE)
  x
}

# API keys for the collectors that need them. The scripts read these from the
# environment first and fall back to a .env file in the project root; both
# stay on this machine. Leave a key NULL to keep what is already set.
set_api_keys <- function(altmetric = NULL, elsevier = NULL, elsevier_insttoken = NULL) {
  keys <- c(ALTMETRIC_API_KEY = altmetric, ELSEVIER_API_KEY = elsevier,
            ELSEVIER_INSTTOKEN = elsevier_insttoken)
  if (length(keys)) do.call(Sys.setenv, as.list(keys))
  invisible(names(keys))
}

# Run the pipeline's Python with `args`. Output streams to the console.
run_python <- function(args, data_dir = NULL, echo = TRUE) {
  env <- c("current", CS43_PIPELINE = pipeline_dir(), PYTHONUTF8 = "1",
           PYTHONIOENCODING = "utf-8")
  if (!is.null(data_dir)) {
    dir.create(data_dir, recursive = TRUE, showWarnings = FALSE)
    env <- c(env, RWPIPE_DATA = normalizePath(data_dir, winslash = "/"))
  }
  res <- processx::run(pipeline_python(), args, env = env, wd = pipeline_dir(),
                       echo = echo, error_on_status = FALSE)
  if (res$status != 0) {
    # The script's own last words, so a step table reads "failed: ...
    # ALTMETRIC_API_KEY not found" rather than only an exit status.
    said <- function(x) {
      x <- trimws(strsplit(if (is.null(x)) "" else x, "\n", fixed = TRUE)[[1]])
      utils::tail(x[nzchar(x)], 1)
    }
    last <- c(said(res$stderr), said(res$stdout))[1]
    stop(sprintf("%s exited with status %d%s", basename(args[1]), res$status,
                 if (!is.na(last)) paste0(": ", substr(last, 1, 200)) else ""),
         call. = FALSE)
  }
  invisible(res)
}

# One script from scripts/, e.g. run_script("extract_text.py", "--limit", "10").
run_script <- function(script, args = character(), data_dir = NULL, echo = TRUE) {
  run_python(c(file.path(pipeline_dir(), "scripts", script), args), data_dir, echo)
}

# The browsers Stage 1 renders pages with: Chromium, and WebKit for the hosts
# that refuse Chromium. A no-op when already installed.
install_browser <- function() {
  run_python(c("-m", "playwright", "install", "chromium", "webkit"))
}

# A query against a pipeline database, returned as a data frame.
read_db <- function(sql, data_dir = NULL) {
  db <- file.path(if (is.null(data_dir)) file.path(pipeline_dir(), "data") else data_dir,
                  "interim", "pipeline.sqlite")
  if (!file.exists(db)) {
    stop("no page database at ", db, if (is.null(data_dir))
           " - copy the data folder of the cs43-pages package into the project (HANDOVER.md, section 1)",
         call. = FALSE)
  }
  # The project's interpreter, not one reticulate picks for itself: in a fresh
  # session that could be any Python on the machine, without pandas.
  pipeline_python()
  sqlite3 <- reticulate::import("sqlite3")
  pd <- reticulate::import("pandas")
  con <- sqlite3$connect(paste0("file:", normalizePath(db, winslash = "/"), "?mode=ro"), uri = TRUE)
  on.exit(con$close())
  pd$read_sql_query(sql, con)
}

# Command-line arguments from R values: an option is dropped when NULL, and a
# flag is present only when TRUE.
opt <- function(name, value) if (is.null(value)) character() else c(name, as.character(value))
flag <- function(name, on) if (isTRUE(on)) name else character()
