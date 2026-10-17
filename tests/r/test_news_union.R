# Checks for R/news_union.R. Run from the project root:
#   Rscript tests/r/test_news_union.R
#
# The four-route news table (Altmetric and PlumX, paper and notice DOIs) was
# counted by hand on 27 September and is now built by
# analysis/news_coverage_union.R. What can go wrong in a union over four
# overlapping routes is counting a paper twice, crediting a mass retraction
# as individual coverage, or counting a paper the corpus does not have - so
# those are what is checked, on a fixture small enough to count by eye.

source("R/news_union.R")

fails <- 0L
ok <- function(label, cond) {
  if (isTRUE(cond)) cat("ok   ", label, "\n")
  else { fails <<- fails + 1L; cat("FAIL ", label, "\n") }
}
val <- function(res, m) res$papers[res$measure == m]
pct <- function(res, m) res$share_pct[res$measure == m]
mk_map <- function(doi, papers)
  data.frame(retraction_doi = rep(doi, lengths(papers)),
             original_doi = unlist(papers),
             n_papers = rep(lengths(papers), lengths(papers)),
             stringsAsFactors = FALSE)
union_of <- function(...) {
  a <- list(universe = character(), alt_paper = character(), plumx_paper = character(),
            alt_notice = character(), plumx_notice = character(),
            map = mk_map(character(), list()))
  b <- list(...)
  a[names(b)] <- b
  do.call(news_coverage_union, a)
}
ALL <- "ALL FOUR"
MASS_ONLY <- "  papers reached only through mass-retraction notices"
LEFT_OUT <- "ALL FOUR, mass-retraction-only papers left out"

# -- PlumX's news count, read from detail_json ---------------------------------
ok("PlumX news count is read from detail_json",
   identical(plumx_news_count('{"mention.NEWS_COUNT": 3}'), 3L))
ok("among other sources",
   identical(plumx_news_count(paste0('{"capture.READER_COUNT": 25, ',
                                     '"mention.NEWS_COUNT": 12, "mention.ALL_BLOG_COUNT": 1}')),
             12L))
ok("no news key, empty JSON or none at all is 0, without warnings",
   identical(withCallingHandlers(
     plumx_news_count(c('{"mention.ALL_BLOG_COUNT": 1}', "", NA)),
     warning = function(w) stop("warned: ", conditionMessage(w))), c(0L, 0L, 0L)))
for (bad in c('bad "mention.NEWS_COUNT": 3', '{"mention.NEWS_COUNT":"3"}',
              '{"mention.NEWS_COUNT":-1}', '{"mention.NEWS_COUNT":1.5}',
              '{"mention.NEWS_COUNT":2147483648}',
              '{"mention.NEWS_COUNT":1,"mention.NEWS_COUNT":2}')) {
  ok("invalid or ambiguous PlumX counts fail instead of becoming zero coverage",
     inherits(try(plumx_news_count(bad), silent = TRUE), "try-error"))
}
ok("valid integer-valued JSON exponent is parsed as its actual count",
   identical(plumx_news_count('{"mention.NEWS_COUNT":1e3}'), 1000L))
ok("a nested key is not the collector's top-level count",
   identical(plumx_news_count('{"nested":{"mention.NEWS_COUNT":3}}'), 0L))

# -- one paper, all four routes ----------------------------------------------
one <- union_of(universe = c("10.1/p", "10.1/quiet"),
                alt_paper = "10.1/p", plumx_paper = " 10.1/P ",
                alt_notice = "10.1/n", plumx_notice = "10.1/N",
                map = mk_map("10.1/n", list("10.1/p")))
ok("a paper on every route is on each route once",
   all(one$papers[1:4] == 1))
ok("and counts once in the union of all four", val(one, ALL) == 1)
ok("DOIs match whatever their case and surrounding spaces",
   val(one, "PlumX, article DOI (count only)") == 1)
ok("shares are over the whole corpus, uncovered papers included",
   pct(one, ALL) == 50)
ok("a paper reached on its own DOI is never mass-only", val(one, MASS_ONLY) == 0)

# -- a fixture with every case at once ------------------------------------------
# 17 papers. "all" is on every route; notice n-three retracts t1-t3 and has
# PlumX news only; n-mass retracts m1-m10 (a mass retraction) with Altmetric
# news, and m1 is also under its own notice with PlumX news; quiet1 and quiet2
# have nothing (quiet2's notice has no news); n-out retracts a paper outside
# the corpus; "self" is a record whose notice DOI is its own DOI.
m <- paste0("10.1/m", 1:10)
t <- paste0("10.1/t", 1:3)
U <- c("10.1/all", t, m, "10.1/quiet1", "10.1/quiet2", "10.1/self")
map <- rbind(mk_map("10.1/n-all", list("10.1/all")),
             mk_map("10.1/n-three", list(t)),
             mk_map("10.1/n-mass", list(m)),
             mk_map("10.1/n-m1", list("10.1/m1")),
             mk_map("10.1/n-quiet", list("10.1/quiet2")),
             mk_map("10.1/n-out", list("10.1/outside")),
             mk_map("10.1/self", list("10.1/self")))
args <- list(universe = U, alt_paper = "10.1/all", plumx_paper = "10.1/all",
             alt_notice = c("10.1/n-all", "10.1/n-mass", "10.1/n-out", "10.1/self"),
             plumx_notice = c("10.1/n-all", "10.1/n-three", "10.1/n-m1"), map = map)
r <- do.call(union_of, args)

ok("a notice reaches every paper it retracts",
   val(r, "PlumX, notice DOI (count only)") == 5)        # all, t1-t3, m1
ok("a paper outside the corpus is not counted",
   val(r, "Altmetric, notice DOI") == 11)                 # all, m1-m10; not 'outside'
ok("papers with no coverage are not counted", val(r, ALL) == 14)
ok("the union is not the sum of the routes",
   val(r, "Altmetric article or notice") == 11 &&
   val(r, "Altmetric article or notice, or PlumX article (previous total)") == 11)
ok("PlumX's notice route adds only papers no other route had",
   val(r, "  added by PlumX notice DOI alone") == 3)      # t1-t3; m1 was there
ok("and says how many of those came through small notices",
   val(r, "    of which via notices retracting <10 papers") == 3)
ok("the Altmetric/PlumX-only split adds up to the total",
   val(r, "  papers reached through Altmetric news counts") +
   val(r, "  PlumX only (counts, no date, no text)") == val(r, ALL))
ok("summary coverage does not claim post text or dates were checked",
   !any(grepl("dated|text-bearing", r$measure)))
ok("a mass retraction's papers are mass-only unless something else reaches them",
   val(r, MASS_ONLY) == 9)                                # m2-m10; m1 has its own notice
ok("and the total without them leaves exactly those out",
   val(r, LEFT_OUT) == 5 && val(r, LEFT_OUT) + val(r, MASS_ONLY) == val(r, ALL))
ok("shares use the corpus size", pct(r, ALL) == round(100 * 14 / 17, 1))

# -- a notice whose DOI is the paper's own -----------------------------------------
# Not a notice route: both collectors and the exporter leave such pairs out.
# The 27 September hand count kept one, which is its one-paper difference.
ok("a self pair is not counted on the notice route, nor anywhere else",
   val(r, "Altmetric, notice DOI") == 11 && val(r, ALL) == 14)
kept <- do.call(union_of, c(args, list(keep_self_pairs = TRUE)))
ok("and is when asked to count the hand count's way",
   val(kept, "Altmetric, notice DOI") == 12 && val(kept, ALL) == 15)

# -- the mass threshold ------------------------------------------------------------
nine <- union_of(universe = paste0("10.1/q", 1:9), alt_notice = "10.1/n9",
                 map = mk_map("10.1/n9", list(paste0("10.1/q", 1:9))))
ok("a notice retracting nine papers is individual coverage", val(nine, MASS_ONLY) == 0)
ten <- union_of(universe = paste0("10.1/q", 1:10), alt_notice = "10.1/n10",
                map = mk_map("10.1/n10", list(paste0("10.1/q", 1:10))))
ok("ten is a mass retraction", val(ten, MASS_ONLY) == 10 && val(ten, LEFT_OUT) == 0)
own <- union_of(universe = paste0("10.1/q", 1:10), plumx_paper = "10.1/q1",
                alt_notice = "10.1/n10",
                map = mk_map("10.1/n10", list(paste0("10.1/q", 1:10))))
ok("a paper under a mass notice that has news on its own DOI is not mass-only",
   val(own, MASS_ONLY) == 9 && val(own, LEFT_OUT) == 1)
# A small notice counts as individual coverage only when it has news itself.
# Not hypothetical: 366 of the 1,531 mass-only papers in the collected data are
# also under a notice retracting fewer than 10 papers that has no news on
# either source (data/reports/news-coverage-union-review-2026-10-13.csv).
# Crediting them would move 366 papers into the trusted total.
eoc <- union_of(universe = paste0("10.1/q", 1:10), plumx_notice = "10.1/n10",
                map = rbind(mk_map("10.1/n10", list(paste0("10.1/q", 1:10))),
                            mk_map("10.1/eoc", list("10.1/q1"))))
ok("a small notice without news does not make a mass-retraction paper individual",
   val(eoc, MASS_ONLY) == 10 && val(eoc, LEFT_OUT) == 0 &&
   val(eoc, "    of which via notices retracting <10 papers") == 0)
unknown <- mk_map("10.1/nx", list("10.1/x"))
unknown$n_papers <- NA
ux <- union_of(universe = "10.1/x", plumx_notice = "10.1/nx", map = unknown)
ok("a notice of unknown size is not credited as individual coverage",
   val(ux, MASS_ONLY) == 1 &&
   val(ux, "    of which via notices retracting <10 papers") == 0)

cat("\n", if (fails) sprintf("%d FAILED", fails) else "all passed", "\n", sep = "")
quit(status = if (fails) 1L else 0L)
