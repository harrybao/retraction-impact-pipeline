# Checks for R/notice_news.R. Run from the project root:
#   Rscript tests/r/test_notice_news.R
#
# One rule, and it is worth a file of its own: a news article about a mass
# retraction is ONE article, not one per paper the notice covers. The largest
# notice in this corpus retracts 659 papers, so getting this wrong inflates the
# headline by a factor that nothing in the output would reveal - the number
# would just be large, and large was what the client asked for.

source("R/notice_news.R")

fails <- 0L
ok <- function(label, cond) {
  if (isTRUE(cond)) cat("ok   ", label, "\n")
  else { fails <<- fails + 1L; cat("FAIL ", label, "\n") }
}
val <- function(res, m) res$summary$value[res$summary$metric == m]
papers_covered <- "papers whose retraction was covered by news"
articles <- "news ARTICLES about retraction notices"

mk_notice <- function(doi, n_papers, news_count, found = 1)
  data.frame(retraction_doi = doi, n_papers = n_papers, news_count = news_count,
             found = found, social_count = 0, stringsAsFactors = FALSE)
mk_map <- function(doi, papers)
  data.frame(retraction_doi = rep(doi, lengths(papers)),
             original_doi = unlist(papers),
             n_papers = rep(lengths(papers), lengths(papers)),
             stringsAsFactors = FALSE)

# -- the rule ---------------------------------------------------------------
# One notice, 100 papers, 1 news article.
big <- mk_notice("10.1/mass", 100, 1)
big_map <- mk_map("10.1/mass", list(paste0("10.1/p", 1:100)))
res <- notice_news_tables(big, big_map)
ok("one article about a mass retraction stays one article", val(res, articles) == 1)
ok("all 100 papers are flagged as covered", val(res, papers_covered) == 100)
ok("and every one of them only via the mass notice",
   val(res, "  ... only via a mass-retraction notice") == 100)
ok("none is credited to an individual notice",
   val(res, "  ... via a notice retracting fewer than 10 papers") == 0)

# The same 100 papers with 5 articles: still 5 articles, not 500.
big5 <- mk_notice("10.1/mass", 100, 5)
ok("five articles stay five", val(notice_news_tables(big5, big_map), articles) == 5)

# -- an individual notice ---------------------------------------------------
one <- mk_notice("10.1/single", 1, 2)
one_map <- mk_map("10.1/single", list("10.1/paper"))
r1 <- notice_news_tables(one, one_map)
ok("a single-paper notice flags its paper", val(r1, papers_covered) == 1)
ok("and counts as individual",
   val(r1, "  ... via a notice retracting fewer than 10 papers") == 1)

# -- the threshold ----------------------------------------------------------
nine <- notice_news_tables(mk_notice("10.1/n", 9, 1),
                           mk_map("10.1/n", list(paste0("10.1/q", 1:9))))
ok("nine papers is not a mass retraction",
   val(nine, "  ... via a notice retracting fewer than 10 papers") == 9)
ten <- notice_news_tables(mk_notice("10.1/n", 10, 1),
                          mk_map("10.1/n", list(paste0("10.1/q", 1:10))))
ok("ten is", val(ten, "  ... only via a mass-retraction notice") == 10)

# -- a paper under two notices ----------------------------------------------
# Retracted twice, once in a purge and once on its own. The individual notice
# is the one a reader should trust, so that is what it counts as.
two <- rbind(mk_notice("10.1/mass", 50, 1), mk_notice("10.1/own", 1, 1))
two_map <- rbind(mk_map("10.1/mass", list(c("10.1/shared", paste0("10.1/x", 1:49)))),
                 mk_map("10.1/own", list("10.1/shared")))
r2 <- notice_news_tables(two, two_map)
ok("a paper under two notices is counted once", val(r2, papers_covered) == 50)
ok("and credited to the individual notice",
   val(r2, "  ... via a notice retracting fewer than 10 papers") == 1)
ok("leaving 49 mass-only", val(r2, "  ... only via a mass-retraction notice") == 49)

# -- a notice with no news --------------------------------------------------
none <- notice_news_tables(mk_notice("10.1/quiet", 5, 0),
                           mk_map("10.1/quiet", list(paste0("10.1/z", 1:5))))
ok("a notice with no news flags no papers", val(none, papers_covered) == 0)
ok("and contributes no articles", val(none, articles) == 0)

# -- articles from the detail pass ------------------------------------------
# One article mentioning two notices of the same purge is still one article.
nd <- rbind(mk_notice("10.1/m1", 30, 1), mk_notice("10.1/m2", 30, 1))
nd_map <- rbind(mk_map("10.1/m1", list(paste0("10.1/a", 1:30))),
                mk_map("10.1/m2", list(paste0("10.1/b", 1:30))))
news <- data.frame(retraction_doi = c("10.1/m1", "10.1/m2"),
                   post_id = c("http://news/one", "http://news/one"),
                   stringsAsFactors = FALSE)
rn <- notice_news_tables(nd, nd_map, news = news)
ok("one post mentioning two notices is one article", val(rn, articles) == 1)
ok("but both notices count as covered", val(rn, "notices covered by news") == 2)
ok("and all 60 papers are flagged", val(rn, papers_covered) == 60)

# Without the detail pass the summary counts stand in, and say so.
ok("the source of the article count is stated",
   grepl("detail pass has not run",
         notice_news_tables(nd, nd_map)$summary$note[
           notice_news_tables(nd, nd_map)$summary$metric == articles]))

# -- against the paper-level figure -----------------------------------------
pn <- c("10.1/p1", "10.1/OTHER")           # one overlaps, one does not
rp <- notice_news_tables(big, big_map, paper_news = pn)
ok("the paper-level figure is carried through",
   val(rp, "papers with news about the PAPER") == 2)
ok("the union is the union",
   val(rp, "papers with news about the paper OR its notice") == 101)
ok("and the notices' contribution is what they added",
   val(rp, "  ... added by asking about notices") == 99)
ok("case does not create a second paper",
   val(notice_news_tables(big, big_map, paper_news = "10.1/P1"),
       "papers with news about the paper OR its notice") == 100)

# -- one article, however many DOIs it mentions -----------------------------
# A piece about a retraction usually names the paper and the notice, so
# Altmetric files it under both DOIs. Adding the two tables counts it twice.
eq_url <- function(a, b) normalise_url(a) == normalise_url(b)
ok("http and https are one article", eq_url("http://x.com/a", "https://x.com/a"))
ok("www is not part of the identity", eq_url("https://www.x.com/a", "https://x.com/a"))
ok("a trailing slash is not either", eq_url("https://x.com/a/", "https://x.com/a"))
ok("nor is a fragment", eq_url("https://x.com/a#top", "https://x.com/a"))
ok("tracking parameters are dropped",
   eq_url("https://x.com/a?utm_source=t&utm_medium=e", "https://x.com/a"))
ok("fbclid too", eq_url("https://x.com/a?fbclid=123", "https://x.com/a"))
ok("a real query parameter is kept", !eq_url("https://x.com/a?id=7", "https://x.com/a"))
ok("different paths are different articles", !eq_url("https://x.com/a", "https://x.com/b"))
ok("different hosts are different articles", !eq_url("https://x.com/a", "https://y.com/a"))

# A post with no URL falls back to its own id and never merges with another.
k <- article_key(c(NA, "", "https://x.com/a"), c("p1", "p2", "p3"))
ok("two posts with no URL stay two articles", k[1] != k[2])
ok("and neither is merged with a real URL", !any(k[1:2] == k[3]))

paper_side <- data.frame(
  url = c("https://news.com/story", "https://news.com/other"),
  post_id = c("a", "b"), stringsAsFactors = FALSE)
notice_side <- data.frame(
  url = c("http://www.news.com/story/", "https://news.com/third"),
  post_id = c("c", "d"), stringsAsFactors = FALSE)
ok("the paper side alone has two", distinct_articles(paper_side) == 2)
ok("the notice side alone has two", distinct_articles(notice_side) == 2)
ok("together they are three, not four", distinct_articles(paper_side, notice_side) == 3)
ok("no arguments is zero", distinct_articles() == 0)
ok("an empty table contributes nothing",
   distinct_articles(paper_side, paper_side[0, ]) == 2)

res_dd <- notice_news_tables(
  mk_notice("10.1/n", 1, 1), mk_map("10.1/n", list("10.1/paper")),
  news = data.frame(retraction_doi = "10.1/n", post_id = "c",
                    url = "http://www.news.com/story/", stringsAsFactors = FALSE),
  paper_news = "10.1/paper", paper_news_posts = paper_side)
ok("the shared article is counted once across both tables",
   val(res_dd, "distinct news articles about retracted work, either identifier") == 2)
ok("and the double counting avoided is reported",
   val(res_dd, "  ... double counting avoided by matching on URL") == 1)

# Without the notice detail pass there is nothing to match against, and a line
# saying "either identifier" beside a paper-only count would be read as a total.
res_nd <- notice_news_tables(
  mk_notice("10.1/n", 1, 1), mk_map("10.1/n", list("10.1/paper")),
  paper_news = "10.1/paper", paper_news_posts = paper_side)
ok("no cross-table total is claimed before the detail pass",
   is.na(val(res_nd, "distinct news articles, either identifier")))
ok("and the reason is stated",
   grepl("detail pass",
         res_nd$summary$note[res_nd$summary$metric ==
                             "distinct news articles, either identifier"]))
ok("duplicate URLs within one table are still reported",
   grepl("already seen",
         res_nd$summary$note[res_nd$summary$metric ==
                             "news articles about the papers"]))

cat("\n", if (fails) sprintf("%d FAILED", fails) else "all passed", "\n", sep = "")
quit(status = if (fails) 1L else 0L)
