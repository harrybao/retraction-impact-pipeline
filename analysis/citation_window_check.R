# How much of the corpus can carry a before/after citation comparison?
#
# OpenAlex reports yearly citations only from 2012, and collection ran in
# 2026-08, so 2026 is a part year. A symmetric comparison of k years each side
# of a retraction is therefore possible only when both the k years before and
# the k years after fall inside 2012-2025, and when the paper was already
# published k years before it was retracted. See R/citations.R.
#
# The point of this script is to size the sample before the analysis is built:
# how many retracted papers qualify at k = 1, 2, 3, and how many of them still
# have matched controls that qualify on the same terms.
#
#   Rscript analysis/citation_window_check.R

source("R/citations.R")

rows <- list()
add <- function(group, metric, value) {
  rows[[length(rows) + 1L]] <<- data.frame(group = group, metric = metric,
                                           value = value, stringsAsFactors = FALSE)
}

cat("reading the retracted papers...\n")
eng <- utils::read.csv(file.path("data", "clean", "openalex_engagement.csv"),
                       stringsAsFactors = FALSE, fileEncoding = "UTF-8-BOM")
eng <- eng[!is.na(eng$openalex_id) & eng$openalex_id != "", ]
eng$retraction_year <- suppressWarnings(as.integer(eng$retraction_year))
eng$original_year <- suppressWarnings(as.integer(eng$original_year))

add("papers", "in openalex", nrow(eng))
add("papers", "retraction year and publication year known",
    sum(!is.na(eng$retraction_year) & !is.na(eng$original_year)))
add("papers", "retracted 2013-2024 (some window exists)",
    sum(eng$retraction_year >= CIT_FIRST_YEAR + 1L &
        eng$retraction_year <= CIT_LAST_YEAR - 1L, na.rm = TRUE))
for (k in 1:3) {
  add("papers", sprintf("usable at +/-%d years", k),
      sum(window_usable(eng$retraction_year, eng$original_year, k)))
}

cat("reading the control group (58 MB, this takes a moment)...\n")
ctl <- utils::read.csv(file.path("data", "controls", "controls_v1.csv"),
                       stringsAsFactors = FALSE)
ctl$control_year <- suppressWarnings(as.integer(ctl$control_year))
ctl$pseudo_year <- as.integer(substr(ctl$pseudo_retraction_date, 1L, 4L))

add("controls", "rows", nrow(ctl))
add("controls", "distinct controls", length(unique(ctl$control_doi)))
add("controls", "retracted papers covered", length(unique(ctl$retracted_doi)))

# A pair works only when both sides do. The control's clock is its
# pseudo-retraction date, which is the date its matched paper was retracted.
paper_k <- eng[c("doi", "retraction_year", "original_year")]
names(paper_k) <- c("doi", "paper_event", "paper_pub")
ctl <- merge(ctl, paper_k, by.x = "retracted_doi", by.y = "doi", all.x = TRUE)

for (k in 1:3) {
  ok <- window_usable(ctl$paper_event, ctl$paper_pub, k) &
    window_usable(ctl$pseudo_year, ctl$control_year, k)
  ok[is.na(ok)] <- FALSE
  per_paper <- table(ctl$retracted_doi[ok])
  add("pairs", sprintf("usable control rows at +/-%d years", k), sum(ok))
  add("pairs", sprintf("papers with >=1 usable control at +/-%d years", k),
      length(per_paper))
  add("pairs", sprintf("papers with 3 usable controls at +/-%d years", k),
      sum(per_paper >= 3L))
  if (k == 2L) {
    keep <- ctl[ok, ]
    add("pairs", "at +/-2 years: match level 1", sum(keep$match_level == 1L))
    add("pairs", "at +/-2 years: match level 2", sum(keep$match_level == 2L))
    add("pairs", "at +/-2 years: match level 3", sum(keep$match_level == 3L))
    add("pairs", "at +/-2 years: distinct controls", length(unique(keep$control_doi)))
    add("pairs", "at +/-2 years: controls used more than once",
        sum(table(keep$control_doi) > 1L))
    yr <- sort(table(keep$paper_event), decreasing = TRUE)
    add("pairs", "at +/-2 years: retraction years spanned", length(yr))
  }
}

out <- do.call(rbind, rows)
print(out, right = FALSE, row.names = FALSE)
dir.create(file.path("data", "reports"), showWarnings = FALSE, recursive = TRUE)
f <- file.path("data", "reports", "citation-window-check-2026-09-23.csv")
utils::write.csv(out, f, row.names = FALSE)
cat("\nwritten:", f, "\n")
