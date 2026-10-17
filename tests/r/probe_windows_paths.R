# Which parts of the pipeline break under a non-ASCII or spaced Windows path?
# A measurement, not a test (it is not named test_*.R). Run from the project
# root, on Windows:
#
#   Rscript tests/r/probe_windows_paths.R <uv folder> <probe folder>
#
#   <uv folder>     a reticulate uv folder that already holds what uv needs
#                   (bin/uv.exe, cache/, python/cpython-3.12.10-*), e.g. the
#                   one use_pipeline_python() filled - it is only read
#   <probe folder>  a new folder with a plain (ASCII) path, best on a drive
#                   without 8.3 short names (`fsutil 8dot3name query E:`)
#
# A second probe folder, <user profile>/cs43-paths-probe, holds the cases that
# need drive C: (short names) or the user's own name outside AppData. The five
# reticulate cases download uv, Python 3.12.10 and requirements.txt (about
# 350 MB each) into the probe folders; nothing outside them is installed.
#
# Writes data/reports/windows-paths-probe-<date>.csv, one row per check.
#
# Why: HANDOVER.md told a Windows user whose user name is not A-Z to set
# R_USER_CACHE_DIR by hand (project log 2026-09-30), because uv failed while R's
# cache sat under the development machine's Chinese user name. Those runs, and
# the first one on 2026-09-18, were inside the Claude desktop app, which
# redirects AppData\Local - the 2026-09-18 entry blamed the redirect - so the
# two causes were never told apart. An audit (2026-10-13) also said R does not
# start when TEMP holds non-ASCII letters or a space.
#
# The first version of this probe (earlier on 2026-10-13) set TEMP and TMP to a
# non-ASCII folder that was not there when the child processes ran: Playwright
# reported ENOENT for it, and R and Python silently used other folders. So its
# rows said nothing about non-ASCII TEMP. This version sets TMPDIR, TMP and TEMP
# together (R reads TMPDIR first, then TMP, then TEMP - ?tempdir - and an
# inherited TMPDIR would otherwise win), checks the folder exists right before
# every run, and records whether the process really used it.

args <- commandArgs(TRUE)
if (length(args) < 2) stop("usage: Rscript tests/r/probe_windows_paths.R <uv folder> <probe folder>")
if (.Platform$OS.type != "windows") stop("this probe is for Windows")
uv_src <- normalizePath(args[1], winslash = "/", mustWork = TRUE)
probe <- args[2]
proj <- normalizePath(".", winslash = "/")
stopifnot(file.exists(file.path(proj, "requirements.txt")))
uv_exe <- file.path(uv_src, "bin", "uv.exe")
py_full <- list.files(file.path(uv_src, "python"), pattern = "^cpython-3\\.12\\.10-", full.names = TRUE)
stopifnot(file.exists(uv_exe), length(py_full) == 1)
venv_py <- file.path(proj, ".venv", "Scripts", "python.exe")
rscript <- file.path(R.home("bin"), "Rscript.exe")

non_ascii <- function(x) any(utf8ToInt(enc2utf8(x)) > 127L)
dir.create(probe, recursive = TRUE, showWarnings = FALSE)
probe <- normalizePath(probe, winslash = "/")
if (non_ascii(probe) || grepl(" ", probe)) stop("the probe folder must have a plain (ASCII) path without spaces")
cprobe <- file.path(normalizePath(Sys.getenv("USERPROFILE"), winslash = "/"), "cs43-paths-probe",
                    basename(probe))
mk <- function(...) {
  d <- file.path(...)
  dir.create(d, recursive = TRUE, showWarnings = FALSE)
  stopifnot(dir.exists(d))
  normalizePath(d, winslash = "/")
}
wide <- "\u975e ASCII \u8def\u5f84"   # Chinese characters and a space
wide1 <- "\u975e\u8def\u5f84"         # Chinese characters, no space

temps <- list(
  "E: plain"             = mk(probe, "temp"),
  "E: plain + space"     = mk(probe, "plain path", "temp"),
  "E: non-ASCII"         = mk(probe, wide1, "temp"),
  "E: non-ASCII + space" = mk(probe, wide, "temp"),
  "C: non-ASCII + space" = mk(cprobe, wide, "temp"),
  "C: this user's TEMP"  = normalizePath(Sys.getenv("TEMP"), winslash = "/", mustWork = TRUE))

# The user's own name stays out of the published table.
hide <- function(x) {
  x <- gsub("\\\\", "/", paste(x, collapse = " "))
  for (h in unique(c(normalizePath(Sys.getenv("USERPROFILE"), winslash = "/", mustWork = FALSE),
                     basename(Sys.getenv("USERPROFILE")))))
    x <- gsub(h, "%USERPROFILE%", x, fixed = TRUE)
  gsub("[\r\n]+", " | ", trimws(x))
}

rows <- list()
record <- function(check, temp, cache, res, used = NA, detail = "") {
  rows[[length(rows) + 1]] <<- data.frame(
    check = check, temp = temp, cache = cache,
    outcome = if (identical(res$status, 0L)) "ok" else "failed",
    exit_status = res$status, seconds = round(res$secs, 1), used_given_temp = used,
    detail = substr(hide(detail), 1, 400), stringsAsFactors = FALSE)
  message(sprintf("%-42s %-22s %-24s %s", check, temp, cache, rows[[length(rows)]]$outcome))
}
run <- function(cmd, cmd_args, env = character(), wd = proj, timeout = 1800) {
  t0 <- Sys.time()
  res <- tryCatch(processx::run(cmd, cmd_args, env = c("current", env), wd = wd,
                                error_on_status = FALSE, timeout = timeout),
                  error = function(e) list(status = -1L, stdout = "", stderr = conditionMessage(e)))
  res$secs <- as.numeric(difftime(Sys.time(), t0, units = "secs"))
  res
}
last_line <- function(x) {
  x <- trimws(strsplit(if (is.null(x)) "" else x, "\n", fixed = TRUE)[[1]])
  x <- x[nzchar(x)]
  if (length(x)) utils::tail(x, 1) else ""
}
temp_env <- function(path) {
  stopifnot(dir.exists(path))   # the first version's mistake
  p <- gsub("/", "\\\\", path)
  c(TMPDIR = p, TMP = p, TEMP = p)
}
same_dir <- function(a, b) {
  a <- tryCatch(normalizePath(trimws(a), winslash = "/", mustWork = TRUE), error = function(e) NA)
  !is.na(a) && identical(tolower(a), tolower(normalizePath(b, winslash = "/")))
}

# --- R itself ---------------------------------------------------------------
r_code <- "cat(normalizePath(dirname(tempdir()), winslash = '/'))"
for (kind in names(temps)) {
  for (v in list(character(), "--vanilla")) {
    r <- run(rscript, c(v, "-e", r_code), temp_env(temps[[kind]]))
    record(if (length(v)) "R starts (--vanilla)" else "R starts (project .Rprofile, renv)",
           kind, "-", r, used = if (r$status == 0) same_dir(r$stdout, temps[[kind]]) else NA,
           detail = paste(r$stdout, last_line(r$stderr)))
  }
}
# The way round a spaced TEMP: TMPDIR alone, without a space.
env <- c(temp_env(temps[["E: plain + space"]]), TMPDIR = gsub("/", "\\\\", temps[["E: plain"]]))
r <- run(rscript, c("--vanilla", "-e", r_code), env)
record("R starts (--vanilla), TMPDIR = E: plain", "E: plain + space", "-", r,
       used = if (r$status == 0) same_dir(r$stdout, temps[["E: plain"]]) else NA,
       detail = paste(r$stdout, last_line(r$stderr)))

# --- the project's Python and the browsers ----------------------------------
py_code <- paste(
  "import tempfile, os, httpx, playwright, pandas, pypdf, bs4",
  "f = tempfile.NamedTemporaryFile('w', delete=False, encoding='utf-8', suffix='.txt')",
  "f.write('caf\\u00e9'); f.close()",
  "assert open(f.name, encoding='utf-8').read() == 'caf\\u00e9'",
  "os.remove(f.name)",
  "print(tempfile.gettempdir())", sep = "\n")
pw_code <- paste(
  "import tempfile",
  "from playwright.sync_api import sync_playwright",
  "with sync_playwright() as p:",
  "    for name in ('chromium', 'webkit'):",
  "        b = getattr(p, name).launch(headless=True)",
  "        pg = b.new_page(); pg.set_content('<p>caf\\u00e9 \\u4e2d</p>')",
  "        assert '\\u4e2d' in pg.content(); b.close(); print(name, 'ok')", sep = "\n")
pw_file <- file.path(mk(probe, "scripts"), "browsers.py")
writeLines(pw_code, pw_file, useBytes = TRUE)
child_temps <- c("E: plain", "E: non-ASCII", "C: non-ASCII + space", "C: this user's TEMP")
if (file.exists(venv_py)) {
  for (kind in child_temps) {
    env <- c(temp_env(temps[[kind]]), PYTHONUTF8 = "1")
    r <- run(venv_py, c("-c", py_code), env)
    record("Python imports + temp file", kind, "-", r,
           used = if (r$status == 0) same_dir(last_line(r$stdout), temps[[kind]]) else NA,
           detail = paste(r$stdout, last_line(r$stderr)))
    r <- run(venv_py, pw_file, env)
    record("Playwright chromium + webkit launch", kind, "-", r,
           detail = paste(r$stdout, last_line(r$stderr)))
  }
  for (kind in c("E: plain", "E: non-ASCII", "C: non-ASCII + space")) {
    # Past a collection error: another change in progress can break one test
    # file, and that says nothing about TEMP. Compare the counts across rows.
    r <- run(venv_py, c("-m", "pytest", "-q", "-p", "no:cacheprovider",
                        "--continue-on-collection-errors"),
             c(temp_env(temps[[kind]]), PYTHONUTF8 = "1"))
    record("pytest, whole suite", kind, "-", r, detail = last_line(r$stdout))
  }
}

# --- uv building an environment under each TEMP ------------------------------
# reticulate::uv_exec() sets UV_CACHE_DIR and UV_PYTHON_INSTALL_DIR under
# R_USER_CACHE_DIR and runs uv with the requirements. Each case gets its own
# copy of the cache, so each builds its environment afresh, offline. No
# `uv python install`: it would also write python.exe into ~/.local/bin.
# The whole cache is copied: its bucket names carry uv's format version
# (simple-v26, wheels-v7 for uv 0.12.24), and a list of names goes stale.
copy_uv <- function(dest) {
  dir.create(file.path(dest, "python"), recursive = TRUE, showWarnings = FALSE)
  dir.create(file.path(dest, "cache"), recursive = TRUE, showWarnings = FALSE)
  for (d in list.dirs(file.path(uv_src, "cache"), recursive = FALSE))
    file.copy(d, file.path(dest, "cache"), recursive = TRUE)
  file.copy(py_full, file.path(dest, "python"), recursive = TRUE)
  dest
}
for (kind in c("E: plain", "E: non-ASCII", "C: non-ASCII + space")) {
  dest <- copy_uv(mk(probe, paste0("uv-", gsub("[^a-z]+", "-", tolower(kind)))))
  env <- c(temp_env(temps[[kind]]), UV_CACHE_DIR = file.path(dest, "cache"),
           UV_PYTHON_INSTALL_DIR = file.path(dest, "python"), UV_OFFLINE = "1",
           UV_PYTHON_DOWNLOADS = "never", UV_PYTHON_PREFERENCE = "only-managed",
           UV_PYTHON_BIN_DIR = mk(probe, "uv-bin"), VIRTUAL_ENV = "")
  r <- run(uv_exe, c("tool", "run", "--isolated", "--python", "3.12.10",
                     "--with", "httpx", "--with", "pandas", "--with", "pypdf",
                     "--with", "beautifulsoup4", "--with", "playwright", "--",
                     "python", "-c", "import sys, httpx, pandas, pypdf, bs4, playwright; print(sys.executable)"),
           c(env, PYTHONUTF8 = "1"), wd = probe)
  record("uv tool run --isolated (new environment, offline)", kind, "E: plain", r,
         detail = paste(last_line(r$stdout), last_line(r$stderr)))
}

# --- the whole uv route, as use_pipeline_python() ran it before 2026-10-13 ---
# A fresh R_USER_CACHE_DIR each time: reticulate downloads uv, Python 3.12.10
# and requirements.txt into it. Run in a folder holding only requirements.txt,
# because use_pipeline_python() takes this route only when the project has no
# .venv, with the project's renv library and without its .Rprofile, so that
# renv does not move its own cache too. The non-ASCII cases sit on E: and in
# the user's profile on C: - like the default AppData\Local\R\cache, but
# outside AppData, which the Claude desktop app redirects.
#
# reticulate 1.47.0 learns which interpreter uv built by having that Python
# write sys.executable to a file - open() without an encoding, so in the
# Windows code page - and reading the file back as UTF-8. The last case runs
# Python in UTF-8 mode (PYTHONUTF8=1) to show whether that is the failure.
route_proj <- mk(probe, "proj")
file.copy(file.path(proj, "requirements.txt"), route_proj, overwrite = TRUE)
route_code <- c(
  sprintf(".libPaths(c('%s', .libPaths()))", .libPaths()[1]),
  "Sys.unsetenv(c('RETICULATE_PYTHON', 'CS43_PYTHON', 'VIRTUAL_ENV'))",
  "req <- readLines('requirements.txt', warn = FALSE)",
  "req <- trimws(sub('#.*$', '', req)); req <- req[nzchar(req)]",
  "Sys.setenv(UV_PYTHON_PREFERENCE = 'only-managed')",
  "reticulate::py_require(req, python_version = '3.12.10')",
  "py <- normalizePath(reticulate::py_config()$python, winslash = '/')",
  "r <- processx::run(py, c('-c', 'import httpx, playwright, pandas, pypdf, bs4'), error_on_status = FALSE)",
  "pd <- reticulate::import('pandas')   # in this R process, as read_db() does",
  "cat('\\nPYTHON', py, 'imports', r$status, 'in-process pandas', pd[['__version__']], '\\n')",
  "quit(status = r$status)")
# What a route run said: its PYTHON line, or why it stopped (reticulate's
# error lines, then the last lines it wrote).
route_said <- function(r) {
  said <- grep("^PYTHON ", strsplit(r$stdout, "\n", fixed = TRUE)[[1]], value = TRUE)
  err <- trimws(strsplit(if (is.null(r$stderr)) "" else r$stderr, "\n", fixed = TRUE)[[1]])
  err <- err[nzchar(err)]
  why <- unique(c(grep("Error|error|[Pp]ython", err, value = TRUE), utils::tail(err, 2)))
  paste(c(said, if (!identical(r$status, 0L)) utils::head(why, 4)), collapse = " | ")
}
route_file <- file.path(mk(probe, "scripts"), "uv_route.R")
writeLines(route_code, route_file, useBytes = TRUE)
route_cases <- list(
  list(cache = "E: plain", dir = mk(probe, "rcache-plain"), utf8 = FALSE),
  list(cache = "E: non-ASCII", dir = mk(probe, wide1, "rcache"), utf8 = FALSE),
  list(cache = "C: profile, non-ASCII", dir = mk(cprobe, "rcache"), utf8 = FALSE),
  list(cache = "C: profile, non-ASCII", dir = mk(cprobe, "rcache-utf8"), utf8 = TRUE))
for (rc in route_cases) {
  stopifnot(length(list.files(rc$dir, all.files = TRUE, no.. = TRUE)) == 0)
  env <- c(R_USER_CACHE_DIR = rc$dir, R_USER_DATA_DIR = mk(probe, "rdata"),
           R_USER_CONFIG_DIR = mk(probe, "rconfig"), UV_PYTHON_BIN_DIR = mk(probe, "uv-bin"),
           if (rc$utf8) c(PYTHONUTF8 = "1"))
  r <- run(rscript, c("--vanilla", route_file), env, wd = route_proj)
  record(paste0("reticulate uv route as before (download)", if (rc$utf8) ", PYTHONUTF8=1"),
         "C: this user's TEMP", rc$cache, r, detail = route_said(r))
}

# --- use_pipeline_python() as changed, twice ---------------------------------
# The code in R/python.R itself, in a project with no .venv, R_USER_CACHE_DIR
# not set, and R's default cache folder under a non-ASCII LOCALAPPDATA (in the
# user's profile, outside the AppData the desktop app redirects). The plain
# folder it moves to is a stand-in for C:/cs43-cache, so that this machine's
# C:/ is left alone. The second session must find the same Python again
# without downloading it.
e2e_cache <- mk(probe, "cs43-cache-standin")
e2e_code <- c(
  sprintf(".libPaths(c('%s', .libPaths()))", .libPaths()[1]),
  "Sys.unsetenv(c('RETICULATE_PYTHON', 'CS43_PYTHON', 'VIRTUAL_ENV', 'R_USER_CACHE_DIR', 'XDG_CACHE_HOME'))",
  sprintf("source('%s/R/python.R')", proj),
  sprintf("cs43_cache_candidates <- function() '%s'", e2e_cache),
  "non_ascii_default <- has_non_ascii(tools::R_user_dir('reticulate', 'cache'))",
  "py <- use_pipeline_python()",
  "r <- processx::run(py, c('-c', 'import httpx, playwright, pandas, pypdf, bs4'), error_on_status = FALSE)",
  "cat('\\nPYTHON', py, 'imports', r$status, 'R_USER_CACHE_DIR', Sys.getenv('R_USER_CACHE_DIR'),",
  "    'default cache non-ASCII', non_ascii_default, '\\n')",
  "quit(status = r$status)")
e2e_file <- file.path(mk(probe, "scripts"), "use_pipeline_python.R")
writeLines(e2e_code, e2e_file, useBytes = TRUE)
e2e_env <- c(LOCALAPPDATA = gsub("/", "\\\\", mk(cprobe, "localappdata")),
             R_USER_DATA_DIR = mk(probe, "rdata"), R_USER_CONFIG_DIR = mk(probe, "rconfig"),
             UV_PYTHON_BIN_DIR = mk(probe, "uv-bin"), CS43_PIPELINE = route_proj)
stopifnot(length(list.files(e2e_cache, all.files = TRUE, no.. = TRUE)) == 0)
first <- NA_character_
for (s in 1:2) {
  r <- run(rscript, c("--vanilla", e2e_file), e2e_env, wd = route_proj)
  py <- sub("^PYTHON (\\S+) .*$", "\\1", grep("^PYTHON ", strsplit(r$stdout, "\n", fixed = TRUE)[[1]], value = TRUE)[1])
  if (s == 1) first <- py
  moved <- grep("^Python for this project is kept in", strsplit(r$stderr, "\n", fixed = TRUE)[[1]], value = TRUE)
  record(sprintf("use_pipeline_python() as changed, session %d%s", s,
                 if (s == 1) " (download)" else " (finds it again)"),
         "C: this user's TEMP", "default under non-ASCII LOCALAPPDATA", r,
         detail = paste(c(route_said(r), substr(moved, 1, 80),
                          if (s == 2) paste("same Python as session 1:", identical(py, first))),
                        collapse = " | "))
}

enc <- tryCatch(trimws(processx::run(venv_py, c("-c", "import locale; print(locale.getencoding())"))$stdout),
                error = function(e) NA_character_)
out <- do.call(rbind, rows)
out <- cbind(date = format(Sys.Date()), r_version = paste(R.version$major, R.version$minor, sep = "."),
             reticulate = as.character(utils::packageVersion("reticulate")),
             uv_version = trimws(processx::run(uv_exe, "--version")$stdout),
             python_locale_encoding = enc,
             short_names_C = grepl("ENABLED on", paste(system2("fsutil", c("8dot3name", "query", "C:"), stdout = TRUE, stderr = TRUE), collapse = " ")),
             short_names_probe_drive = grepl("ENABLED on", paste(system2("fsutil", c("8dot3name", "query", substr(probe, 1, 2)), stdout = TRUE, stderr = TRUE), collapse = " ")),
             out)
dir.create(file.path(proj, "data", "reports"), showWarnings = FALSE)
f <- file.path(proj, "data", "reports", sprintf("windows-paths-probe-%s.csv", Sys.Date()))
utils::write.csv(out, f, row.names = FALSE, fileEncoding = "UTF-8")
message("-> ", f)
