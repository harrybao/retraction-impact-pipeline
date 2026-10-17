# Stage 3, part 2: what retraction does to a paper's citations.
#
#   Rscript analysis/citation_impact.R
#
# Everything the client reads comes out of this one script, into
# data/reports/citation-impact-*.csv and two figures. The method is in
# R/did.R; this file only decides what to report and writes it down.
#
# The reporting order for the breakdowns is the client's own: reason, then
# country, then publisher, then discipline.

source("R/did.R")
source("R/publisher_families.R")

# k = 1 is the headline, because it covers the most papers - 34,277 against
# 14,741 at k=2. The longer window is not "better": requiring k whole years
# before the retraction throws out every paper retracted quickly, and most
# papers in this corpus were. k=2 and k=3 are reported beside it as stricter
# checks on a narrower, slower-retracted sample, and the report says so rather
# than presenting one number as the answer for all retractions.
K_HEADLINE <- 1L
# The robustness ladder and the breakdowns run at the headline window, so the
# report never has to explain why a subgroup was measured on a different
# sample from the total. It also doubles the papers behind every breakdown
# row, which is what the smaller countries and reasons need.
K_PRIMARY <- K_HEADLINE
K_ALL <- 1:3
B_MAIN <- 1000L          # bootstrap draws for the headline
B_DIM <- 200L            # and for each breakdown row
MIN_PAPERS <- 50L        # a breakdown row below this is not reported
OUT <- file.path("data", "reports")
dir.create(OUT, showWarnings = FALSE, recursive = TRUE)

write_table <- function(d, name) {
  f <- file.path(OUT, paste0("citation-impact-", name, ".csv"))
  utils::write.csv(d, f, row.names = FALSE)
  cat("written:", f, "\n")
  invisible(f)
}

panel <- citation_panel()
cat("panel:", nrow(panel$papers), "papers,", nrow(panel$controls), "control links\n\n")

# ------------------------------------------------------------------ funnel --
# Where the papers go. Read top to bottom, this is the answer to "why is the
# analysis on 14,000 papers when you collected 62,000".

cat("== funnel ==\n")
p <- panel$papers
steps <- list(
  c("retracted papers with an OpenAlex record", nrow(p)),
  c("retraction date and publication year known",
    sum(!is.na(p$event_year) & !is.na(p$pub_year))),
  c("nature is Retraction (not an expression of concern or correction)",
    sum(p$retraction_nature == "Retraction", na.rm = TRUE)),
  c("the OpenAlex record is the article, not the notice",
    sum(p$retraction_nature == "Retraction" &
        !(tolower(p$work_type) %in% NOTICE_WORK_TYPES), na.rm = TRUE)))
base_keep <- p$retraction_nature == "Retraction" &
  !(tolower(p$work_type) %in% NOTICE_WORK_TYPES)
base_keep[is.na(base_keep)] <- FALSE
for (k in K_ALL) {
  u <- base_keep & offsets_usable(p$event_year, p$pub_year, c(-k:-1, 1:k))
  u[is.na(u)] <- FALSE
  steps[[length(steps) + 1L]] <- c(sprintf("  ... and %d whole years each side fit 2012-2025", k),
                                   sum(u))
}
cells_by_k <- lapply(K_ALL, function(k) did_cells_k(panel, k))
names(cells_by_k) <- as.character(K_ALL)
for (k in K_ALL) {
  steps[[length(steps) + 1L]] <- c(sprintf("  ... and k=%d leaves it in a stratum that has controls", k),
                                   sum(cells_by_k[[as.character(k)]]$n_treated))
}
funnel <- data.frame(step = vapply(steps, `[`, character(1), 1),
                     papers = as.integer(vapply(steps, `[`, character(1), 2)),
                     stringsAsFactors = FALSE)
print(funnel, row.names = FALSE)
write_table(funnel, "funnel")

# ---------------------------------------------------------------- headline --

cat("\n== headline ==\n")
head_rows <- lapply(K_ALL, function(k) {
  cells <- cells_by_k[[as.character(k)]]
  r <- summarise_cells(cells, sprintf("k = %d years each side", k), B = B_MAIN)
  r$treated_change <- r$t_post / r$t_pre
  r$control_change <- r$c_post / r$c_pre
  r$naive_wrong <- naive_ratio(cells)
  r
})
headline <- do.call(rbind, head_rows)
headline$citations_lost <- round(headline$t_pre * headline$control_change - headline$t_post)
print(headline[c("group", "papers", "controls", "treated_change", "control_change",
                 "ratio_mh", "ci_lower", "ci_upper", "ratio_pooled", "naive_wrong")],
      row.names = FALSE, digits = 4)
write_table(headline, "headline")

# ----------------------------------------------------------- event study ----
# Each year against the year before the retraction. The years before the event
# are the test: if they sit at 1, the two arms were on the same path and the
# drop afterwards is the retraction. If they drift, some of the drop was
# already happening and the report has to say so.

cat("\n== event study ==\n")
es <- do.call(rbind, lapply(c(-4:-2, 1:4), function(tau) {
  r <- summarise_cells(did_cells(panel, -1L, tau), sprintf("%+d", tau), B = 300)
  r$tau <- tau
  r
}))
es <- rbind(data.frame(group = "-1", strata = NA_integer_, papers = NA_integer_,
                       controls = NA_integer_, t_pre = NA_integer_, t_post = NA_integer_,
                       c_pre = NA_integer_, c_post = NA_integer_, ratio_mh = 1,
                       ratio_pooled = 1, ci_lower = NA_real_, ci_upper = NA_real_,
                       tau = -1L),
            es)
es <- es[order(es$tau), ]
print(es[c("tau", "papers", "ratio_mh", "ci_lower", "ci_upper")],
      row.names = FALSE, digits = 4)
write_table(es, "event-study")

# ------------------------------------------------------------- placebo -----
# The whole comparison moved back k years, so both windows sit before the
# retraction and nothing has happened yet. The truth is 1.000; whatever this
# returns instead is the size of the pre-existing drift.

cat("\n== placebo ==\n")
# The windows are the main comparison's own, shifted back k years: pre becomes
# -2k..-k-1 and post becomes -k..-1. Both must be k years wide, or the test is
# not the same test.
#
# This was written (-k+1):-1, which is neither. At k=2 it gave a two-year pre
# window against a one-year post window; at k=1 it gave 0:-1, and offset 0 is
# the retraction year - the one year the whole analysis excludes, because
# OpenAlex counts by year and a citation in it cannot be placed on either side.
# tests/r/test_did.R asserted the placebo windows never touch the event year,
# but it asserted it about a correct pair written out by hand in the test,
# not about the expression that actually ships.
placebo <- do.call(rbind, lapply(1:2, function(k) {
  pre <- (-2 * k):(-k - 1)
  post <- (-k):-1
  stopifnot(length(pre) == k, length(post) == k, max(c(pre, post)) < 0)
  r <- summarise_cells(did_cells(panel, pre, post),
                       sprintf("placebo, k = %d (truth is 1.000)", k), B = B_MAIN)
  r$k <- k
  r
}))
placebo$real_effect <- vapply(placebo$k, function(k)
  mh_ratio(cells_by_k[[as.character(k)]]), numeric(1))
# Not a correction, a bound. The placebo does not come out at 1, so retracted
# papers were already sliding against their controls before anything was
# retracted. Dividing by that drift says what is left if the whole of it would
# have continued - which is why the report calls the result a fall relative to
# matched controls, not an effect the retraction caused.
placebo$effect_net_of_drift <- placebo$real_effect / placebo$ratio_mh
print(placebo[c("group", "papers", "ratio_mh", "ci_lower", "ci_upper",
                "real_effect", "effect_net_of_drift")], row.names = FALSE, digits = 4)
write_table(placebo, "placebo")

# ---------------------------------------------------------- robustness -----

cat("\n== robustness ==\n")
cells_p <- cells_by_k[[as.character(K_PRIMARY)]]
rob <- list(summarise_cells(
  cells_p, sprintf("primary (k=%d, exact publication year)", K_PRIMARY), B = B_MAIN))
add_rob <- function(cells, label) rob[[length(rob) + 1L]] <<- summarise_cells(cells, label)
add_rob(did_cells_k(panel, K_PRIMARY, levels = 1), "same-journal controls only (level 1)")
add_rob(did_cells_k(panel, K_PRIMARY, levels = 3),
        "same field, other journal only (level 3)")
add_rob(did_cells_k(panel, K_PRIMARY, levels = 1:3),
        "with year +/-1 controls added back (levels 1-3)")
add_rob(did_cells_k(panel, K_PRIMARY, levels = 2), "year +/-1 controls only (level 2)")
add_rob(did_cells_k(panel, K_PRIMARY, controls = unique_controls_only(panel)),
        "controls used by one paper only")
add_rob(did_cells_k(panel, K_PRIMARY, natures = NULL), "every retraction nature")
add_rob(did_cells_k(panel, K_PRIMARY, drop_notice_types = FALSE),
        "notice-typed OpenAlex records kept")
robust <- do.call(rbind, rob)
robust <- robust[c("group", "strata", "papers", "controls", "ratio_mh",
                   "ratio_pooled", "ci_lower", "ci_upper")]
print(robust, row.names = FALSE, digits = 4)
write_table(robust, "robustness")

cat("\n== why the publication year must match exactly ==\n")
cat("   level 2 relaxes it to +/-1 year. Split by the offset, the two halves\n")
cat("   point opposite ways and only cancel by accident.\n")
offsets <- year_offset_check(panel, K_PRIMARY, levels = 1:3)
print(offsets, row.names = FALSE, digits = 4)
write_table(offsets, "year-offset")

cat("\n== does it matter how many controls a paper got? ==\n")
counts <- control_count_check(panel, K_PRIMARY)
print(counts, row.names = FALSE, digits = 4)
write_table(counts, "control-count")

cat("\n== the deal of controls to papers ==\n")
deal <- redeal_check(panel, K_PRIMARY)
print(deal, row.names = FALSE, digits = 4)
write_table(deal, "redeal")

cat("\n== is the control arm itself damaged by a mass retraction? ==\n")
contam <- contamination_check(panel, K_PRIMARY)
print(contam, row.names = FALSE, digits = 4)
write_table(contam, "contamination")

# ------------------------------------------------------------ dimensions ---
# The client's order: reason, country, publisher, discipline. Each narrows the
# retracted arm only - a reason or a country belongs to a paper, not to a
# journal-year, so the control arm stays whole and every row is compared
# against the same age curve.

dims <- panel_dimensions()
by_doi <- function(long, col) dimension_values(panel, long, col, min_papers = MIN_PAPERS)

pub <- panel$papers
pub$family <- publisher_family(pub$publisher)
pub <- pub[!is.na(pub$family) & pub$family != "", ]
pub_values <- split(pub$doi, pub$family)
pub_values <- pub_values[lengths(pub_values) >= MIN_PAPERS]

# Retraction Watch's seven subject codes, spelled out. B/T is here because it
# used to vanish: the code contains a slash, the cleaner's pattern did not
# allow one, and a fifth of all subject rows came out with no discipline.
DISCIPLINE_NAMES <- c(
  BLS = "Basic Life Sciences",
  HSC = "Health Sciences",
  "B/T" = "Business and Technology",
  PHY = "Physical Sciences",
  SOC = "Social Sciences",
  ENV = "Environmental Sciences",
  HUM = "Humanities")

subj <- dims$subject
code <- ifelse(is.na(subj$subject_code) | subj$subject_code == "",
               "(no code)", subj$subject_code)
named <- unname(DISCIPLINE_NAMES[code])
subj$discipline <- ifelse(is.na(named), code, sprintf("%s (%s)", named, code))

for (d in list(
  list(name = "by-reason", label = "reason", values = by_doi(dims$reason, "reason")),
  list(name = "by-country", label = "country", values = by_doi(dims$country, "country")),
  list(name = "by-publisher", label = "publisher", values = pub_values),
  list(name = "by-discipline", label = "discipline",
       values = by_doi(subj, "discipline")))) {
  cat("\n==", d$label, "==", length(d$values), "values with at least",
      MIN_PAPERS, "papers\n")
  tab <- did_by_dimension(cells_p, d$values, min_papers = MIN_PAPERS, B = B_DIM)
  if (is.null(tab)) { cat("  nothing to report\n"); next }
  names(tab)[1] <- d$label
  print(utils::head(tab[c(d$label, "papers", "ratio_mh", "ci_lower", "ci_upper")], 12),
        row.names = FALSE, digits = 4)
  write_table(tab, d$name)
}

# --------------------------------------------------------------- figures ---

png_out <- function(name, width = 1100, height = 750) {
  f <- file.path(OUT, paste0("citation-impact-", name, ".png"))
  grDevices::png(f, width = width, height = height, res = 130)
  f
}

f <- png_out("event-study")
op <- graphics::par(mar = c(6, 4.5, 3, 1))
graphics::plot(es$tau, es$ratio_mh, type = "n", ylim = range(c(0, es$ci_lower,
               es$ci_upper, es$ratio_mh), na.rm = TRUE),
               xlab = "", ylab = "citations relative to matched controls",
               main = "Citations before and after retraction")
graphics::abline(h = 1, col = "grey60", lty = 2)
graphics::abline(v = 0, col = "grey60", lty = 3)
graphics::arrows(es$tau, es$ci_lower, es$tau, es$ci_upper, angle = 90, code = 3,
                 length = 0.04, col = "grey30")
# Drawn as two segments with a gap at the retraction year. Joining them would
# put a line across a year that was deliberately never measured - OpenAlex
# counts by year, so a citation in the retraction year cannot be placed on
# either side of it - and a reader would take the crossing point for data.
for (side in list(es[es$tau < 0, ], es[es$tau > 0, ])) {
  graphics::lines(side$tau, side$ratio_mh, col = "#22536f", lwd = 2)
}
graphics::points(es$tau, es$ratio_mh, pch = 19, col = "#22536f")
graphics::mtext("years from the retraction", side = 1, line = 2.5)
graphics::mtext(c("1.0 = the same change the matched control papers saw.",
                  "The retraction year itself is not measured, so the line breaks there."),
                side = 1, line = c(4.1, 5.0), cex = 0.72, col = "grey35")
graphics::par(op)
grDevices::dev.off()
cat("written:", f, "\n")

reason_tab <- utils::read.csv(file.path(OUT, "citation-impact-by-reason.csv"),
                              stringsAsFactors = FALSE)
reason_tab <- utils::head(reason_tab[order(-reason_tab$papers), ], 15)
reason_tab <- reason_tab[order(reason_tab$ratio_mh), ]
f <- png_out("by-reason", height = 800)
op <- graphics::par(mar = c(6.5, 23, 3, 1))
y <- seq_len(nrow(reason_tab))
# The scale runs to 1 even though nothing reaches it. Zoomed to the data, the
# spread between reasons looks dramatic; against the no-effect line it is
# visible as what it is - every reason loses about half its citations, and the
# differences between them are second-order.
graphics::plot(reason_tab$ratio_mh, y, xlim = c(0, 1.05), yaxt = "n", pch = 19,
               col = "#22536f", xlab = "", ylab = "",
               main = "By stated reason for retraction")
short <- ifelse(nchar(reason_tab$reason) > 52,
                paste0(substr(reason_tab$reason, 1, 51), "…"), reason_tab$reason)
graphics::axis(2, at = y, labels = short, las = 1, cex.axis = 0.7)
graphics::abline(v = 1, col = "grey60", lty = 2)
graphics::segments(reason_tab$ci_lower, y, reason_tab$ci_upper, y, col = "grey30")
graphics::points(reason_tab$ratio_mh, y, pch = 19, col = "#22536f")
graphics::mtext("citations relative to matched controls", side = 1, line = 2.5)
# Two short lines rather than one long one: the plot region is narrow here
# because the labels are wide, and centred text longer than it overflows the
# image edge.
graphics::mtext(c("1.0 = no detectable effect.",
                  "The 15 most common reasons; a paper can carry several."),
                side = 1, line = c(4.1, 5.1), cex = 0.72, col = "grey35")
graphics::par(op)
grDevices::dev.off()
cat("written:", f, "\n")

cat("\ndone\n")
