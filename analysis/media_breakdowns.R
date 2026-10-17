# Media after retraction, broken down in the client's order: reason, country,
# publisher, discipline.
#
#   Rscript analysis/media_breakdowns.R
#
# Reads the same answers as analysis/media_classification.R (the model's task A
# answers, plus the items answered A0 by rule) and asks, for each value of each
# dimension: of the papers mentioned after their retraction, how many were
# ever described as retracted, and what share of the mentions say so.
#
# Two scopes, side by side, because only news and blogs were validated against
# the labellers so far: news and blogs, and every source with text. Social
# media joins the headline once round 2 has checked the model on it.
#
# A paper with two reasons, countries or subjects counts under each - as in the
# citation and notice breakdowns - so the rows of one table do not sum to the
# total. Values with fewer than MIN_PAPERS papers are left out, and the count
# of them is printed.
#
# Writes data/reports/media-by-{reason,country,publisher,discipline}.csv

source("R/labelling.R")
source("R/llm.R")
source("R/analysis_data.R")
source("R/citation_panel.R")      # panel_dimensions()
source("R/publisher_families.R")

MIN_PAPERS <- 30L
CORPUS <- file.path("data", "llm", "corpus")
OUT <- file.path("data", "reports")
slug <- gsub("[^A-Za-z0-9]+", "-", LLM_MODEL)

items <- utils::read.csv(file.path(CORPUS, "items_A.csv"), colClasses = "character",
                         encoding = "UTF-8")
f <- list.files(CORPUS, sprintf("^%s_A_A-v2-.*[.]csv$", slug), full.names = TRUE)
ans <- llm_read(f[1])[, c("id", "label", "error")]
byrule <- utils::read.csv(file.path(CORPUS, "answers_A_by_rule.csv"), colClasses = "character",
                          encoding = "UTF-8")
ans <- rbind(ans, data.frame(id = byrule$id, label = byrule$label, error = NA_character_))
d <- merge(items, ans, by = "id")
d <- d[is.na(d$error) & d$relative_to_retraction %in% "after", ]
d$told <- d$label == "A2"
d$validated <- d$kind %in% c("news", "blogs")
cat("mentions after retraction:", nrow(d), " on", length(unique(d$doi)), "papers\n")

rec <- utils::read.csv(file.path("data", "clean", "records_clean.csv"), colClasses = "character",
                       encoding = "UTF-8")
rec <- rec[!is.na(rec$original_doi) & nzchar(rec$original_doi), ]
dims <- panel_dimensions()
to_doi <- function(long, col) {
  long$doi <- rec$original_doi[match(as.character(long$record_id), rec$record_id)]
  long <- long[!is.na(long$doi), ]
  unique(data.frame(doi = long$doi, value = long[[col]], stringsAsFactors = FALSE))
}
DISCIPLINE_NAMES <- c(BLS = "Basic Life Sciences", HSC = "Health Sciences",
                      "B/T" = "Business and Technology", PHY = "Physical Sciences",
                      SOC = "Social Sciences", ENV = "Environmental Sciences",
                      HUM = "Humanities")
subj <- dims$subject
code <- ifelse(is.na(subj$subject_code) | subj$subject_code == "", "(no code)", subj$subject_code)
named <- unname(DISCIPLINE_NAMES[code])
subj$discipline <- ifelse(is.na(named), code, sprintf("%s (%s)", named, code))
pub <- unique(data.frame(doi = rec$original_doi, value = publisher_family(rec$publisher),
                         stringsAsFactors = FALSE))
pub <- pub[!is.na(pub$value) & nzchar(pub$value), ]

breakdown <- function(map, name) {
  rows <- list()
  for (v in unique(map$value)) {
    dois <- unique(map$doi[map$value == v])
    s <- d[d$doi %in% dois, ]
    papers <- unique(s$doi)
    if (length(papers) < MIN_PAPERS) next
    sv <- s[s$validated, ]
    pv <- unique(sv$doi)
    rows[[length(rows) + 1L]] <- data.frame(
      value = v,
      papers_mentioned_after = length(papers),
      papers_ever_told_pct = round(100 * length(unique(s$doi[s$told])) / length(papers), 1),
      mentions_after = nrow(s),
      mentions_told_pct = round(100 * mean(s$told), 1),
      news_blogs_papers = length(pv),
      news_blogs_papers_ever_told_pct = if (length(pv)) round(100 * length(unique(sv$doi[sv$told])) / length(pv), 1) else NA,
      news_blogs_mentions_told_pct = if (nrow(sv)) round(100 * mean(sv$told), 1) else NA,
      stringsAsFactors = FALSE)
  }
  out <- do.call(rbind, rows)
  out <- out[order(-out$papers_mentioned_after), ]
  dropped <- length(unique(map$value)) - nrow(out)
  cat(sprintf("\n== %s: %d values with at least %d papers mentioned after retraction (%d smaller left out)\n",
              name, nrow(out), MIN_PAPERS, dropped))
  print(utils::head(out[, c("value", "papers_mentioned_after", "papers_ever_told_pct",
                            "mentions_told_pct", "news_blogs_mentions_told_pct")], 10),
        row.names = FALSE)
  p <- file.path(OUT, sprintf("media-by-%s.csv", name))
  utils::write.csv(out, p, row.names = FALSE, fileEncoding = "UTF-8")
  cat("written:", p, "\n")
  invisible(out)
}

breakdown(to_doi(dims$reason, "reason"), "reason")
breakdown(to_doi(dims$country, "country"), "country")
breakdown(pub, "publisher")
breakdown(to_doi(subj, "discipline"), "discipline")
