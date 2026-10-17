# Where does OpenAlex's counts_by_year actually start and stop?
#
# counts_by_year omits years with no citations, so the earliest year present
# for one work is not the window's edge - it is that work's first citation.
# Across tens of thousands of works, though, the edge shows up as a hard floor:
# no work, however old or heavily cited, reports a year before it. This script
# finds that floor and ceiling empirically, because it decides whether a
# symmetric +/-k year comparison around a retraction sits inside the data.
#
#   Rscript analysis/citation_window_probe.R

counts_long <- function(id, json) {
  json[is.na(json)] <- ""
  hits <- regmatches(json, gregexpr('"[0-9]{4}"\\s*:\\s*[0-9]+', json, perl = TRUE))
  n <- lengths(hits)
  flat <- unlist(hits, use.names = FALSE)
  data.frame(id = rep(id, n),
             year = as.integer(substr(flat, 2L, 5L)),
             n = as.integer(sub('^"[0-9]{4}"\\s*:\\s*', "", flat)),
             stringsAsFactors = FALSE)
}

report <- function(label, id, json, pub_year, cited) {
  long <- counts_long(id, json)
  cat("\n==", label, "==\n")
  cat("works with at least one year:", length(unique(long$id)), "\n")
  cat("years present, earliest to latest:", min(long$year), "-", max(long$year), "\n")
  cat("\ncitations recorded per year (all works pooled):\n")
  print(tapply(long$n, long$year, sum))
  f <- factor(long$id, levels = id)
  first <- as.integer(tapply(long$year, f, min))
  cat("\nearliest year present per work:\n")
  print(sort(table(first), decreasing = TRUE)[1:12])
  # If the floor is real, old works pile up on it. Split by publication year.
  old <- !is.na(pub_year) & pub_year <= 2005L
  if (any(old & !is.na(first))) {
    cat("\nworks published 2005 or earlier (", sum(old), "), earliest year present:\n", sep = "")
    print(sort(table(first[old]), decreasing = TRUE)[1:8])
  }
  inwin <- as.integer(tapply(long$n, f, sum))
  ok <- !is.na(cited) & !is.na(inwin) & cited <= inwin
  cat("\nwhole history visible (cited_by_count accounted for): ", sum(ok, na.rm = TRUE),
      " of ", sum(!is.na(cited)), "\n", sep = "")
  cat("of works published 2012 or later: ",
      sum(ok & !is.na(pub_year) & pub_year >= 2012L, na.rm = TRUE), " of ",
      sum(!is.na(pub_year) & pub_year >= 2012L), "\n", sep = "")
}

eng <- utils::read.csv(file.path("data", "clean", "openalex_engagement.csv"),
                       stringsAsFactors = FALSE, fileEncoding = "UTF-8-BOM")
eng <- eng[!is.na(eng$openalex_id) & eng$openalex_id != "", ]
report("retracted papers", eng$doi, eng$counts_by_year,
       suppressWarnings(as.integer(eng$original_year)),
       suppressWarnings(as.integer(eng$cited_by_count)))

ctl <- utils::read.csv(file.path("data", "controls", "controls_v1.csv"),
                       stringsAsFactors = FALSE)
ctl <- ctl[!duplicated(ctl$control_doi), ]
report("controls", ctl$control_doi, ctl$counts_by_year,
       suppressWarnings(as.integer(ctl$control_year)),
       suppressWarnings(as.integer(ctl$cited_by_count)))

cat("\nretractions by year (all papers with a date):\n")
print(table(suppressWarnings(as.integer(eng$retraction_year))))
