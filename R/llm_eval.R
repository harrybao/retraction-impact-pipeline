# Judging the local model against the labellers.
#
# There is no answer key to score the model against. There are seven people who
# each applied the protocol, and they agree with one another only so far - alpha
# 0.594 on the five media categories. Scoring the model against their majority
# and calling the result "accuracy" would hold it to a standard the labellers
# themselves do not meet, and would say nothing about whether that is good.
#
# So the model is held to the standard a labeller is held to. Each labeller in
# turn is set aside and the other six vote; how often the set-aside person
# agrees with that vote is what one careful human achieves against a panel. The
# model is scored against exactly the same six-person panels. If it agrees with
# them about as often as a person does, it labels about as well as a person does,
# which is the most a classification checked by seven people can claim.
#
# Needs R/labelling.R (majority_vote, krippendorff_alpha).

# Cohen's kappa between two label vectors: agreement corrected for what the two
# label distributions would produce by chance.
cohen_kappa <- function(x, y, levels = NULL) {
  keep <- !is.na(x) & !is.na(y)
  if (!any(keep)) return(NA_real_)
  if (is.null(levels)) levels <- sort(unique(c(x[keep], y[keep])))
  t <- table(factor(x[keep], levels), factor(y[keep], levels))
  n <- sum(t)
  po <- sum(diag(t)) / n
  pe <- sum(rowSums(t) * colSums(t)) / n^2
  if (isTRUE(all.equal(pe, 1))) NA_real_ else (po - pe) / (1 - pe)
}

# Agreement of `x` (one label per row of `m`) with the majority of `m`'s columns,
# over items that have a majority. `within` counts a disagreement of up to that
# many steps as agreement, for the ordinal clarity scale; it needs `levels` in
# order.
agree_with_majority <- function(m, x, levels = NULL, within = 0L) {
  stopifnot(length(x) == nrow(m))
  v <- majority_vote(m)
  ok <- !v$tied & !is.na(v$majority) & !is.na(x)
  if (!any(ok)) return(c(agreement = NA_real_, n = 0))
  hit <- if (within > 0L) {
    stopifnot(!is.null(levels))
    abs(match(x[ok], levels) - match(v$majority[ok], levels)) <= within
  } else {
    x[ok] == v$majority[ok]
  }
  c(agreement = mean(hit), n = sum(ok))
}

# The human ceiling, and the model against it. One row per labeller set aside.
leave_one_out <- function(m, x, levels = NULL, within = 0L) {
  stopifnot(length(x) == nrow(m), ncol(m) >= 3L)
  rows <- lapply(colnames(m), function(j) {
    rest <- m[, colnames(m) != j, drop = FALSE]
    h <- agree_with_majority(rest, m[, j], levels, within)
    a <- agree_with_majority(rest, x, levels, within)
    data.frame(held_out = j, human = h[["agreement"]], n_human = h[["n"]],
               model = a[["agreement"]], n_model = a[["n"]],
               stringsAsFactors = FALSE)
  })
  do.call(rbind, rows)
}

# The whole judgement for one labelling task, as a long table of numbers.
# `m` is items x labellers, `x` the model's label for each item (NA where it
# failed), `levels` the label set (in order, if ordinal).
judge_task <- function(m, x, levels, metric = c("nominal", "ordinal")) {
  metric <- match.arg(metric)
  stopifnot(length(x) == nrow(m))
  maj <- majority_vote(m)
  am <- agree_with_majority(m, x)
  loo <- leave_one_out(m, x)
  alpha_h <- krippendorff_alpha(m, metric, levels)
  alpha_hm <- krippendorff_alpha(cbind(m, .model = x), metric, levels)
  out <- data.frame(
    measure = c("items", "model answered", "items with a majority",
                "model = majority of all labellers",
                "Cohen kappa, model vs majority",
                "one labeller = majority of the other six (mean)",
                "model = majority of the same six (mean)",
                "Krippendorff alpha, labellers",
                "Krippendorff alpha, labellers + model"),
    value = c(nrow(m), sum(!is.na(x)), sum(!maj$tied & !is.na(maj$majority)),
              am[["agreement"]], cohen_kappa(x, maj$majority, levels),
              mean(loo$human), mean(loo$model), alpha_h, alpha_hm),
    stringsAsFactors = FALSE)
  if (metric == "ordinal") {
    w1 <- agree_with_majority(m, x, levels, within = 1L)
    loo1 <- leave_one_out(m, x, levels, within = 1L)
    out <- rbind(out, data.frame(
      measure = c("model within one step of majority",
                  "one labeller within one step of the other six (mean)",
                  "model within one step of the same six (mean)"),
      value = c(w1[["agreement"]], mean(loo1$human), mean(loo1$model)),
      stringsAsFactors = FALSE))
  }
  out
}

# Model label x majority label, over items with a majority.
confusion <- function(m, x, levels) {
  maj <- majority_vote(m)
  ok <- !maj$tied & !is.na(maj$majority) & !is.na(x)
  t <- table(model = factor(x[ok], levels), majority = factor(maj$majority[ok], levels))
  as.data.frame(t, stringsAsFactors = FALSE)
}
