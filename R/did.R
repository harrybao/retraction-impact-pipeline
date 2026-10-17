# What retraction does to a paper's citations, against matched controls.
#
# ## The quantity
#
# For each stratum - one matching group at one retraction year - four numbers:
# the retracted papers' citations in the k years before and after, and their
# controls' citations over the same calendar years. The effect is a ratio of
# ratios: how the retracted papers' post/pre change compares with the change
# their controls saw. 1 means retraction did nothing visible; 0.5 means the
# retracted papers ended with half the citations they would otherwise have had.
#
# Controls carry the age curve. A 2018 paper cited less in 2022 is partly just
# a 2018 paper - in fact the controls here GAIN citations, because a retraction
# usually lands a year or two after publication and that is when a paper is
# picking up. Same-journal, same-year, same-type controls age the same way, so
# dividing one change by the other leaves the retraction.
#
# ## Why a ratio of sums and never a sum of ratios
#
# The obvious aggregate - work out post/pre for each paper, then average - is
# wrong here, and wrong in the direction that flatters the answer. Most
# retracted papers have very few citations, so the per-paper ratio is a ratio
# of small integers, and the expectation of a ratio is not the ratio of
# expectations: a paper going from 1 citation to 5 contributes a 5, while one
# going from 5 to 1 contributes 0.2. The upward tail is unbounded, the downward
# one stops at zero, and the average drifts up. Measured on this data at k=1 it
# gives 0.774 against 0.527 for the estimator below - "citations fell 23%"
# instead of "citations fell 47%". naive_ratio() is kept so the report can show
# the size of that gap rather than assert it.
#
# So the ratio stays a ratio of sums, and aggregation across strata uses the
# Mantel-Haenszel form, which stays consistent when individual strata are thin.
#
# ## Inference
#
# The resampling unit is the stratum, not the paper. Controls are matched with
# replacement and papers within a group draw from one pool, so papers are not
# independent of each other; strata are the largest unit that is.

source("R/citations.R")
source("R/citation_panel.R")

# OpenAlex types that are the retraction notice rather than the article. Their
# citations are the notice's, not the paper's.
NOTICE_WORK_TYPES <- c("retraction", "erratum")

# ------------------------------------------------------- windows as offsets --

# Every comparison here is "these years before the event against those years
# after", so the windows are expressed as offsets from the event year and the
# same code serves the main estimate, the placebo and the event study. Offset 0
# is never used: OpenAlex counts by year, not by month, so a citation in the
# retraction year cannot be placed on either side of it.

offsets_usable <- function(event_year, pub_year, offsets,
                           first = CIT_FIRST_YEAR, last = CIT_LAST_YEAR) {
  ok <- !is.na(event_year) & !is.na(pub_year)
  ok & event_year + min(offsets) >= first &
    event_year + min(offsets) >= pub_year &
    event_year + max(offsets) <= last
}

# Citations summed over the given offsets, per row of `m`.
offset_sums <- function(m, event_year, offsets, first = CIT_FIRST_YEAR) {
  last <- first + ncol(m) - 1L
  out <- rep(NA_integer_, nrow(m))
  inside <- !is.na(event_year) & event_year + min(offsets) >= first &
    event_year + max(offsets) <= last
  cols <- lapply(sort(unique(event_year[inside])),
                 function(e) e + offsets - first + 1L)
  names(cols) <- as.character(sort(unique(event_year[inside])))
  for (i in which(inside)) out[i] <- sum(m[i, cols[[as.character(event_year[i])]]])
  out
}

# ------------------------------------------------------------------- cells --

# Match levels 1 and 3 only, by default. Both match the publication year
# exactly; level 2 relaxes it to +/-1 year, and publication year is precisely
# the variable the control arm exists to hold constant.
#
# The damage is measurable and large, and it points in both directions at once.
# Splitting the level-2 controls at k=1 by how their publication year differs
# from their paper's (year_offset_check() prints this):
#
#   offset  control rows  the control arm's own growth  the estimate it gives
#     -1           3,942                         1.082                  1.578
#      0          96,472                         1.396                  0.526
#     +1           1,434                         3.103                  0.246
#
# A control published a year EARLIER is further along its own citation curve
# and rising more slowly, so the retracted paper looks fine beside it. One
# published a year LATER is still climbing steeply, so the same paper looks
# catastrophic. Neither number is about retraction; both are about age. Pooled,
# level 2 alone gives 1.127 - which reads as "no effect at all" and is nothing
# of the sort.
#
# Excluding it costs almost nothing: 34,269 papers against 34,277, and the
# estimate moves from 0.5417 to 0.5265. Level 2 stays available and the
# analysis reports it as a sensitivity rather than hiding it.
CONTROL_LEVELS <- c(1L, 3L)

# Per stratum: the four sums, and how many works produced them.
#
# `natures` restricts which retractions count as the treatment. The default is
# Retraction alone - an Expression of concern is a different event, and mixing
# them asks what the average of two treatments does.
#
# `papers_subset` narrows the TREATED arm only, which is how the dimension
# tables are built. A reason, a country or a discipline varies between papers
# inside one stratum, so it cannot select whole strata; and the control arm
# must not be narrowed with it, because controls carry no reason or country of
# their own and subsetting them would change what the age curve is measured on.
did_cells <- function(panel, pre, post, natures = "Retraction",
                      drop_notice_types = TRUE, levels = CONTROL_LEVELS,
                      papers_subset = NULL, controls = NULL) {
  offsets <- c(pre, post)
  # Offset 0 is the event year, and the event year is never measured: OpenAlex
  # counts by year, not by month, so a citation in it cannot be placed before
  # or after the retraction. A caller that asks for it has made an arithmetic
  # slip - (-k+1):-1 is 0:-1 at k=1 - and would get a number back rather than
  # an error, which is how a wrong placebo reached a committed table once.
  if (any(offsets == 0L)) {
    stop("offset 0 is the event year and is never measured; got pre = ",
         paste(pre, collapse = ","), " post = ", paste(post, collapse = ","),
         call. = FALSE)
  }
  # There is deliberately no check that `pre` comes before `post`. The two are
  # the denominator and numerator windows, not a chronology: an event-study
  # coefficient for a year before the retraction puts that earlier year in
  # `post` and the base year -1 in `pre`, which is exactly what it means.
  p <- panel$papers
  keep <- offsets_usable(p$event_year, p$pub_year, offsets)
  if (!is.null(natures)) keep <- keep & p$retraction_nature %in% natures
  if (drop_notice_types) keep <- keep & !(tolower(p$work_type) %in% NOTICE_WORK_TYPES)
  if (!is.null(papers_subset)) keep <- keep & p$doi %in% papers_subset
  keep[is.na(keep)] <- FALSE
  p <- p[keep, ]
  if (!nrow(p)) return(empty_cells())
  mp <- panel$mp[p$row, , drop = FALSE]
  p$pre <- offset_sums(mp, p$event_year, pre)
  p$post <- offset_sums(mp, p$event_year, post)

  cl <- if (is.null(controls)) panel$controls else controls
  ck <- offsets_usable(cl$event_year, cl$pub_year, offsets) & cl$match_level %in% levels
  ck[is.na(ck)] <- FALSE
  cl <- cl[ck, ]
  if (!nrow(cl)) return(empty_cells())
  mc <- panel$mc[cl$row, , drop = FALSE]
  cl$pre <- offset_sums(mc, cl$event_year, pre)
  cl$post <- offset_sums(mc, cl$event_year, post)

  # A stratum is a matching group at one retraction year: inside it every work
  # shares a journal, a publication year, a type and the same calendar windows.
  p$stratum <- paste(p$group_id, p$event_year, sep = "|")
  cl$stratum <- paste(cl$group_id, cl$event_year, sep = "|")
  common <- intersect(unique(p$stratum), unique(cl$stratum))
  if (!length(common)) return(empty_cells())
  p <- p[p$stratum %in% common, ]
  cl <- cl[cl$stratum %in% common, ]

  agg <- function(d) {
    f <- factor(d$stratum, levels = common)
    cbind(as.integer(tapply(d$pre, f, sum)),
          as.integer(tapply(d$post, f, sum)),
          as.integer(table(f)))
  }
  ta <- agg(p); ca <- agg(cl)
  cells <- data.frame(stratum = common,
                      t_pre = ta[, 1], t_post = ta[, 2], n_treated = ta[, 3],
                      c_pre = ca[, 1], c_post = ca[, 2], n_control = ca[, 3],
                      stringsAsFactors = FALSE)
  for (j in names(cells)[-1]) cells[[j]][is.na(cells[[j]])] <- 0L
  attr(cells, "papers") <- p
  attr(cells, "controls") <- cl
  cells
}

empty_cells <- function() {
  data.frame(stratum = character(), t_pre = integer(), t_post = integer(),
             n_treated = integer(), c_pre = integer(), c_post = integer(),
             n_control = integer(), stringsAsFactors = FALSE)
}

# The main comparison: k years each side.
did_cells_k <- function(panel, k, ...) did_cells(panel, -k:-1, 1:k, ...)

# ---------------------------------------------------------------- estimates --

# Mantel-Haenszel ratio of ratios. Each stratum is weighted by its own
# information, so a stratum holding two citations cannot outvote one holding
# two hundred.
mh_ratio <- function(cells, w = NULL) {
  if (!nrow(cells)) return(NA_real_)
  n <- cells$t_pre + cells$t_post + cells$c_pre + cells$c_post
  ok <- n > 0
  if (!any(ok)) return(NA_real_)
  if (is.null(w)) w <- rep(1, nrow(cells))
  num <- sum(w[ok] * cells$t_post[ok] * cells$c_pre[ok] / n[ok])
  den <- sum(w[ok] * cells$t_pre[ok] * cells$c_post[ok] / n[ok])
  if (den == 0) return(NA_real_)
  num / den
}

# Every stratum pooled into one 2x2. Reported beside the MH figure because it
# is the number a reader can check by hand; a large gap between them means the
# strata differ a lot in level.
pooled_ratio <- function(cells) {
  tp <- sum(cells$t_pre); tq <- sum(cells$t_post)
  cp <- sum(cells$c_pre); cq <- sum(cells$c_post)
  if (!nrow(cells) || tp == 0 || cp == 0 || cq == 0) return(NA_real_)
  (tq / tp) / (cq / cp)
}

# The wrong estimator, kept to show what it costs. See the header.
naive_ratio <- function(cells) {
  d <- cells[cells$t_pre > 0 & cells$c_pre > 0 & cells$c_post > 0, ]
  if (!nrow(d)) return(NA_real_)
  r <- (d$t_post / d$t_pre) / (d$c_post / d$c_pre)
  sum(r * d$n_treated) / sum(d$n_treated)
}

# Confidence interval by resampling strata with replacement.
boot_ci <- function(cells, B = 1000, seed = 20260923, stat = mh_ratio,
                    probs = c(0.025, 0.975)) {
  if (nrow(cells) < 2L) return(c(lower = NA_real_, upper = NA_real_,
                                 se = NA_real_, draws = 0))
  set.seed(seed)
  n <- nrow(cells)
  out <- numeric(B)
  for (b in seq_len(B)) out[b] <- stat(cells[sample.int(n, n, replace = TRUE), ])
  out <- out[is.finite(out)]
  if (!length(out)) return(c(lower = NA_real_, upper = NA_real_,
                             se = NA_real_, draws = 0))
  c(lower = unname(stats::quantile(out, probs[1])),
    upper = unname(stats::quantile(out, probs[2])),
    se = stats::sd(out), draws = length(out))
}

# One row summarising a set of cells.
summarise_cells <- function(cells, label = "", B = 0) {
  row <- data.frame(
    group = label,
    strata = nrow(cells),
    papers = sum(cells$n_treated),
    controls = sum(cells$n_control),
    t_pre = sum(cells$t_pre), t_post = sum(cells$t_post),
    c_pre = sum(cells$c_pre), c_post = sum(cells$c_post),
    ratio_mh = mh_ratio(cells),
    ratio_pooled = pooled_ratio(cells),
    ci_lower = NA_real_, ci_upper = NA_real_,
    stringsAsFactors = FALSE)
  if (B > 0) {
    ci <- boot_ci(cells, B = B)
    row$ci_lower <- ci[["lower"]]
    row$ci_upper <- ci[["upper"]]
  }
  row
}

# ------------------------------------------------------------ robustness --

# Deal the controls to the papers again under a different seed.
#
# Inside a matching group the controls were dealt round-robin to the papers
# (R/control_group.R), and that deal is not cosmetic: it decides which paper's
# retraction date each control inherits, and therefore both its windows and
# whether it survives the window filter at all. Within one retraction year the
# deal genuinely does not matter, because every paper there shares a date - but
# a group spans several years, so across them it does. Re-dealing costs nothing
# and no API calls, and the spread of the estimate across deals is an honest
# extra source of uncertainty.
redeal_controls <- function(panel, seed) {
  cl <- panel$controls
  p <- panel$papers
  by_group <- split(p$doi, p$group_id)
  rows <- split(seq_len(nrow(cl)), cl$group_id)
  set.seed(seed)
  for (g in names(rows)) {
    dois <- by_group[[g]]
    if (is.null(dois) || !length(dois)) next
    r <- rows[[g]]
    cl$retracted_doi[r] <- sample(rep(dois, length.out = length(r)))
  }
  ev <- stats::setNames(p$event_year, p$doi)
  pb <- stats::setNames(p$pub_year, p$doi)
  cl$event_year <- unname(ev[cl$retracted_doi])
  # A control's own publication year is its own; only the event moves.
  cl$pub_year <- ifelse(is.na(cl$pub_year), unname(pb[cl$retracted_doi]), cl$pub_year)
  cl
}

redeal_check <- function(panel, k, seeds = c(11, 22, 33, 44, 55)) {
  base <- summarise_cells(did_cells_k(panel, k), "original deal")
  rows <- lapply(seeds, function(s) {
    cl <- redeal_controls(panel, s)
    summarise_cells(did_cells_k(panel, k, controls = cl), paste("re-deal", s))
  })
  out <- do.call(rbind, c(list(base), rows))
  out[c("group", "strata", "papers", "controls", "ratio_mh", "ratio_pooled")]
}

# Is the control arm itself damaged when a journal-year is purged?
#
# The worry is real in principle: if a journal retracts hundreds of papers at
# once, the survivors used as controls may be avoided too, the age curve sags,
# and the estimate understates the loss. It is a worry, not a finding, so it is
# measured rather than asserted - by how the control arm's own growth varies
# with how many papers the stratum had retracted, and by comparing same-journal
# controls (level 1) with out-of-journal ones (level 3), which no purge touches.
contamination_check <- function(panel, k) {
  cells <- did_cells_k(panel, k)
  # How big the purge was, not how many papers survived the window filter:
  # a journal-year that lost 400 papers is the case in question even if only
  # 30 of them can carry a comparison.
  group_size <- table(panel$papers$group_id)
  stratum_group <- sub("[|][0-9]{4}$", "", cells$stratum)
  size <- cut(as.integer(group_size[stratum_group]),
              c(0, 1, 2, 5, 10, 20, 50, Inf),
              labels = c("1", "2", "3-5", "6-10", "11-20", "21-50", "50+"))
  by_size <- do.call(rbind, lapply(levels(size), function(s) {
    d <- cells[which(size == s), ]
    if (!nrow(d)) return(NULL)
    data.frame(cut = "retracted papers in the group", value = s,
               strata = nrow(d), papers = sum(d$n_treated),
               control_growth = sum(d$c_post) / sum(d$c_pre),
               ratio_mh = mh_ratio(d), stringsAsFactors = FALSE)
  }))

  # Same-journal controls against out-of-journal ones, WITHIN the strata that
  # hold both. Comparing them across their own separate strata would compare
  # different papers, not different controls: level 3 is only reached when a
  # journal pool ran dry, so those strata are unusual to begin with.
  l1 <- did_cells_k(panel, k, levels = 1)
  l3 <- did_cells_k(panel, k, levels = 3)
  both <- intersect(l1$stratum, l3$stratum)
  a <- l1[match(both, l1$stratum), ]
  b <- l3[match(both, l3$stratum), ]
  by_level <- if (length(both)) rbind(
    data.frame(cut = sprintf("within the %d strata holding both", length(both)),
               value = "level 1 (same journal)", strata = length(both),
               papers = sum(a$n_treated),
               control_growth = sum(a$c_post) / sum(a$c_pre),
               ratio_mh = mh_ratio(a), stringsAsFactors = FALSE),
    data.frame(cut = sprintf("within the %d strata holding both", length(both)),
               value = "level 3 (same field, other journal)", strata = length(both),
               papers = sum(b$n_treated),
               control_growth = sum(b$c_post) / sum(b$c_pre),
               ratio_mh = mh_ratio(b), stringsAsFactors = FALSE)) else NULL

  by_level_all <- do.call(rbind, lapply(1:3, function(l) {
    d <- did_cells_k(panel, k, levels = l)
    if (!nrow(d)) return(NULL)
    data.frame(cut = "all strata, by control match level", value = as.character(l),
               strata = nrow(d), papers = sum(d$n_treated),
               control_growth = sum(d$c_post) / sum(d$c_pre),
               ratio_mh = mh_ratio(d), stringsAsFactors = FALSE)
  }))
  rbind(by_size, by_level, by_level_all)
}

# Drop controls that serve more than one retracted paper. Matching with
# replacement is standard and the reuse is small in the usable subset, but the
# client should see the estimate without it rather than take that on trust.
unique_controls_only <- function(panel) {
  cl <- panel$controls
  cl[!duplicated(cl$control_doi) & !cl$control_doi %in%
       cl$control_doi[duplicated(cl$control_doi)], ]
}

# --------------------------------------------------------------- dimensions --

# The client's four dimensions, in his order: reason, country, publisher,
# discipline. Each narrows the TREATED arm only; see did_cells().
# Re-aggregate the TREATED arm of an existing set of cells over a subset of
# papers, keeping the control arm exactly as it is.
#
# This is the same result as calling did_cells() again with papers_subset, and
# it exists because it is not the same cost. The control arm is identical for
# every dimension value - that is the design - so recomputing it 150 times
# over 45,000 control rows spends half an hour to arrive back where it started.
retreat_cells <- function(cells, dois) {
  p <- attr(cells, "papers")
  p <- p[p$doi %in% dois, ]
  if (!nrow(p)) return(empty_cells())
  f <- factor(p$stratum, levels = cells$stratum)
  out <- cells
  out$t_pre <- as.integer(tapply(p$pre, f, sum))
  out$t_post <- as.integer(tapply(p$post, f, sum))
  out$n_treated <- as.integer(table(f))
  for (j in c("t_pre", "t_post", "n_treated")) out[[j]][is.na(out[[j]])] <- 0L
  # Strata with no paper left contribute nothing; dropping them keeps the
  # stratum count meaning what it says.
  out <- out[out$n_treated > 0L, ]
  attr(out, "papers") <- p
  attr(out, "controls") <- attr(cells, "controls")
  out
}

# The client's four dimensions, in his order: reason, country, publisher,
# discipline. Each narrows the TREATED arm only; see did_cells().
did_by_dimension <- function(cells, values, min_papers = 30, B = 0) {
  out <- lapply(names(values), function(v) {
    d <- retreat_cells(cells, values[[v]])
    if (sum(d$n_treated) < min_papers) return(NULL)
    summarise_cells(d, v, B = B)
  })
  out <- do.call(rbind, out[!vapply(out, is.null, logical(1))])
  if (is.null(out)) return(NULL)
  out[order(-out$papers), ]
}

# DOIs per dimension value, from the Retraction Watch long tables. They key on
# record_id, the citation data keys on DOI, and papers carries both.
dimension_values <- function(panel, long, value_col, min_papers = 30) {
  rid <- stats::setNames(panel$papers$doi, panel$papers$record_id)
  long <- long[!is.na(long[[value_col]]) & long[[value_col]] != "", ]
  long$doi <- unname(rid[as.character(long$record_id)])
  long <- long[!is.na(long$doi), ]
  sp <- split(unique(long[c("doi", value_col)])$doi,
              unique(long[c("doi", value_col)])[[value_col]])
  sp[lengths(sp) >= min_papers]
}

# Why does relaxing the publication-year match break the comparison?
#
# One row per publication-year offset between a control and its retracted
# paper, with the control arm's own growth and the estimate it produces. This
# is the evidence for matching on the exact year, and it is reported rather
# than asserted.
year_offset_check <- function(panel, k, levels = 1:3) {
  cells <- did_cells_k(panel, k, levels = levels)
  cl <- attr(cells, "controls")
  pap <- attr(cells, "papers")
  cl$offset <- cl$pub_year - pap$pub_year[match(cl$retracted_doi, pap$doi)]
  out <- lapply(sort(unique(cl$offset[!is.na(cl$offset)])), function(o) {
    sub <- cl[!is.na(cl$offset) & cl$offset == o, ]
    if (!nrow(sub)) return(NULL)
    f <- factor(sub$stratum, levels = cells$stratum)
    d <- cells
    d$c_pre <- as.integer(tapply(sub$pre, f, sum))
    d$c_post <- as.integer(tapply(sub$post, f, sum))
    d$n_control <- as.integer(table(f))
    for (j in c("c_pre", "c_post", "n_control")) d[[j]][is.na(d[[j]])] <- 0L
    d <- d[d$n_control > 0, ]
    data.frame(offset = o,
               match_levels = paste(sort(unique(sub$match_level)), collapse = "+"),
               control_rows = nrow(sub), strata = nrow(d),
               control_growth = sum(d$c_post) / sum(d$c_pre),
               ratio_mh = mh_ratio(d), stringsAsFactors = FALSE)
  })
  do.call(rbind, out[!vapply(out, is.null, logical(1))])
}

# Does it matter how many controls a paper got? A paper matched to three is
# measured against three; one matched to one is measured against one, and its
# stratum is thinner. Restricting to papers with the full three answers
# whether the short ones are pulling the estimate.
control_count_check <- function(panel, k, ...) {
  cells <- did_cells_k(panel, k, ...)
  cl <- attr(cells, "controls")
  per_paper <- table(cl$retracted_doi)
  rows <- lapply(list(c(1, Inf), c(3, Inf), c(3, 3), c(1, 2)), function(rng) {
    keep <- names(per_paper)[per_paper >= rng[1] & per_paper <= rng[2]]
    d <- retreat_cells(cells, keep)
    if (!nrow(d)) return(NULL)
    label <- if (is.infinite(rng[2])) sprintf("papers with >=%d controls", rng[1])
             else if (rng[1] == rng[2]) sprintf("papers with exactly %d controls", rng[1])
             else sprintf("papers with %d-%d controls", rng[1], rng[2])
    summarise_cells(d, label)
  })
  out <- do.call(rbind, rows[!vapply(rows, is.null, logical(1))])
  out[c("group", "strata", "papers", "controls", "ratio_mh", "ratio_pooled")]
}
