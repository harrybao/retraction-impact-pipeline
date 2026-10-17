# Checks for R/did.R. Run from the project root:
#   Rscript tests/r/test_did.R
#
# The estimator is arithmetic on four numbers per stratum, so it can be checked
# by hand, and it is - because the failure mode here is not a crash. Every one
# of these functions returns a plausible number when it is wrong, and that
# number goes into a report.

source("R/did.R")

fails <- 0L
ok <- function(label, cond) {
  if (isTRUE(cond)) cat("ok   ", label, "\n")
  else { fails <<- fails + 1L; cat("FAIL ", label, "\n") }
}
near <- function(label, got, want, tol = 1e-6) {
  if (!isTRUE(abs(got - want) < tol))
    cat("      got:", format(got, digits = 8), " want:", format(want, digits = 8), "\n")
  ok(label, isTRUE(abs(got - want) < tol))
}

cell <- function(t_pre, t_post, c_pre, c_post, n_treated = 1L, n_control = 3L,
                 stratum = NULL) {
  n <- length(t_pre)
  data.frame(stratum = if (is.null(stratum)) paste0("s", seq_len(n)) else stratum,
             t_pre = t_pre, t_post = t_post, n_treated = rep(n_treated, length.out = n),
             c_pre = c_pre, c_post = c_post, n_control = rep(n_control, length.out = n),
             stringsAsFactors = FALSE)
}

# -- mh_ratio, by hand ------------------------------------------------------
# One stratum: treated halve, controls hold steady. n = 10+5+10+10 = 35.
# num = 5*10/35, den = 10*10/35, ratio = 0.5.
near("one stratum, treated halve against flat controls",
     mh_ratio(cell(10, 5, 10, 10)), 0.5)
near("pooled agrees on one stratum", pooled_ratio(cell(10, 5, 10, 10)), 0.5)

# No change anywhere is exactly 1.
near("no change is 1", mh_ratio(cell(10, 10, 10, 10)), 1)
# Treated and controls move together: the ratio still says nothing happened.
near("a common trend divides out", mh_ratio(cell(10, 20, 10, 20)), 1)
near("a common decline divides out", mh_ratio(cell(20, 10, 20, 10)), 1)

# Two strata worked through longhand.
c2 <- cell(c(10, 40), c(5, 10), c(10, 40), c(10, 40))
n1 <- 10 + 5 + 10 + 10; n2 <- 40 + 10 + 40 + 40
near("two strata, Mantel-Haenszel",
     mh_ratio(c2), (5 * 10 / n1 + 10 * 40 / n2) / (10 * 10 / n1 + 40 * 40 / n2))

# -- the estimator the header warns about -----------------------------------
# A thin stratum whose treated count rises from 1 to 5 contributes 5 to a mean
# of ratios, and almost nothing to a ratio of sums.
thin <- cell(c(1, 1000), c(5, 500), c(1, 1000), c(1, 1000), n_treated = 1L)
ok("the naive average is dragged up by a thin stratum", naive_ratio(thin) > 2)
ok("the Mantel-Haenszel estimate is not", mh_ratio(thin) < 0.75)
ok("and the pooled estimate is not either", pooled_ratio(thin) < 0.75)

# -- degenerate input returns NA, never a number ----------------------------
ok("no strata is NA", is.na(mh_ratio(empty_cells())))
ok("all-zero cells are NA", is.na(mh_ratio(cell(0, 0, 0, 0))))
ok("a zero denominator is NA", is.na(mh_ratio(cell(0, 5, 10, 0))))
ok("pooled with no pre-period citations is NA", is.na(pooled_ratio(cell(0, 5, 10, 10))))

# -- offsets ----------------------------------------------------------------
# k = 2 around 2018 touches 2016..2020, all inside 2012-2025.
ok("k=2 at 2018 is usable", offsets_usable(2018L, 2010L, c(-2:-1, 1:2)))
ok("the pre-period may not predate 2012", !offsets_usable(2013L, 2005L, c(-2:-1, 1:2)))
ok("the paper must exist at the start of the window",
   !offsets_usable(2018L, 2017L, c(-2:-1, 1:2)))
ok("the post-period may not run past 2025",
   !offsets_usable(2024L, 2010L, c(-2:-1, 1:2)))
ok("a single-year offset has its own bounds",
   offsets_usable(2013L, 2005L, c(-1L, 1L)))

m <- counts_matrix(c("a", "b"), c('{"2015": 1, "2016": 2, "2017": 4, "2018": 8, "2019": 16}',
                                  '{"2018": 3}'))
near("offset sums pick the right years", offset_sums(m, c(2017L, 2017L), -2:-1)[1], 3)
near("and the right years after", offset_sums(m, c(2017L, 2017L), 1:2)[1], 24)
near("a work with one year is summed correctly",
     offset_sums(m, c(2017L, 2017L), 1:2)[2], 3)
ok("the event year is never included",
   offset_sums(m, 2017L, c(-2:-1, 1:2))[1] == 3 + 24)
ok("an out-of-range window is NA", is.na(offset_sums(m, 2012L, -2:-1)[1]))

# -- did_cells on a panel built by hand -------------------------------------
# Two strata in one group. Treated papers lose citations, controls do not.
mk_panel <- function() {
  papers <- data.frame(
    doi = c("p1", "p2"), record_id = c(1L, 2L), group_id = c("G", "G"),
    event_year = c(2018L, 2018L), pub_year = c(2015L, 2015L),
    work_type = c("article", "article"), retraction_nature = c("Retraction", "Retraction"),
    publisher = "Pub", journal = "J", cited_by_count = c(12L, 12L),
    stringsAsFactors = FALSE)
  papers$row <- 1:2
  mp <- counts_matrix(papers$doi,
                      rep('{"2016": 5, "2017": 5, "2019": 2, "2020": 3}', 2))
  controls <- data.frame(
    control_doi = c("c1", "c2"), retracted_doi = c("p1", "p2"),
    group_id = c("G", "G"), match_level = c(1L, 1L), control_type = "article",
    pub_year = c(2015L, 2015L), event_year = c(2018L, 2018L),
    cited_by_count = c(20L, 20L), stringsAsFactors = FALSE)
  controls$row <- 1:2
  mc <- counts_matrix(controls$control_doi,
                      rep('{"2016": 5, "2017": 5, "2019": 5, "2020": 5}', 2))
  list(papers = papers, mp = mp, controls = controls, mc = mc,
       years = as.integer(colnames(mp)))
}
pan <- mk_panel()
cells <- did_cells_k(pan, 2L)
ok("one stratum results", nrow(cells) == 1L)
near("treated pre is summed across both papers", cells$t_pre, 20)
near("treated post likewise", cells$t_post, 10)
near("control pre", cells$c_pre, 20)
near("control post", cells$c_post, 20)
near("both papers counted", cells$n_treated, 2)
near("both controls counted", cells$n_control, 2)
near("the effect is a halving", mh_ratio(cells), 0.5)

# A narrowed treated arm must not narrow the control arm - that is how the
# dimension tables stay comparable with the headline.
one <- did_cells_k(pan, 2L, papers_subset = "p1")
near("subsetting the treated arm halves the treated side", one$t_pre, 10)
near("and leaves the control arm whole", one$c_pre, cells$c_pre)
near("and leaves the control count whole", one$n_control, cells$n_control)

# The fast path for dimension tables must agree with the slow one exactly.
# It exists only to avoid recomputing an identical control arm 150 times, so
# any disagreement between them is a bug, not a trade-off.
slow <- did_cells_k(pan, 2L, papers_subset = "p1")
fast <- retreat_cells(cells, "p1")
for (col in c("t_pre", "t_post", "n_treated", "c_pre", "c_post", "n_control")) {
  ok(paste("fast path agrees on", col), identical(slow[[col]], fast[[col]]))
}
ok("fast path keeps the same strata", identical(slow$stratum, fast$stratum))
ok("a subset matching no paper gives no cells",
   nrow(retreat_cells(cells, "nobody")) == 0L)

# Notice-typed works are the notice, not the paper.
pan2 <- pan
pan2$papers$work_type[1] <- "retraction"
near("notice-typed works are dropped by default",
     did_cells_k(pan2, 2L)$n_treated, 1)
near("and kept when asked for",
     did_cells_k(pan2, 2L, drop_notice_types = FALSE)$n_treated, 2)

# Only Retractions are the treatment by default.
pan3 <- pan
pan3$papers$retraction_nature[2] <- "Expression of concern"
near("an expression of concern is not a retraction",
     did_cells_k(pan3, 2L)$n_treated, 1)
near("unless natures says so",
     did_cells_k(pan3, 2L, natures = NULL)$n_treated, 2)

# A stratum with no control arm cannot contribute.
pan4 <- pan
pan4$controls <- pan4$controls[0, ]
ok("no controls means no cells", nrow(did_cells_k(pan4, 2L)) == 0L)
ok("and the estimate is NA, not a number", is.na(mh_ratio(did_cells_k(pan4, 2L))))

# -- the placebo window sits entirely before the event ----------------------
pl <- did_cells(pan, -4:-3, -2:-1)
ok("a placebo built from pre-event offsets runs", is.data.frame(pl))

# Offset 0 must be refused rather than silently summed. The analysis script
# once built its placebo post window as (-k+1):-1, which is 0:-1 at k=1 - the
# retraction year, the one year the design excludes. An earlier version of
# this test asserted the windows avoid 0 by checking a pair typed out by hand
# here, which is not the same as checking what the caller passes.
ok("offset 0 in the post window is refused",
   inherits(try(did_cells(pan, -2L, c(0L, -1L)), silent = TRUE), "try-error"))
ok("offset 0 in the pre window is refused",
   inherits(try(did_cells(pan, c(-1L, 0L), 1:2), silent = TRUE), "try-error"))
ok("the error says why",
   grepl("event year", tryCatch(did_cells(pan, -2L, 0L),
                                error = function(e) conditionMessage(e))))

# The expression the analysis script actually uses, checked at every k it runs.
for (k in 1:3) {
  pre <- (-2 * k):(-k - 1); post <- (-k):-1
  ok(sprintf("placebo windows at k=%d are both %d years wide", k, k),
     length(pre) == k && length(post) == k)
  ok(sprintf("placebo windows at k=%d stay before the event", k),
     max(c(pre, post)) < 0)
  ok(sprintf("placebo windows at k=%d do not overlap", k), max(pre) < min(post))
}

# -- boot_ci ----------------------------------------------------------------
many <- cell(rep(10, 50), rep(5, 50), rep(10, 50), rep(10, 50))
ci <- boot_ci(many, B = 50)
ok("a bootstrap on identical strata is tight",
   abs(ci[["lower"]] - 0.5) < 1e-6 && abs(ci[["upper"]] - 0.5) < 1e-6)
ok("a single stratum gives no interval", is.na(boot_ci(cell(10, 5, 10, 10))[["lower"]]))
ok("the bootstrap is reproducible",
   identical(boot_ci(many, B = 20, seed = 1), boot_ci(many, B = 20, seed = 1)))

cat("\n", if (fails) sprintf("%d FAILED", fails) else "all passed", "\n", sep = "")
quit(status = if (fails) 1L else 0L)
