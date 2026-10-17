# Checks for save_page() in R/handover.R. Run from the project root:
#   Rscript --vanilla tests/r/test_save_page.R
#
# No Python and no real database: read_db() is replaced by a stub, and the
# stored pages are written into a temporary folder laid out like data/. What
# is checked is what the client sees: the file type comes from the content,
# and a page missing from his copy of the dataset - the full texts Elsevier and
# IOP supplied under licence are left out of it - is named as such rather than
# failing on an unreadable file.

source("R/python.R")
source("R/handover.R")

fails <- 0L
ok <- function(label, cond) {
  if (isTRUE(cond)) cat("ok   ", label, "\n")
  else { fails <<- fails + 1L; cat("FAIL ", label, "\n") }
}
err <- function(expr) tryCatch({ expr; "" }, error = function(e) conditionMessage(e))

root <- tempfile("cs43-save-page-")
dir.create(file.path(root, "mhtml", "10.1371"), recursive = TRUE)
dir.create(file.path(root, "mhtml", "10.1016"), recursive = TRUE)
put <- function(rel, text) {
  con <- gzfile(file.path(root, rel), "wb")
  writeBin(charToRaw(text), con)
  close(con)
}
put("mhtml/10.1371/a.mhtml.gz", "From: <Saved by Blink>\r\nMIME-Version: 1.0\r\n\r\nbody")
put("mhtml/10.1016/b.xml.gz", "<?xml version=\"1.0\"?><full-text-retrieval-response/>")
putraw <- function(rel, bytes) {
  dir.create(dirname(file.path(root, rel)), showWarnings = FALSE, recursive = TRUE)
  con <- gzfile(file.path(root, rel), "wb")
  writeBin(bytes, con)
  close(con)
}
# byte 300 falls inside a two-byte UTF-8 "é"; and an MHTML header in Latin-1
split_utf8 <- c(charToRaw(paste0("<html>", strrep("a", 293))), as.raw(c(0xc3, 0xa9)),
                charToRaw("</html>"))
putraw("mhtml/10.1093/c.html.gz", split_utf8)
putraw("mhtml/10.1093/d.mhtml.gz", c(charToRaw("FROM: <Saved by Blink> caf"), as.raw(0xe9),
                                     charToRaw("\r\nMIME-Version: 1.0\r\n")))
putraw("mhtml/10.1177/e.html.gz", c(charToRaw("%PDF-1.3\n"), as.raw(c(0xe2, 0xe3, 0xcf, 0xd3))))

rows <- list(
  "10.1371/journal.pone.0066275" = c("data/mhtml/10.1371/a.mhtml.gz", ""),
  "10.1016/xml-held"             = c("data/mhtml/10.1016/b.xml.gz", "elsevier-api"),
  "10.1016/elsevier-left-out"    = c("data/mhtml/10.1016/gone.xml.gz", "elsevier-api"),
  "10.1088/iop-left-out"         = c("data/mhtml/10.1088/gone.xml.gz", "iop-sftp"),
  "10.1234/lost"                 = c("data/mhtml/10.1234/gone.mhtml.gz", ""),
  "10.1093/split-utf8"           = c("data/mhtml/10.1093/c.html.gz", "campus-network"),
  "10.1093/latin1-mhtml"         = c("data/mhtml/10.1093/d.mhtml.gz", "campus-network"),
  "10.1177/pdf"                  = c("data/mhtml/10.1177/e.html.gz", "tail-plain-http"))

# The real read_db() stops with a plain message, before starting Python, when
# the pages' database is not there (a client who copied only the analysis).
m <- tryCatch({ read_db("SELECT 1", file.path(tempdir(), "no-such-dataset")); "" },
              error = function(e) conditionMessage(e))
ok("read_db names a missing page database", grepl("no page database at", m))

read_db <- function(sql, data_dir = NULL) {
  doi <- sub("^.*doi = '([^']*)'.*$", "\\1", sql)
  r <- rows[[doi]]
  if (is.null(r)) return(data.frame(mhtml_path = character(), source_kind = character()))
  data.frame(mhtml_path = r[1], source_kind = r[2], stringsAsFactors = FALSE)
}

out <- tempfile("pages-")
p <- save_page("10.1371/journal.pone.0066275", root, to = out)
ok("an MHTML page is written as .mhtml", grepl("[.]mhtml$", p) && file.exists(p))
ok("the file name is the DOI with / replaced",
   basename(p) == "10.1371_journal.pone.0066275.mhtml")
ok("the content is unpacked, not left gzipped",
   startsWith(rawToChar(readBin(p, "raw", 100)), "From:"))

x <- save_page("10.1016/xml-held", root, to = out)
ok("publisher XML is written as .xml", grepl("[.]xml$", x))
x <- err(y <- save_page("10.1093/split-utf8", root, to = out))
ok("a page whose 300th byte splits a character is still written (as .html)",
   x == "" && grepl("[.]html$", y))
x <- err(y <- save_page("10.1093/latin1-mhtml", root, to = out))
ok("an MHTML header with a Latin-1 byte is still recognised", x == "" && grepl("[.]mhtml$", y))
y <- save_page("10.1177/pdf", root, to = out)
ok("a stored PDF is written as .pdf, not .html", grepl("[.]pdf$", y))
ok("the bytes are written unchanged",
   identical(readBin(y, "raw", 100), c(charToRaw("%PDF-1.3\n"), as.raw(c(0xe2, 0xe3, 0xcf, 0xd3)))))

m <- err(save_page("10.1016/elsevier-left-out", root, to = out))
ok("an Elsevier text missing from the copy is named as licensed",
   grepl("not in this copy of the dataset", m) && grepl("Elsevier", m) && grepl("licence", m))
m <- err(save_page("10.1088/iop-left-out", root, to = out))
ok("an IOP text missing from the copy is named as licensed",
   grepl("IOP", m) && grepl("licence", m))
m <- err(save_page("10.1234/lost", root, to = out))
ok("any other missing file says where it was expected",
   grepl("not in this copy", m) && grepl("expected at", m) && !grepl("licence", m))
m <- err(save_page("10.9999/unknown", root, to = out))
ok("a DOI with no verified page says so", grepl("no verified page is held", m))

opened <- list()
launcher <- function(command, args, wait) { opened <<- list(command, args, wait); 0L }
no_safari <- function(...) stop("Safari must not receive MHTML")
ok("Mac MHTML selects Chrome instead of the default browser",
   open_saved_page(p, system = "Darwin", exists = function(x) grepl("Google Chrome.app$", x),
                   launch = launcher, browse = no_safari))
ok("Mac launch names the browser and saved page",
   identical(opened[[1]], "open") && opened[[2]][1] == "-a" &&
     grepl("Google Chrome", opened[[2]][2]) && grepl("mhtml", opened[[2]][3]) &&
     identical(opened[[3]], FALSE))
warning_text <- ""
v <- withCallingHandlers(open_saved_page(p, system = "Darwin", exists = function(x) FALSE,
                                         launch = launcher, browse = no_safari),
                         warning = function(w) { warning_text <<- conditionMessage(w);
                                                 invokeRestart("muffleWarning") })
ok("Mac without an MHTML browser leaves the file available with a warning",
   identical(v, FALSE) && grepl("Chrome", warning_text))
ordinary <- FALSE
open_saved_page(y, system = "Darwin", browse = function(path) ordinary <<- TRUE)
ok("Mac PDF retains the normal browser path", ordinary)
# run_pipeline() refuses more entries than one command line can carry, with
# a plain message and before any Python is started.
run_script <- function(...) stop("Python should not have been started")
m <- err(run_pipeline(sprintf("10.1/x%d", seq_len(MAX_ITEMS + 1L)), data_dir = tempfile()))
ok("too many entries is refused with a message about batches",
   grepl("at most 1000 entries", m) && grepl("batches", m))
long <- sprintf("10.1234/a-long-doi-%s-%04d", strrep("x", 60), seq_len(400))
m <- err(run_pipeline(long, data_dir = tempfile()))
ok("fewer but long entries that would overflow the command line are refused too",
   sum(nchar(long)) > MAX_CHARS && grepl("fewer if they are long", m))

# check_package(): every listed file present with the listed SHA-256.
pk <- tempfile("pkg-"); dir.create(file.path(pk, "data"), recursive = TRUE)
writeLines("one", file.path(pk, "data", "a.txt")); writeLines("two", file.path(pk, "b.txt"))
lst <- tempfile(fileext = ".csv")
utils::write.csv(data.frame(path = c("data/a.txt", "b.txt"),
                            bytes = 0, sha256 = unname(tools::sha256sum(file.path(pk, c("data/a.txt", "b.txt"))))),
                 lst, row.names = FALSE)
ok("an intact package has nothing missing or different",
   nrow(suppressMessages(check_package(lst, pk))) == 0)
writeLines("changed", file.path(pk, "b.txt")); unlink(file.path(pk, "data", "a.txt"))
bad <- suppressMessages(check_package(lst, pk))
msgs <- character()
withCallingHandlers(check_package(lst, pk), message = function(c) {
  msgs <<- c(msgs, conditionMessage(c)); invokeRestart("muffleMessage") })
ok("the message names the files that are wrong", any(grepl("missing data/a.txt", msgs)) &&
     any(grepl("different b.txt", msgs)))
ok("a changed and a missing file are both reported",
   identical(bad$path, c("data/a.txt", "b.txt")) && identical(bad$problem, c("missing", "different")))
unlink(c(pk, lst), recursive = TRUE)

unlink(c(root, out), recursive = TRUE)
cat("\n", if (fails) sprintf("%d FAILED", fails) else "all passed", "\n", sep = "")
quit(status = if (fails) 1L else 0L)
