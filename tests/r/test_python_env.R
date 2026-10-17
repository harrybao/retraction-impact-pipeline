# Configuration tests use simulated paths and never create a cache folder.
source("R/python.R")
fails <- 0L
ok <- function(label, cond) {
  if (isTRUE(cond)) cat("ok   ", label, "\n")
  else { fails <<- fails + 1L; cat("FAIL ", label, "\n") }
}
choose <- function(...) ascii_cache_dir(
  windows = TRUE, current = "", default = "C:/Users/\u00e9/cache",
  candidates = c("C:/cs43-cache", "C:/Users/Public/cs43-cache"), ...)
ok("ASCII path needs no redirection",
   is.null(ascii_cache_dir(TRUE, "", "C:/Users/Student/cache")))
ok("non-Windows paths need no redirection",
   is.null(ascii_cache_dir(FALSE, "", "/home/\u00e9/cache")))
ok("an explicit cache path is preserved, even if non-ASCII",
   is.null(ascii_cache_dir(TRUE, "D:/\u00e9/cache", "C:/Users/\u00e9/cache")))
ok("first writable candidate is selected",
   identical(choose(exists = function(d) FALSE, usable = function(d) TRUE), "C:/cs43-cache"))
ok("a previously created fallback keeps being selected",
   identical(choose(exists = function(d) grepl("Public", d), usable = function(d) TRUE),
             "C:/Users/Public/cs43-cache"))
ok("an unwritable first candidate falls back",
   identical(choose(exists = function(d) FALSE, usable = function(d) grepl("Public", d)),
             "C:/Users/Public/cs43-cache"))
ok("no writable candidate is reported",
   is.na(choose(exists = function(d) FALSE, usable = function(d) FALSE)))
ok("a non-ASCII character is detected",
   has_non_ascii("C:/Users/\u00e9/cache") && !has_non_ascii("C:/cs43-cache"))

local({
  old <- Sys.getenv("RWPIPE_CONTACT", unset = NA)
  on.exit(if (is.na(old)) Sys.unsetenv("RWPIPE_CONTACT") else Sys.setenv(RWPIPE_CONTACT = old))
  Sys.unsetenv("RWPIPE_CONTACT")
  ok("R uses the same default contact as Python", polite_contact() == "xbao0816@uni.sydney.edu.au")
  Sys.setenv(RWPIPE_CONTACT = " client@example.org ")
  ok("a contact override is trimmed and used", polite_contact() == "client@example.org")
  Sys.setenv(RWPIPE_CONTACT = " ")
  ok("a blank contact uses the default", polite_contact() == CS43_DEFAULT_CONTACT)
  for (space in c("\u000b", "\u000c", "\u001c", "\u0085", "\u00a0", "\u2003", "\u3000")) {
    Sys.setenv(RWPIPE_CONTACT = paste0(space, "client@example.org", space))
    ok("Unicode/control whitespace is removed like Python str.strip",
       polite_contact() == "client@example.org")
    Sys.setenv(RWPIPE_CONTACT = space)
    ok("Unicode/control whitespace alone uses the default",
       polite_contact() == CS43_DEFAULT_CONTACT)
  }
  for (address in c("no-at-sign", "a@@example.org", "a@", "@example.org",
                    "a\nb@example.org", "a b@example.org", "a@bad..org", "a@-bad.org",
                    "\u00e9@example.org")) {
    Sys.setenv(RWPIPE_CONTACT = address)
    err <- try(polite_contact(), silent = TRUE)
    ok("invalid email fails before an API call",
       inherits(err, "try-error") && grepl("RWPIPE_CONTACT", err, fixed = TRUE))
  }
})

local({
  old <- Sys.getenv("PYTHONUTF8", unset = NA)
  on.exit(if (is.na(old)) Sys.unsetenv("PYTHONUTF8") else Sys.setenv(PYTHONUTF8 = old))
  Sys.setenv(PYTHONUTF8 = "0")
  seen <- FALSE
  require <- function(req, python_version) {
    seen <<- Sys.getenv("PYTHONUTF8") == "1"
  }
  p <- uv_python(character(), "3.12.10", require, function() list(python = R.home()))
  ok("uv sees UTF-8 while building and locating its interpreter", seen && nzchar(p))
  ok("the previous UTF-8 mode is restored after success", Sys.getenv("PYTHONUTF8") == "0")
  err <- try(uv_python(character(), "3.12.10", function(...) stop("fake failure"),
                       function() list(python = R.home())), silent = TRUE)
  ok("a failed uv build also restores the previous mode",
     inherits(err, "try-error") && Sys.getenv("PYTHONUTF8") == "0")
})
cat("\n", if (fails) sprintf("%d FAILED", fails) else "all passed", "\n", sep = "")
quit(status = if (fails) 1L else 0L)
