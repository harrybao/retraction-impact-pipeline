# Scoring the seven-labeller sheets: agreement, majority vote, and the ties
# the coordinator has to settle.
#
# Seven people label every item independently, the majority wins, and the
# coordinator breaks ties (docs/labelling-protocol.md). Two things have to come
# out of that: the agreed label for each item, and a defensible number for how
# much the labellers actually agreed - the client asked for a labelling
# protocol precisely so the classification could be validated, and "we all
# labelled it" is not validation.
#
# Krippendorff's alpha is computed here rather than taken from a package: it
# tolerates missing labels (someone skips an item), it handles the ordinal
# clarity scale properly, and the client can run this with no installs.

LABEL_A <- c("A1", "A2", "A3", "A4", "A0")
LABEL_A_PRECEDENCE <- c("A2", "A3", "A1", "A4", "A0")  # protocol section A3
LABEL_R <- c("R1_fabrication", "R2_image", "R3_plagiarism", "R4_duplicate",
             "R5_peer_review", "R6_paper_mill", "R7_honest_error",
             "R8_authorship_ethics", "R9_other", "R0_no_reason")
LABEL_C <- c("C0", "C1", "C2", "C3")  # ordinal, C0 lowest

# ---------------------------------------------------------------- reading ---

# Labeller name from CS43_pilot_Labeller_B.xlsx, CS43_labelling_Labeller_B.xlsx
# or CS43_social_Labeller_B.xlsx. The prefix must be listed here or the whole
# filename becomes the name, two sheets from one person look like two people,
# and the agreement figure is computed over labellers who do not exist.
labeller_of <- function(path) {
  b <- basename(path)
  b <- sub("\\.xlsx$", "", b)
  # Anything before the CS43_ marker is discarded, not just a bare prefix:
  # sheets come back renamed. One arrived as "（已标注）CS43_labelling_Labeller_D"
  # and an anchored pattern would have made the whole filename the person's name.
  b <- sub("^.*CS43_(pilot|labelling|social|round2)_", "", b)
  # Browsers add " (1)" to a file downloaded twice, and the sheets come back
  # through chat and email. Left in, one person returning their sheet twice
  # becomes two labellers who never disagree with each other, which raises
  # every agreement figure.
  b <- sub("[ _]?\\(\\d+\\)$", "", b)
  trimws(gsub("_", " ", b))
}

# Every workbook returned in `dir`, as one long table per task. Sheets that are
# missing or empty are reported, not silently skipped: a labeller who returned
# a blank sheet must not look like a labeller who agreed with everyone.
read_returned_labels <- function(dir) {
  files <- sort(list.files(dir, pattern = "\\.xlsx$", full.names = TRUE))
  files <- files[!grepl("^~\\$", basename(files))]  # Excel lock files
  if (!length(files)) stop("no .xlsx files in ", dir)
  a <- list(); b <- list(); notes <- list()
  for (f in files) {
    who <- labeller_of(f)
    sheets <- openxlsx::getSheetNames(f)
    # EVERY task A sheet, not only "A_media". Round 2 and the social supplement
    # go out as one workbook per person - one file to fill and one to return,
    # rather than two, because two files is two chances to send back one. So a
    # workbook may hold "A_media" and "A_social", and a reader that knew only
    # the one name would silently score half of what came back. The sheet a row
    # came from is kept, because the two are different populations and are
    # scored apart.
    take <- function(prefix, task) {
      got <- list()
      for (nm in sheets[startsWith(sheets, prefix)]) {
        d <- openxlsx::read.xlsx(f, sheet = nm)
        if (is.null(d) || !nrow(d)) next
        # The item id is the first column of every sheet issued. One round-2
        # sheet came back with that header overtyped (a backtick), and reading
        # by name would have dropped all 100 of its rows as unidentified.
        if (!"item_id" %in% names(d)) {
          notes[[length(notes) + 1L]] <<- data.frame(
            labeller = who, task = task,
            issue = sprintf("sheet %s: first column header was '%s', read as item_id",
                            nm, names(d)[1]))
          names(d)[1] <- "item_id"
        }
        d$labeller <- who
        d$sheet <- nm
        got[[length(got) + 1L]] <- d
      }
      if (!length(got)) {
        notes[[length(notes) + 1L]] <<- data.frame(labeller = who, task = task,
                                                   issue = "sheet missing or empty")
      }
      got
    }
    a <- c(a, take("A_", "A"))
    b <- c(b, take("B_", "B"))
  }
  # Sheets may not carry identical columns - the social supplement adds a
  # `source` column, and a labeller may have deleted one they were told not to
  # touch. Bind on the columns they share, and say which were dropped rather
  # than failing on an rbind nobody can read.
  # Sheets need not carry identical columns - the social supplement adds
  # `source`, and a labeller may delete one they were told not to touch. Bind
  # on the UNION and fill what a sheet does not have with NA, rather than on
  # the intersection: dropping meant that putting the social items in the same
  # workbook as the round-2 items silently lost `source`, which is the column
  # the social supplement exists to break results down by. Columns that are not
  # everywhere are still named, because one appearing is usually a sheet that
  # was edited.
  bind <- function(x) {
    if (!length(x)) return(NULL)
    x <- lapply(x, function(d) d[, unique(names(d)), drop = FALSE])
    all_cols <- unique(unlist(lapply(x, names)))
    common <- Reduce(intersect, lapply(x, names))
    uneven <- setdiff(all_cols, common)
    if (length(uneven)) {
      message("columns not present in every sheet, kept as NA where absent: ",
              paste(uneven, collapse = ", "))
    }
    do.call(rbind, lapply(x, function(d) {
      for (nm in setdiff(all_cols, names(d))) d[[nm]] <- NA
      d[, all_cols, drop = FALSE]
    }))
  }
  list(A = bind(a), B = bind(b), files = files,
       problems = if (length(notes)) do.call(rbind, notes) else NULL)
}

# --------------------------------------------------------------- agreement --

# units x raters, from a long table of one label per item per labeller.
rating_matrix <- function(item, rater, value) {
  items <- sort(unique(item))
  raters <- sort(unique(rater))
  m <- matrix(NA_character_, nrow = length(items), ncol = length(raters),
              dimnames = list(items, raters))
  m[cbind(match(item, items), match(rater, raters))] <- as.character(value)
  m
}

# Krippendorff's alpha. `metric` is "nominal" (unordered categories) or
# "ordinal" (ranked, so disagreeing by one step counts less than by three).
# `levels` fixes the order for the ordinal case.
krippendorff_alpha <- function(m, metric = c("nominal", "ordinal"), levels = NULL) {
  metric <- match.arg(metric)
  vals <- if (is.null(levels)) sort(unique(as.vector(m[!is.na(m)]))) else levels
  if (length(vals) < 2L) return(NA_real_)  # everyone said the same thing
  # Coincidence matrix: every pairable pair of values within a unit, weighted
  # so each unit contributes equally regardless of how many labelled it.
  o <- matrix(0, length(vals), length(vals), dimnames = list(vals, vals))
  for (i in seq_len(nrow(m))) {
    v <- m[i, ][!is.na(m[i, ])]
    mu <- length(v)
    if (mu < 2L) next
    cnt <- tabulate(match(v, vals), nbins = length(vals))
    o <- o + (outer(cnt, cnt) - diag(cnt, nrow = length(vals))) / (mu - 1)
  }
  n_c <- rowSums(o)
  n <- sum(n_c)
  if (n < 2) return(NA_real_)
  # Squared difference between two categories.
  d2 <- matrix(0, length(vals), length(vals))
  if (metric == "nominal") {
    d2[] <- 1
    diag(d2) <- 0
  } else {
    # Krippendorff's ordinal metric: the distance between two ranks is the mass
    # of the categories lying between them, half-counting the endpoints.
    for (a in seq_along(vals)) for (b in seq_along(vals)) {
      if (a == b) next
      g <- seq(min(a, b), max(a, b))
      d2[a, b] <- (sum(n_c[g]) - (n_c[a] + n_c[b]) / 2)^2
    }
  }
  num <- sum(o * d2)
  den <- sum(outer(n_c, n_c) * d2) / (n - 1)
  if (den == 0) return(NA_real_)
  1 - num / den
}

# The share of labeller pairs that agree, averaged over items. Blunt, but it is
# the number people actually understand.
pairwise_agreement <- function(m) {
  agree <- 0; pairs <- 0
  for (i in seq_len(nrow(m))) {
    v <- m[i, ][!is.na(m[i, ])]
    k <- length(v)
    if (k < 2L) next
    cnt <- table(v)
    agree <- agree + sum(cnt * (cnt - 1)) / 2
    pairs <- pairs + k * (k - 1) / 2
  }
  if (pairs == 0) NA_real_ else agree / pairs
}

# Fleiss' kappa, over the items every labeller rated. Reported alongside alpha
# because it is what most readers of this literature expect to see.
fleiss_kappa <- function(m, levels = NULL) {
  keep <- rowSums(!is.na(m)) == ncol(m)
  m <- m[keep, , drop = FALSE]
  if (nrow(m) < 2L) return(NA_real_)
  vals <- if (is.null(levels)) sort(unique(as.vector(m))) else levels
  k <- ncol(m)
  cnt <- t(apply(m, 1, function(r) tabulate(match(r, vals), nbins = length(vals))))
  p_i <- (rowSums(cnt^2) - k) / (k * (k - 1))
  p_j <- colSums(cnt) / (nrow(m) * k)
  pbar <- mean(p_i); pe <- sum(p_j^2)
  if (isTRUE(all.equal(pe, 1))) return(NA_real_)
  (pbar - pe) / (1 - pe)
}

# ------------------------------------------------------------ majority vote --

# One agreed label per item. `order` breaks nothing by itself - a tie is
# reported as a tie - but it records which label the protocol's precedence
# would suggest, so the coordinator has a starting point rather than a coin.
majority_vote <- function(m, order = NULL) {
  out <- data.frame(item_id = rownames(m), n_labels = NA_integer_,
                    majority = NA_character_, votes = NA_integer_,
                    agreement = NA_real_, tied = FALSE,
                    suggested = NA_character_, all_labels = NA_character_,
                    stringsAsFactors = FALSE)
  for (i in seq_len(nrow(m))) {
    v <- m[i, ][!is.na(m[i, ])]
    out$n_labels[i] <- length(v)
    if (!length(v)) next
    cnt <- sort(table(v), decreasing = TRUE)
    top <- names(cnt)[cnt == cnt[1]]
    out$votes[i] <- as.integer(cnt[1])
    out$agreement[i] <- cnt[[1]] / length(v)
    out$all_labels[i] <- paste(sprintf("%s=%d", names(cnt), as.integer(cnt)), collapse = " ")
    out$tied[i] <- length(top) > 1L
    out$majority[i] <- if (length(top) == 1L) top else NA_character_
    out$suggested[i] <- if (length(top) == 1L) top
      else if (!is.null(order)) top[order(match(top, order))][1] else top[1]
  }
  out
}

# ----------------------------------------------------------------- scoring --

# The code out of a cell, whatever shape it arrived in.
#
# The workbook offers a dropdown whose options read "A2 Acknowledges
# retraction", so that whole string is what Excel writes - and that is what came
# back from all six labellers. An earlier version compared the cell against
# "A2" and matched nothing: every Task A label and every clarity rating scored
# invalid, alpha came out NA, and the sheets looked unusable when the labelling
# was fine and the reader was wrong. Both shapes are accepted now.
label_code <- function(x, valid) {
  x <- toupper(trimws(as.character(x)))
  code <- sub("^([A-Z][0-9]).*$", "\\1", x)
  ifelse(code %in% toupper(valid), code, NA_character_)
}

# Task A: one category per item.
score_task_a <- function(a) {
  raw <- trimws(as.character(a$label))
  a$label <- label_code(raw, LABEL_A)
  bad <- is.na(a$label) & !is.na(raw) & nzchar(raw)
  m <- rating_matrix(a$item_id, a$labeller, a$label)
  list(matrix = m,
       invalid = a[bad, c("item_id", "labeller", "label")],
       stats = data.frame(
         task = "A media", measure = c("Krippendorff alpha (nominal)",
                                       "Fleiss kappa", "pairwise agreement"),
         value = c(krippendorff_alpha(m, "nominal", LABEL_A),
                   fleiss_kappa(m, LABEL_A), pairwise_agreement(m)),
         stringsAsFactors = FALSE),
       vote = majority_vote(m, LABEL_A_PRECEDENCE))
}

# Task B as rating matrices: one units x labellers matrix per reason ("Y"/"N")
# and one for clarity. Separate from the scoring so that anything else judged
# against the labellers - the local model, in R/llm_eval.R - reads their answers
# through exactly the same rules.
task_b_matrices <- function(b) {
  yes <- function(x) {
    x <- toupper(trimws(as.character(x)))
    ifelse(x %in% c("Y", "YES", "1", "TRUE"), "Y",
           ifelse(x %in% c("", "N", "NO", "0", "FALSE") | is.na(x), "N", NA_character_))
  }
  # A blank reason cell means "this reason does not apply" - the sheet asks for
  # a Y against the reasons that do and nothing against the rest, so blank is a
  # real N and must be counted as one.
  #
  # Except on a row nobody touched. A labeller who skipped an item leaves every
  # reason blank and the clarity blank too, and reading that as ten considered
  # Ns would score a non-answer as perfect agreement with whoever said no to
  # everything - inflating the reliability figure with rows that were never
  # labelled. Those rows are missing, and Krippendorff's alpha is chosen partly
  # because it handles missing properly.
  present <- Reduce(`|`, lapply(intersect(LABEL_R, names(b)),
                                function(r) yes(b[[r]]) %in% "Y"))
  clarity_given <- !is.na(label_code(b$clarity, LABEL_C))
  answered <- present | clarity_given
  if (any(!answered)) {
    message(sum(!answered), " task B rows have no reason and no clarity; ",
            "counted as unlabelled, not as ten Ns")
  }
  reasons <- list()
  for (r in intersect(LABEL_R, names(b))) {
    v <- yes(b[[r]])
    v[!answered] <- NA_character_
    reasons[[r]] <- rating_matrix(b$item_id, b$labeller, v)
  }
  list(reasons = reasons,
       clarity = rating_matrix(b$item_id, b$labeller, label_code(b$clarity, LABEL_C)))
}

# Task B: reasons are ten independent yes/no judgements, clarity is one ordinal
# scale. Agreement is reported per reason, because "did they agree" means
# something different for fabrication than for the catch-all R9.
score_task_b <- function(b) {
  mats <- task_b_matrices(b)
  stats <- list(); votes <- list()
  for (r in names(mats$reasons)) {
    m <- mats$reasons[[r]]
    stats[[r]] <- data.frame(task = paste("B reason", r),
                             measure = "Krippendorff alpha (nominal)",
                             value = krippendorff_alpha(m, "nominal", c("N", "Y")),
                             stringsAsFactors = FALSE)
    v <- majority_vote(m)
    v$reason <- r
    votes[[r]] <- v
  }
  mc <- mats$clarity
  stats[["clarity"]] <- data.frame(
    task = "B clarity", measure = c("Krippendorff alpha (ordinal)",
                                    "Fleiss kappa", "pairwise agreement"),
    value = c(krippendorff_alpha(mc, "ordinal", LABEL_C),
              fleiss_kappa(mc, LABEL_C), pairwise_agreement(mc)),
    stringsAsFactors = FALSE)
  out <- list(stats = do.call(rbind, stats),
              reason_votes = do.call(rbind, votes),
              clarity_vote = majority_vote(mc, rev(LABEL_C)))

  # Publisher boilerplate repeats. The 210 drawn items are 160 distinct notice
  # texts: 61 rows sit in a duplicate-text group and one Hindawi template
  # appears 22 times. Seven people agreeing on that template is one piece of
  # evidence, not 22, so an agreement figure over all 210 rows is inflated by
  # however many times the sampler happened to draw the same words.
  #
  # Two things come out of that, and both are reported:
  #   * agreement over DISTINCT texts, which is the honest reliability figure;
  #   * whether a labeller gave the same answer to the same text twice, which
  #     the repeats provide for free - an unplanned test-retest.
  if ("notice_text" %in% names(b)) {
    key <- trimws(as.character(b$notice_text))
    # Counted per ITEM, not per row. The table has one row per item per
    # labeller, so counting rows multiplies every figure by however many people
    # labelled - seven, here - and reports a repeat group of 154 where there
    # were 22 items.
    item_text <- tapply(key, b$item_id, function(x) x[1])
    per_text <- table(item_text)
    first_item <- tapply(names(item_text), item_text, function(x) sort(x)[1])
    n_items <- length(item_text)
    n_texts <- length(per_text)
    out$duplication <- data.frame(
      metric = c("items drawn", "distinct notice texts",
                 "items sharing their text with another",
                 "largest repeat group"),
      value = c(n_items, n_texts,
                sum(item_text %in% names(per_text)[per_text > 1]),
                max(per_text)),
      stringsAsFactors = FALSE)
    distinct <- b$item_id %in% unname(first_item)

    if (n_texts < n_items) {
      bd <- b[distinct, ]
      cl_d <- label_code(bd$clarity, LABEL_C)
      md <- rating_matrix(bd$item_id, bd$labeller, cl_d)
      out$stats <- rbind(out$stats, data.frame(
        task = "B clarity, distinct texts only",
        measure = c("Krippendorff alpha (ordinal)", "pairwise agreement"),
        value = c(krippendorff_alpha(md, "ordinal", LABEL_C),
                  pairwise_agreement(md)), stringsAsFactors = FALSE))

      # Same labeller, same words, twice: did they say the same thing?
      dup_keys <- names(per_text)[per_text > 1]
      same <- diff <- 0L
      for (kk in dup_keys) {
        rows <- b[key == kk, ]
        for (who in unique(rows$labeller)) {
          v <- label_code(rows$clarity[rows$labeller == who], LABEL_C)
          v <- v[!is.na(v)]
          if (length(v) < 2L) next
          if (length(unique(v)) == 1L) same <- same + 1L else diff <- diff + 1L
        }
      }
      if (same + diff > 0) {
        out$duplication <- rbind(out$duplication, data.frame(
          metric = c("labeller saw the same text twice and agreed with themselves",
                     "... and did not"),
          value = c(same, diff), stringsAsFactors = FALSE))
      }
    }
  }
  out
}

# Everything, written out. `dir` holds the returned workbooks.
score_labelling <- function(dir, out = file.path("data", "labelling", "scored"),
                            tag = basename(dir)) {
  got <- read_returned_labels(dir)
  dir.create(out, showWarnings = FALSE, recursive = TRUE)
  w <- function(d, name) {
    if (is.null(d) || !nrow(d)) return(invisible(NULL))
    f <- file.path(out, sprintf("%s_%s.csv", tag, name))
    utils::write.csv(d, f, row.names = FALSE)
    cat("written:", f, "\n")
  }
  res <- list(labellers = length(got$files))
  cat("labellers returned:", length(got$files), "\n")
  if (!is.null(got$problems)) { print(got$problems); w(got$problems, "problems") }
  if (!is.null(got$A)) {
    ra <- score_task_a(got$A); res$A <- ra
    cat("\n-- task A --\n"); print(ra$stats, row.names = FALSE)
    cat("ties needing the coordinator:", sum(ra$vote$tied), "of", nrow(ra$vote), "\n")
    w(ra$stats, "A_agreement"); w(ra$vote, "A_majority")
    w(ra$vote[ra$vote$tied, ], "A_ties"); w(ra$invalid, "A_invalid")
  }
  if (!is.null(got$B)) {
    rb <- score_task_b(got$B); res$B <- rb
    cat("\n-- task B --\n"); print(rb$stats, row.names = FALSE)
    cat("clarity ties:", sum(rb$clarity_vote$tied), "of", nrow(rb$clarity_vote), "\n")
    if (!is.null(rb$duplication)) {
      cat("\nrepeated notice text:\n")
      print(rb$duplication, row.names = FALSE)
      w(rb$duplication, "B_duplication")
    }
    w(rb$stats, "B_agreement"); w(rb$reason_votes, "B_reason_majority")
    w(rb$clarity_vote, "B_clarity_majority")
    w(rb$reason_votes[rb$reason_votes$tied, ], "B_reason_ties")
  }
  invisible(res)
}
