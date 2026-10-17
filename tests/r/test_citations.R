# Checks for R/citations.R. Run from the project root:
#   Rscript tests/r/test_citations.R
#
# The two counts_by_year shapes are the point of most of these: the Python
# collector wrote objects, the R control collector kept OpenAlex's arrays, and
# a reader that silently handles only one returns zero citations for half the
# corpus without erroring anywhere.

source("R/citations.R")

fails <- 0L
ok <- function(label, cond) {
  if (isTRUE(cond)) {
    cat("ok   ", label, "\n")
  } else {
    fails <<- fails + 1L
    cat("FAIL ", label, "\n")
  }
}
eq <- function(label, got, want) {
  same <- isTRUE(all.equal(got, want, check.attributes = FALSE))
  if (!same) cat("      got: ", paste(utils::capture.output(print(got)), collapse = " | "),
                 "\n      want:", paste(utils::capture.output(print(want)), collapse = " | "), "\n")
  ok(label, same)
}

obj <- '{"2019": 1, "2013": 2}'
arr <- '[{"year":2026,"cited_by_count":1},{"year":2023,"cited_by_count":2}]'

# -- counts_long reads both shapes ------------------------------------------
l <- counts_long("a", obj)
eq("object shape: years", sort(l$year), c(2013L, 2019L))
eq("object shape: counts", l$n[order(l$year)], c(2L, 1L))

l <- counts_long("b", arr)
eq("array shape: years", sort(l$year), c(2023L, 2026L))
eq("array shape: counts", l$n[order(l$year)], c(2L, 1L))

l <- counts_long(c("a", "b"), c(obj, arr))
eq("mixed shapes in one column", sort(unique(l$id)), c("a", "b"))
eq("mixed shapes: total citations", sum(l$n), 6L)

eq("empty and NA yield no rows", nrow(counts_long(c("x", "y"), c(NA, ""))), 0L)
eq("a work with no citations yields no rows", nrow(counts_long("z", "{}")), 0L)

# Whitespace variants OpenAlex has used at one time or another.
eq("spacing variants", sum(counts_long("a", '{"2020" : 5}')$n), 5L)
eq("array spacing variants",
   sum(counts_long("a", '[ { "year" : 2020 , "cited_by_count" : 5 } ]')$n), 5L)

# -- counts_matrix zero-fills and clips to the trustworthy window -----------
m <- counts_matrix(c("a", "b"), c(obj, arr))
eq("matrix spans 2012-2025 only", colnames(m), as.character(2012:2025))
eq("2026 is dropped as a part year", m["b", "2025"], 0L)
eq("array row keeps its in-window year", m["b", "2023"], 2L)
eq("object row keeps both years", unname(m["a", c("2013", "2019")]), c(2L, 1L))
eq("absent years are zero, not missing", m["a", "2016"], 0L)
ok("nothing is NA", !anyNA(m))

pre2012 <- '{"2008": 7, "2014": 3}'
m2 <- counts_matrix("c", pre2012)
eq("years before 2012 are dropped", sum(m2), 3L)

# Row order follows the ids given, even when ids repeat or are unsorted.
m3 <- counts_matrix(c("b", "a"), c(arr, obj))
eq("row order follows the ids given", unname(m3["a", "2019"]), 1L)

# -- window_usable ----------------------------------------------------------
# Retracted 2018, published 2010: k=2 needs 2016-2017 and 2019-2020, all inside
# 2012-2025, so it is usable even though the paper's 2010-2011 citations are
# invisible. That is the whole reason the rule is a window rule.
ok("old paper, recent retraction, usable at k=2", window_usable(2018L, 2010L, 2L))
ok("k=2 fails when the pre-period predates 2012", !window_usable(2013L, 2005L, 2L))
ok("k=1 passes at the 2012 floor", window_usable(2013L, 2005L, 1L))
ok("fails when the paper is younger than the pre-period",
   !window_usable(2018L, 2017L, 2L))
ok("passes when publication is exactly k years before",
   window_usable(2018L, 2016L, 2L))
ok("fails when the post-period runs past 2025", !window_usable(2024L, 2010L, 2L))
ok("passes when the post-period ends exactly at 2025",
   window_usable(2023L, 2010L, 2L))
ok("missing years are not usable", !window_usable(NA_integer_, 2010L, 1L))
ok("missing publication year is not usable", !window_usable(2018L, NA_integer_, 1L))
eq("vectorised", window_usable(c(2018L, 2013L), c(2010L, 2005L), 2L), c(TRUE, FALSE))

# -- window_sums ------------------------------------------------------------
# Citations in 2015..2019 = 1,2,3,4,5. Retraction 2017, k=2:
# before = 2015+2016 = 3, after = 2018+2019 = 9, and 2017 is excluded.
full <- '{"2015": 1, "2016": 2, "2017": 3, "2018": 4, "2019": 5}'
m4 <- counts_matrix("d", full)
s <- window_sums(m4, 2017L, 2L)
eq("before excludes the retraction year", s$before, 3L)
eq("after excludes the retraction year", s$after, 9L)
s1 <- window_sums(m4, 2017L, 1L)
eq("k=1 before", s1$before, 2L)
eq("k=1 after", s1$after, 4L)

m5 <- counts_matrix(c("d", "e"), c(full, '{"2018": 6}'))
s2 <- window_sums(m5, c(2017L, 2017L), 1L)
eq("vectorised before", s2$before, c(2L, 0L))
eq("vectorised after", s2$after, c(4L, 6L))

# A window running off either end of the matrix must come back missing. R reads
# a zero or negative column index as "drop this column", so without the bounds
# check the 2012 case silently returned the sum of all the other years.
off_low <- window_sums(m4, 2012L, 2L)
ok("a pre-window before 2012 is NA, not a wrong number", is.na(off_low$before))
ok("its post-window is NA too", is.na(off_low$after))
off_high <- window_sums(m4, 2025L, 2L)
ok("a post-window past 2025 is NA, not an error", is.na(off_high$after))
ok("event exactly on the first usable year works",
   !is.na(window_sums(m4, 2013L, 1L)$before))
ok("event exactly on the last usable year works",
   !is.na(window_sums(m4, 2024L, 1L)$after))
ok("a missing event year is NA", is.na(window_sums(m4, NA_integer_, 1L)$before))

# -- history_visible --------------------------------------------------------
# Empty yearly figures with citations on the record means the citations are
# outside the window, not absent.
hv <- counts_matrix(c("p", "q", "r"), c("[]", "[]", '{"2015": 4}'))
eq("empty counts with citations is not a visible history",
   history_visible(hv, c(3L, 0L, 4L)), c(FALSE, TRUE, TRUE))
eq("a partly visible history is flagged",
   history_visible(counts_matrix("s", '{"2015": 4}'), 9L), FALSE)
eq("missing cited_by_count counts as zero",
   history_visible(counts_matrix("t", "[]"), NA_integer_), TRUE)

cat("\n", if (fails) sprintf("%d FAILED", fails) else "all passed", "\n", sep = "")
quit(status = if (fails) 1L else 0L)
