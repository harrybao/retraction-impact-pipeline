# The control group: non-retracted papers matched to each retracted paper.
#
# The client preferred a control group of non-retracted papers for the
# before/after comparison. Each retracted paper gets
# up to RATIO controls, drawn at random (fixed seed) from OpenAlex:
#
#   level 1  same source (journal or proceedings), same publication year,
#            same OpenAlex type, not retracted
#   level 2  same source and type, publication year +-1
#   level 3  same OpenAlex field, same year and type (any source) - for
#            sources with too few candidates, e.g. a whole retracted volume
#
# There is no level 4. A paper that level 3 cannot fill keeps fewer controls,
# and the shortfall is recorded (short_shard_<k>.csv). Filling it across types
# would compare conference papers with journal articles, whose citation
# patterns differ (the user's decision, 2026-09-19). Most shortfalls are
# conference papers with no source in OpenAlex.
#
# A candidate is never retracted in OpenAlex and never appears anywhere in the
# Retraction Watch CSV. The control's pseudo retraction date is its paper's
# retraction date, so both are measured over the same windows.
#
# OpenAlex now charges filter calls against a daily allowance: about 1,000 a
# day without a key, 10,000 a day with a free account's key (set it as
# OPENALEX_API_KEY, e.g. in a .Renviron that is never committed). The whole run
# is about 18,000 calls, so it spans two days; every step resumes.
#
#   control_groups()        groups.csv, papers.csv, rw_dois.txt (~625 calls)
#   collect_controls()      controls_shard_1.csv (resumable, day after day)
#   merge_controls()        checks completeness -> controls_v1.csv
#
# n_shards > 1 splits the work so several people can run it, each with their
# own key; by default one person runs everything as shard 1.

CONTROL_SEED <- 20260919
CONTROL_RATIO <- 3
# OpenAlex types a notice, not a paper. 2,226 retracted papers carry one of
# these (2,181 retraction, 45 erratum), mostly where the article and its notice
# share a DOI. Matching on the
# type drew other notices as controls (68 groups, 357 controls, discarded on
# 2026-09-19), so these papers are matched as articles, with their own seed so
# that their sample is not a copy of the article group's in the same source-year.
NOTICE_TYPES <- c("retraction", "erratum")
OA <- "https://api.openalex.org"
# The contact address (mailto, User-Agent) is polite_contact() in R/python.R,
# read at each request: it was a fixed placeholder group address here, which
# nobody running the collection could change.

# --- OpenAlex requests ------------------------------------------------------

oa_spent <- new.env()
oa_spent$usd <- 0
oa_spent$calls <- 0
oa_spent$remaining <- NA_real_

# One GET against the OpenAlex API. The key, when set, is read from the
# OPENALEX_API_KEY environment variable and never printed.
oa_get <- function(path, query = list()) {
  key <- Sys.getenv("OPENALEX_API_KEY", "")
  req <- httr2::request(OA) |>
    httr2::req_url_path_append(path) |>
    httr2::req_url_query(!!!query, mailto = polite_contact()) |>
    httr2::req_user_agent(sprintf("CS43-retraction-pipeline (%s)", polite_contact())) |>
    httr2::req_throttle(rate = 5 / 1) |>
    # 5xx is retried. 429 is not: OpenAlex answers 429 when the day's
    # allowance is spent, and retrying only burns time until tomorrow.
    httr2::req_retry(max_tries = 5, is_transient = function(r) httr2::resp_status(r) %in% c(500, 502, 503, 504),
                     backoff = function(i) 2^i)
  if (nzchar(key)) req <- httr2::req_url_query(req, api_key = key)
  resp <- httr2::req_perform(req)
  body <- httr2::resp_body_json(resp, simplifyVector = FALSE)
  left <- httr2::resp_header(resp, "x-ratelimit-remaining")
  if (!is.null(left)) oa_spent$remaining <- as.numeric(left)
  oa_spent$calls <- oa_spent$calls + 1
  oa_spent$usd <- oa_spent$usd + (body$meta$cost_usd %||% 0)
  body
}
`%||%` <- function(a, b) if (is.null(a)) b else a

norm_doi <- function(x) tolower(sub("^https?://(dx\\.)?doi\\.org/", "", x %||% NA_character_))
short_id <- function(x) sub("^https://openalex.org/(fields/)?", "", x)

# --- 1. groups (coordinator) --------------------------------------------------

control_groups <- function(out = file.path(pipeline_dir(), "data", "controls"),
                           clean = file.path(pipeline_dir(), "data", "clean"),
                           n_shards = 1) {
  dir.create(out, recursive = TRUE, showWarnings = FALSE)
  eng <- utils::read.csv(file.path(clean, "engagement.csv"), colClasses = "character",
                         encoding = "UTF-8")
  rec <- utils::read.csv(file.path(clean, "records_clean.csv"), colClasses = "character",
                         encoding = "UTF-8")
  # Every DOI Retraction Watch names, original or notice: none may be a control.
  rw <- unique(tolower(c(rec$original_doi, rec$retraction_doi)))
  writeLines(sort(rw[nzchar(rw)]), file.path(out, "rw_dois.txt"))

  # Each paper's date: its earliest Retraction record, else its earliest notice.
  rec <- rec[nzchar(rec$original_doi), ]
  rec$is_ret <- rec$retraction_nature == "Retraction"
  rec <- rec[order(rec$original_doi, !rec$is_ret, rec$retraction_date), ]
  first <- rec[!duplicated(rec$original_doi), c("original_doi", "retraction_date", "retraction_nature")]

  ids <- unique(eng$openalex_id[nzchar(eng$openalex_id)])
  meta <- vector("list", ceiling(length(ids) / 100))
  for (i in seq_along(meta)) {
    batch <- ids[((i - 1) * 100 + 1):min(i * 100, length(ids))]
    res <- oa_get("works", list(filter = paste0("openalex_id:", paste(batch, collapse = "|")),
                               `per-page` = 100,
                               select = "id,doi,publication_year,type,primary_location,primary_topic"))
    meta[[i]] <- do.call(rbind, lapply(res$results, function(w) data.frame(
      openalex_id = short_id(w$id), oa_doi = norm_doi(w$doi),
      year = w$publication_year %||% NA_integer_, type = w$type %||% NA_character_,
      source_id = short_id(w$primary_location$source$id %||% NA_character_),
      source_name = w$primary_location$source$display_name %||% NA_character_,
      field_id = short_id(w$primary_topic$field$id %||% NA_character_))))
    if (i %% 50 == 0) message(sprintf("  looked up %d of %d batches", i, length(meta)))
  }
  meta <- do.call(rbind, meta)
  papers <- merge(eng[c("doi", "openalex_id")], meta, by = "openalex_id")
  papers <- merge(papers, first, by.x = "doi", by.y = "original_doi", all.x = TRUE)
  papers$group_id <- ifelse(is.na(papers$source_id),
                            paste("nosource", papers$field_id, papers$year, papers$type, sep = "|"),
                            paste(papers$source_id, papers$year, papers$type, sep = "|"))

  # One row per group. aggregate() would drop the groups with no source, which
  # are exactly the ones that need level 3.
  groups <- papers[!duplicated(papers$group_id), c("group_id", "source_id", "year", "type")]
  groups$n_papers <- as.integer(table(papers$group_id)[groups$group_id])
  # the field most of the group's papers are in, for level 3
  fld <- tapply(papers$field_id, papers$group_id, function(f) names(sort(table(f), decreasing = TRUE))[1] %||% NA)
  groups$field_id <- unname(fld[groups$group_id])
  groups <- groups[order(-groups$n_papers, groups$group_id), ]

  # Shards balanced by the calls a group will need, assigned greedily.
  groups$calls <- ceiling(pmin(10000, ceiling(1.5 * CONTROL_RATIO * groups$n_papers) + 5) / 200) + 1
  load <- numeric(n_shards)
  groups$shard <- NA_integer_
  for (i in seq_len(nrow(groups))) {
    k <- which.min(load)
    groups$shard[i] <- k
    load[k] <- load[k] + groups$calls[i]
  }
  utils::write.csv(papers, file.path(out, "papers.csv"), row.names = FALSE, fileEncoding = "UTF-8")
  utils::write.csv(groups, file.path(out, "groups.csv"), row.names = FALSE, fileEncoding = "UTF-8")
  message(sprintf("%d papers in %d groups; expected calls per shard: %s; OpenAlex cost $%.4f",
                  nrow(papers), nrow(groups), paste(round(load), collapse = ", "), oa_spent$usd))
  invisible(groups)
}

# --- 2. one shard (each member) ---------------------------------------------

# Draw candidates for one filter, excluding anything Retraction Watch names.
oa_sample <- function(filter, n, seed, exclude) {
  n <- min(n, 10000)
  got <- list()
  for (page in seq_len(ceiling(n / 200))) {
    res <- oa_get("works", list(filter = filter, sample = n, seed = seed, `per-page` = 200,
                               page = page,
                               select = "id,doi,publication_year,type,cited_by_count,counts_by_year,is_retracted"))
    if (!length(res$results)) break
    got <- c(got, res$results)
    if (length(res$results) < 200) break
  }
  if (!length(got)) return(NULL)
  df <- data.frame(
    control_openalex_id = short_id(vapply(got, function(w) w$id, "")),
    control_doi = vapply(got, function(w) norm_doi(w$doi), ""),
    control_year = vapply(got, function(w) as.integer(w$publication_year %||% NA), 0L),
    control_type = vapply(got, function(w) w$type %||% NA_character_, ""),
    cited_by_count = vapply(got, function(w) as.integer(w$cited_by_count %||% NA), 0L),
    counts_by_year = vapply(got, function(w) as.character(jsonlite::toJSON(w$counts_by_year, auto_unbox = TRUE)), ""),
    retracted = vapply(got, function(w) isTRUE(w$is_retracted), NA))
  df[!is.na(df$control_doi) & !df$retracted & !(df$control_doi %in% exclude), ]
}

match_group <- function(g, papers, exclude) {
  need <- CONTROL_RATIO * nrow(papers)
  if (is.na(g$year)) return(NULL)                   # nothing to match a year on
  notice <- isTRUE(g$type %in% NOTICE_TYPES)
  type <- if (notice) "article" else g$type
  seed <- CONTROL_SEED + notice
  base <- paste0(if (!is.na(type)) sprintf("type:%s,", type), "is_retracted:false")
  # One slot per level. Assigning NULL to pools[[1]] would delete the slot, and
  # a source with no candidates then crashed the next line (519 groups on the
  # first day's run: "subscript out of bounds").
  pools <- vector("list", 3)
  if (!is.na(g$source_id)) {
    pools[1] <- list(oa_sample(sprintf("primary_location.source.id:%s,publication_year:%s,%s",
                                    g$source_id, g$year, base), ceiling(1.5 * need) + 5, seed, exclude))
    if (NROW(pools[[1]]) < need) {
      pools[2] <- list(oa_sample(sprintf("primary_location.source.id:%s,publication_year:%s|%s,%s",
                                      g$source_id, as.integer(g$year) - 1L, as.integer(g$year) + 1L, base),
                              ceiling(1.5 * need) + 5, seed, exclude))
    }
  }
  have <- sum(vapply(pools, NROW, 0L))
  if (have < need && !is.na(g$field_id)) {
    pools[3] <- list(oa_sample(sprintf("primary_topic.field.id:%s,publication_year:%s,%s",
                                       g$field_id, g$year, base), ceiling(1.5 * (need - have)) + 5, seed, exclude))
  }
  cand <- do.call(rbind, lapply(seq_along(pools), function(l)
    if (NROW(pools[[l]])) cbind(pools[[l]], match_level = l)))
  if (is.null(cand)) return(NULL)
  cand <- cand[!duplicated(cand$control_doi), ]
  cand <- cand[order(cand$match_level), ]          # closest match first
  # Deal the candidates to the papers in turn: each paper gets its share of
  # level-1 controls before anyone gets a level-2 one.
  set.seed(CONTROL_SEED)
  papers <- papers[sample(nrow(papers)), ]
  take <- min(need, nrow(cand))
  cand <- cand[seq_len(take), ]
  cand$retracted_doi <- rep(papers$doi, length.out = take)
  cand$pseudo_retraction_date <- papers$retraction_date[match(cand$retracted_doi, papers$doi)]
  cand$group_id <- g$group_id
  cand[c("group_id", "retracted_doi", "control_doi", "control_openalex_id", "match_level",
         "control_year", "control_type", "pseudo_retraction_date", "cited_by_count", "counts_by_year")]
}

collect_controls <- function(shard = 1, dir = file.path(pipeline_dir(), "data", "controls"),
                             limit = NULL) {
  groups <- utils::read.csv(file.path(dir, "groups.csv"), colClasses = "character", encoding = "UTF-8")
  stopifnot(as.character(shard) %in% groups$shard)
  papers <- utils::read.csv(file.path(dir, "papers.csv"), colClasses = "character", encoding = "UTF-8")
  exclude <- readLines(file.path(dir, "rw_dois.txt"))
  groups[groups == "NA"] <- NA
  mine <- groups[groups$shard == shard, ]
  if (!is.null(limit)) mine <- utils::head(mine, limit)
  cache <- file.path(dir, sprintf("shard_%d", shard), "cache")
  dir.create(cache, recursive = TRUE, showWarnings = FALSE)
  if (!nzchar(Sys.getenv("OPENALEX_API_KEY"))) {
    message("No OPENALEX_API_KEY set: the free anonymous allowance is about 1,000 calls a day.")
  }
  t0 <- Sys.time()
  for (i in seq_len(nrow(mine))) {
    g <- mine[i, ]
    f <- file.path(cache, paste0(gsub("[^A-Za-z0-9]+", "_", g$group_id), ".rds"))
    if (file.exists(f)) next                                   # resumable
    if (!is.na(oa_spent$remaining) && oa_spent$remaining < 60) {
      message("OpenAlex allowance for today is nearly spent - stopping. Run again tomorrow; it resumes.")
      break
    }
    res <- tryCatch(match_group(g, papers[papers$group_id == g$group_id, ], exclude),
                    error = function(e) e)
    if (inherits(res, "error")) {
      # Not cached, so the next run tries the group again.
      message(sprintf("  group %s failed: %s", g$group_id, conditionMessage(res)))
      if (grepl("429", conditionMessage(res))) {
        message("HTTP 429: the day's OpenAlex allowance is spent - stopping. Run again tomorrow.")
        break
      }
      next
    }
    saveRDS(if (is.null(res)) data.frame() else res, f)
    if (i %% 100 == 0) {
      message(sprintf("  %d of %d groups, %d calls, $%.3f, %.0f min", i, nrow(mine),
                      oa_spent$calls, oa_spent$usd, as.numeric(difftime(Sys.time(), t0, units = "mins"))))
    }
  }
  cached <- file.path(cache, paste0(gsub("[^A-Za-z0-9]+", "_", mine$group_id), ".rds"))
  status <- data.frame(group_id = mine$group_id,
                       status = ifelse(file.exists(cached), "done", "not finished"))
  utils::write.csv(status, file.path(dir, sprintf("status_shard_%d.csv", shard)),
                   row.names = FALSE, fileEncoding = "UTF-8")
  if (any(status$status != "done")) {
    message(sprintf("%d groups not finished - run collect_controls(%d) again; it resumes.",
                    sum(status$status != "done"), shard))
  }
  parts <- lapply(cached[file.exists(cached)], readRDS)
  out <- do.call(rbind, parts[vapply(parts, NROW, 0L) > 0])
  # Papers of finished groups only: an unfinished group is not a shortfall.
  mine_papers <- papers[papers$group_id %in% status$group_id[status$status == "done"], ]
  n_ctrl <- table(factor(out$retracted_doi, levels = mine_papers$doi))
  short <- data.frame(retracted_doi = names(n_ctrl), controls = as.integer(n_ctrl))
  short <- short[short$controls < CONTROL_RATIO, ]
  utils::write.csv(out, file.path(dir, sprintf("controls_shard_%d.csv", shard)),
                   row.names = FALSE, fileEncoding = "UTF-8")
  utils::write.csv(short, file.path(dir, sprintf("short_shard_%d.csv", shard)),
                   row.names = FALSE, fileEncoding = "UTF-8")
  message(sprintf("shard %d: %d groups, %d controls for %d papers; %d papers with fewer than %d; %d calls, $%.3f",
                  shard, nrow(mine), NROW(out), length(unique(out$retracted_doi)), nrow(short),
                  CONTROL_RATIO, oa_spent$calls, oa_spent$usd))
  invisible(out)
}

# --- 3. merge (coordinator) ---------------------------------------------------

merge_controls <- function(dir = file.path(pipeline_dir(), "data", "controls")) {
  groups <- utils::read.csv(file.path(dir, "groups.csv"), colClasses = "character", encoding = "UTF-8")
  shards <- sort(unique(as.integer(groups$shard)))
  files <- file.path(dir, sprintf("controls_shard_%d.csv", shards))
  stat <- file.path(dir, sprintf("status_shard_%d.csv", shards))
  missing <- c(files, stat)[!file.exists(c(files, stat))]
  if (length(missing)) stop("missing shard files: ", paste(basename(missing), collapse = ", "), call. = FALSE)
  status <- do.call(rbind, lapply(stat, utils::read.csv, colClasses = "character"))
  undone <- setdiff(groups$group_id, status$group_id[status$status == "done"])
  if (length(undone)) {
    stop(length(undone), " groups not finished, e.g. ", undone[1],
         " - that shard must run collect_controls() again", call. = FALSE)
  }
  all <- do.call(rbind, lapply(files, utils::read.csv, colClasses = "character", encoding = "UTF-8"))
  dup <- duplicated(all[c("retracted_doi", "control_doi")])
  if (any(dup)) warning(sum(dup), " duplicated retracted/control pairs dropped")
  all <- all[!dup, ]
  covered <- unique(all$group_id)
  stopifnot("a shard ran groups that are not its own" = all(covered %in% groups$group_id))
  utils::write.csv(all, file.path(dir, "controls_v1.csv"), row.names = FALSE, fileEncoding = "UTF-8")
  summary <- data.frame(
    metric = c("groups", "groups with controls", "retracted papers with controls", "controls",
               "controls at level 1", "controls at level 2", "controls at level 3"),
    value = c(nrow(groups), length(covered), length(unique(all$retracted_doi)), nrow(all),
              sum(all$match_level == "1"), sum(all$match_level == "2"), sum(all$match_level == "3")))
  utils::write.csv(summary, file.path(dir, "controls_v1_summary.csv"), row.names = FALSE)
  print(summary, row.names = FALSE)
  invisible(all)
}
