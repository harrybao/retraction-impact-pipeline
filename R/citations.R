# Yearly citation counts, and the window in which they can be trusted.
#
# Both sides of the comparison carry OpenAlex's `counts_by_year`, but in two
# shapes: the Python collector stored the retracted papers as an object,
# {"2019": 1, "2013": 2}, and the R control-group collector kept OpenAlex's own
# array, [{"year":2019,"cited_by_count":1}, ...]. Same numbers, so one reader
# handles both.
#
# The window matters more than the shape. OpenAlex reports yearly citations
# only from 2012, and a year with no citations is left out entirely rather than
# written as zero. Inside 2012 onwards an absent year therefore means "nobody
# cited it", which is a real zero and safe to use; before 2012 an absent year
# means "not reported", which is not. Collection ran in 2026-08, so 2026 is a
# part-year and cannot be compared with a full one.

CIT_FIRST_YEAR <- 2012L  # OpenAlex reports no yearly figures before this
CIT_LAST_YEAR <- 2025L   # 2026 was still in progress when the data was taken

# Long format: one row per work per year with at least one citation. Vectorised
# over the whole column; ids repeat, so the caller keeps them unique if needed.
counts_long <- function(id, json) {
  json <- as.character(json)
  json[is.na(json)] <- ""
  # {"2019": 1}  and  [{"year":2019,"cited_by_count":1}]  in one pass.
  pat <- '"([0-9]{4})"\\s*:\\s*([0-9]+)|"year"\\s*:\\s*([0-9]{4})\\s*,\\s*"cited_by_count"\\s*:\\s*([0-9]+)'
  hits <- regmatches(json, gregexpr(pat, json, perl = TRUE))
  n <- lengths(hits)
  flat <- unlist(hits, use.names = FALSE)
  if (!length(flat)) {
    return(data.frame(id = character(), year = integer(), n = integer(),
                      stringsAsFactors = FALSE))
  }
  nums <- regmatches(flat, gregexpr("[0-9]+", flat))
  year <- as.integer(vapply(nums, `[`, character(1), 1L))
  cnt <- as.integer(vapply(nums, `[`, character(1), 2L))
  data.frame(id = rep(id, n), year = year, n = cnt, stringsAsFactors = FALSE)
}

# A wide matrix of citations per work per year, zero-filled across the whole
# trustworthy window. Rows are `ids` in the order given.
counts_matrix <- function(ids, json, first = CIT_FIRST_YEAR, last = CIT_LAST_YEAR) {
  long <- counts_long(seq_along(ids), json)
  long <- long[long$year >= first & long$year <= last, ]
  m <- matrix(0L, nrow = length(ids), ncol = last - first + 1L,
              dimnames = list(ids, as.character(first:last)))
  if (nrow(long)) {
    idx <- cbind(as.integer(long$id), long$year - first + 1L)
    # A work can repeat a year only if the source did; sum to be safe.
    m[idx] <- m[idx] + long$n
  }
  m
}

# Can this work carry a symmetric comparison of k years each side of `event`?
# The event year itself is never used: OpenAlex counts by year, not by month,
# so a citation in the retraction year cannot be placed before or after it.
window_usable <- function(event_year, pub_year, k,
                          first = CIT_FIRST_YEAR, last = CIT_LAST_YEAR) {
  ok <- !is.na(event_year) & !is.na(pub_year)
  ok & event_year - k >= first &        # the "before" years are reported at all
    event_year - k >= pub_year &        # and the work already existed then
    event_year + k <= last              # and the "after" years are complete
}

# Citations in the k years before and the k years after `event_year`, read off
# a matrix from counts_matrix(). One row per work; NA where the window falls
# outside the reported years.
#
# The bounds check is not defensive decoration. The column index here is
# arithmetic on a year, and a year before the matrix starts makes that index
# zero or negative - which in R means "drop these columns", not "out of range".
# Before this check, asking for a 2012 event with k = 2 returned the sum of
# every year except the first two and called it the pre-window: a wrong number,
# silently, and wrong in the direction of a larger effect.
window_sums <- function(m, event_year, k, first = CIT_FIRST_YEAR) {
  stopifnot(k >= 1L, nrow(m) == length(event_year))
  last <- first + ncol(m) - 1L
  col <- function(y) y - first + 1L
  before <- after <- rep(NA_integer_, nrow(m))
  inside <- !is.na(event_year) & event_year - k >= first & event_year + k <= last
  for (i in which(inside)) {
    e <- event_year[i]
    before[i] <- sum(m[i, col(e - k):col(e - 1L)])
    after[i] <- sum(m[i, col(e + 1L):col(e + k)])
  }
  data.frame(before = before, after = after, stringsAsFactors = FALSE)
}

# Are this work's yearly figures the whole story, or does it have citations the
# window cannot see?
#
# An empty counts_by_year with a non-zero cited_by_count is not a work nobody
# cited - it is a work whose citations all predate 2012, or one OpenAlex gave
# no yearly breakdown for. Zero-filling it, which is what a count matrix does,
# turns "unknown" into "none". That is harmless in a ratio of sums, where such
# a work contributes nothing to either side, but it corrupts any measure of
# whether a work was cited at all. 749 retracted papers and 1,725 control rows
# are in this state.
history_visible <- function(m, cited_by_count) {
  cited <- suppressWarnings(as.integer(cited_by_count))
  cited[is.na(cited)] <- 0L
  rowSums(m) >= cited
}
