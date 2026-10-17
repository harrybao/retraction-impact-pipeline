# Stage 1 and Stage 2, one R function per pipeline step.
#
# Every function takes `data_dir`: NULL works on the collected dataset in
# data/, a path works on a separate directory. Each step is resumable and safe
# to re-run (see README). The order a full rebuild follows is `pipeline_steps()`.
# A full rebuild takes days and needs the API keys and access routes described
# in data/share/blocked_publishers.csv. The handover test is run_pipeline().

# --- Stage 0: the frontier ----------------------------------------------------

# Retraction Watch CSV -> records and the queue of pages to fetch.
build_queue <- function(data_dir = NULL) {
  run_script("build_queue.py", data_dir = data_dir)
}

# --- Stage 1a: publisher pages ------------------------------------------------

# Render each queued page in a real browser and archive it as MHTML.
crawl <- function(data_dir = NULL, workers = 6, limit = NULL, prefix = NULL,
                  max_attempts = NULL, retry_only = FALSE, webkit = TRUE, shard = NULL) {
  run_script("crawl.py", c(opt("--workers", workers), opt("--limit", limit),
                           opt("--prefix", prefix), opt("--max-attempts", max_attempts),
                           flag("--retry-only", retry_only), flag("--no-webkit", !webkit),
                           opt("--shard", shard)), data_dir)
}

# Is each archive the paper it is filed under? Sets documents.usable.
verify_archives <- function(data_dir = NULL, limit = NULL, source_kind = NULL, apply = FALSE) {
  run_script("verify_archives.py", c(if (apply) "--apply" else "--dry-run",
                                   opt("--limit", limit), opt("--source-kind", source_kind)),
             data_dir)
}

# Where each unreachable document can be read legally for free (OpenAlex/Unpaywall).
find_oa_locations <- function(data_dir = NULL, limit = NULL, redo = FALSE) {
  run_script("collect_oa_locations.py", c(opt("--limit", limit), flag("--redo", redo)), data_dir)
}

# PubMed Central: DOIs -> PMCIDs, then PMCIDs -> JATS XML.
recover_pmc <- function(data_dir = NULL, limit = NULL) {
  r <- run_script("collect_pmc.py", c("--resolve", opt("--limit", limit)), data_dir)
  f <- run_script("collect_pmc.py", c("--fetch", opt("--limit", limit)), data_dir)
  # NCBI refuses in spells (HTTP 429 for minutes at a time). The script waits
  # about eight minutes, then leaves what it could not ask or fetch for the
  # next run rather than failing it, so say so here: on 6 October one refusal
  # hid a paper PMC did hold, and the step still read "ok".
  if (grepl("gave up after", paste(r$stdout, f$stdout), fixed = TRUE)) {
    warning("NCBI did not answer for some DOIs (HTTP 429); they are left for the next ",
            "run, so recover_pmc() run again later asks them", call. = FALSE)
  }
  invisible(f)
}

# The open-access copy, where the publisher's page is shut.
recover_open_access <- function(data_dir = NULL, limit = NULL, skip_pmc = TRUE) {
  run_script("collect_oa.py", c(opt("--limit", limit), flag("--skip-pmc", skip_pmc)), data_dir)
}

# The Internet Archive's snapshot, as real MHTML, then each checked against its
# DOI and title and merged into the documents the later steps read.
recover_wayback <- function(data_dir = NULL, limit = NULL) {
  r <- run_script("collect_wayback.py", opt("--limit", limit), data_dir)
  run_script("merge_wayback.py", data_dir = data_dir)
  # As for PubMed Central: a refusal must not read "ok". The pages the
  # Archive did not answer for are left unrecorded, so a later run asks again.
  if (grepl("did not answer for", r$stdout, fixed = TRUE)) {
    warning("the Internet Archive did not answer for some pages; they are left for the ",
            "next run, so recover_wayback() run again later asks them", call. = FALSE)
  }
  invisible(r)
}

# Which blocked hosts answer a plain HTTP client, one request each? Run this
# before recover_plain_http(), which asks for a host or a prefix and will not
# run without one - it refuses to sweep the whole frontier against publishers
# that have said no.
probe_plain_http <- function(data_dir = NULL, hosts = NULL, delay = NULL) {
  run_script("probe_plain_http.py", c(opt("--hosts", hosts), opt("--delay", delay)),
             data_dir)
}

# Hosts that answer a plain HTTP client but refuse a browser. One of `host` or
# `prefix` is required by the script itself.
recover_plain_http <- function(data_dir = NULL, host = NULL, prefix = NULL, limit = NULL,
                               dry_run = FALSE) {
  if (is.null(host) && is.null(prefix)) {
    stop("recover_plain_http() needs a host or a prefix: it will not sweep the ",
         "whole frontier. probe_plain_http() lists the hosts worth asking.",
         call. = FALSE)
  }
  run_script("collect_plain_http.py", c(opt("--host", host), opt("--prefix", prefix),
                                        opt("--limit", limit), flag("--dry-run", dry_run)),
             data_dir)
}

# One request per host per route over the documents no plan covers. Writes the
# probe results that recover_remainder() then works from; without it that step
# exits saying there are no probed routes.
probe_remainder <- function(data_dir = NULL, hosts = NULL, rate = NULL) {
  run_script("probe_remainder.py", c(opt("--hosts", hosts), opt("--rate", rate)), data_dir)
}

# The long tail, from the hosts that answered a probe.
recover_remainder <- function(data_dir = NULL, dry_run = FALSE) {
  run_script("collect_remainder.py", flag("--dry-run", dry_run), data_dir)
}

# Elsevier full text through the API. Needs ELSEVIER_API_KEY and an institutional
# entitlement (ELSEVIER_INSTTOKEN, or the University network).
recover_elsevier <- function(data_dir = NULL, limit = NULL) {
  run_script("collect_elsevier.py", opt("--limit", limit), data_dir)
}

# Metadata and abstracts from Crossref, Europe PMC and OpenAlex for pages no
# route could fetch.
enrich_metadata <- function(data_dir = NULL, limit = NULL, all = FALSE) {
  run_script("enrich.py", c(opt("--limit", limit), flag("--all", all)), data_dir)
}

# Give skipped documents the host they resolve to.
backfill_hosts <- function(data_dir = NULL, limit = NULL) {
  run_script("backfill_hosts.py", opt("--limit", limit), data_dir)
}

# DOIs a publisher says are wrong in Retraction Watch, recorded without
# editing the source CSV.
record_doi_corrections <- function(data_dir = NULL, list = TRUE) {
  run_script("record_doi_corrections.py", flag("--list", list), data_dir)
}

# Batches collected outside the crawler. Each needs the delivery folder it was
# built from; dry_run (the default) reports what would change.
#   tandf  a teammate's Taylor & Francis captures (library access) + their log
#   sage   a teammate's SAGE captures
#   campus captures made on the University network (COLLECT-CAMPUS kit)
#   shard  a teammate's own crawl database and archives
#   iop    IOP's licensed text-and-data-mining delivery
merge_batch <- function(kind = c("tandf", "sage", "campus", "shard", "iop"), from,
                        log = NULL, who = NULL, data_dir = NULL, dry_run = TRUE) {
  kind <- match.arg(kind)
  from <- normalizePath(from, winslash = "/", mustWork = TRUE)
  switch(kind,
    tandf  = run_script("merge_tandf.py", c("--from", from, opt("--log", log),
                                            flag("--dry-run", dry_run)), data_dir),
    sage   = run_script("merge_sage.py", c("--from", from, flag("--dry-run", dry_run)), data_dir),
    campus = run_script("merge_campus.py", c("--from", from, flag("--dry-run", dry_run)), data_dir),
    shard  = run_script("merge_shard.py", c("--from", from, opt("--who", who),
                                            flag("--dry-run", dry_run)), data_dir),
    iop    = run_script("ingest_iop.py", c("--dir", from, flag("--apply", !dry_run)), data_dir))
}

# Is an IOP delivery complete and intact?
check_iop_delivery <- function(dir, data_dir = NULL) {
  run_script("check_iop_delivery.py", c("--dir", normalizePath(dir, winslash = "/")), data_dir)
}

# --- Stage 1b: downstream engagement ------------------------------------------

# OpenAlex citations per year (free). wikipedia = TRUE adds the English Wikipedia pass.
collect_openalex <- function(data_dir = NULL, limit = NULL, wikipedia = FALSE, redo = FALSE) {
  run_script("collect_engagement.py", c(opt("--limit", limit), flag("--wikipedia", wikipedia),
                                        flag("--redo", redo)), data_dir)
}

# Altmetric attention counts, then the posts themselves. Needs ALTMETRIC_API_KEY
# (the client's key; the posts endpoint needs a commercial licence).
# exclude: Altmetric sources to leave out of the posts. "twitter" by default:
# the key's separate allowance of X post ids was spent on 2026-09-27, and while
# it is, Altmetric refuses every DOI that has X posts. NULL once it is renewed.
collect_altmetric <- function(data_dir = NULL, limit = NULL, posts = TRUE,
                              exclude = "twitter") {
  run_script("collect_altmetric.py", opt("--limit", limit), data_dir)
  if (posts) {
    run_script("collect_altmetric_detail.py",
               c(opt("--limit", limit),
                 opt("--exclude-sources", if (length(exclude)) paste(exclude, collapse = ","))),
               data_dir)
  }
}

# The same for the retraction NOTICES' own DOIs: news about a retraction links
# the notice more often than the paper. Counts first, then the posts.
collect_altmetric_notices <- function(data_dir = NULL, limit = NULL, posts = TRUE,
                                      exclude = "twitter") {
  run_script("collect_altmetric_notices.py", opt("--limit", limit), data_dir)
  if (posts) {
    run_script("collect_altmetric_notice_detail.py",
               c(opt("--limit", limit),
                 opt("--exclude-sources", if (length(exclude)) paste(exclude, collapse = ","))),
               data_dir)
  }
}

# PlumX metrics (counts only). Needs ELSEVIER_API_KEY with an institutional token.
# notices = TRUE asks about the retraction-notice DOIs instead of the papers.
collect_plumx <- function(data_dir = NULL, limit = NULL, probe = FALSE, notices = FALSE) {
  run_script("collect_plumx.py", c(opt("--limit", limit), flag("--probe", probe),
                                   flag("--notices", notices)), data_dir)
}

# The same on the retraction-notice DOIs; a step of its own so rebuild_all()
# can run it by name.
collect_plumx_notices <- function(data_dir = NULL, limit = NULL) {
  collect_plumx(data_dir, limit = limit, notices = TRUE)
}

# Wikipedia citations in every language edition.
collect_wikipedia <- function(data_dir = NULL, limit = NULL) {
  run_script("collect_wikipedia.py", opt("--limit", limit), data_dir)
}

# --- Stage 2: text ------------------------------------------------------------

# The article body and the retraction notice out of each verified archive.
extract_text <- function(data_dir = NULL, limit = NULL, redo = FALSE, upgrade = FALSE) {
  run_script("extract_text.py", c(opt("--limit", limit), flag("--redo", redo), flag("--upgrade", upgrade)), data_dir)
}

# Mark documents whose "body" is a template shared by many pages.
flag_shared_bodies <- function(data_dir = NULL, dry_run = FALSE) {
  run_script("flag_shared_bodies.py", flag("--dry-run", dry_run), data_dir)
}

# Every export in data/clean: the cleaned records, engagement, text and indexes.
export_all <- function(data_dir = NULL) {
  for (s in c("clean_data.py", "export_tables.py", "export_text.py", "export_master.py",
              "export_archive_index.py", "export_missing.py", "export_notice_news.py")) {
    run_script(s, data_dir = data_dir)
  }
}

# The bundle teammates work from, in data/share.
build_share <- function(data_dir = NULL) {
  run_script("build_share.py", data_dir = data_dir)
}

# --- The whole pipeline ---------------------------------------------------------

# The order a full rebuild follows. Merges are left out: they need the
# delivery folders (see merge_batch()).
pipeline_steps <- function() {
  data.frame(
    step = c("build_queue", "crawl", "verify_archives", "find_oa_locations", "recover_pmc",
             "recover_open_access", "recover_wayback",
             "probe_plain_http", "recover_plain_http",
             "probe_remainder", "recover_remainder", "recover_elsevier",
             "verify_archives", "enrich_metadata", "backfill_hosts",
             "record_doi_corrections", "collect_openalex", "collect_altmetric",
             "collect_altmetric_notices", "collect_plumx", "collect_plumx_notices",
             "collect_wikipedia", "extract_text", "flag_shared_bodies",
             "export_all", "build_share"),
    needs = c("the Retraction Watch CSV", "a browser (install_browser())", "", "network",
              "network", "network", "network",
              "network",
              "network, and a host or prefix from the probe - it will not sweep",
              "network",
              "network, and probe_plain_http/probe_remainder results",
              "ELSEVIER_API_KEY + institutional entitlement", "", "network", "network",
              "", "network",
              "ALTMETRIC_API_KEY", "ALTMETRIC_API_KEY",
              "ELSEVIER_API_KEY + ELSEVIER_INSTTOKEN", "ELSEVIER_API_KEY + ELSEVIER_INSTTOKEN",
              "network",
              "", "", "", ""),
    stringsAsFactors = FALSE)
}

# Steps that cannot run unattended: recover_plain_http() asks only the hosts
# a person picks from probe_plain_http()'s list, by design.
MANUAL_STEPS <- c("recover_plain_http")

# Run the steps in order, from `from` to `to`. With confirm = FALSE (the
# default) it only prints the plan: the full run takes days.
#
# A run that takes days must not die on day two because one service refused
# or a key is missing, so a failed step is recorded and the next one runs -
# except the queue, without which no later step has anything to work on. The
# manual steps are skipped and named. Returns the plan with an `outcome`
# column, and prints it at the end.
rebuild_all <- function(data_dir = NULL, from = "build_queue", to = "build_share",
                        confirm = FALSE) {
  steps <- pipeline_steps()
  i <- match(from, steps$step)
  j <- length(steps$step) + 1 - match(to, rev(steps$step))
  if (is.na(i) || is.na(j) || i > j) stop("unknown or reversed from/to step", call. = FALSE)
  plan <- steps[i:j, ]
  if (!confirm) {
    message("Plan (pass confirm = TRUE to run it):")
    print(plan, row.names = FALSE)
    return(invisible(plan))
  }
  plan$outcome <- NA_character_
  for (k in seq_len(nrow(plan))) {
    s <- plan$step[k]
    message(sprintf("\n== %s ==", s))
    if (s %in% MANUAL_STEPS) {
      plan$outcome[k] <- paste0("skipped: needs ", plan$needs[k], "; run it by hand")
      message(plan$outcome[k])
      next
    }
    params <- list(data_dir = data_dir)
    if (s == "verify_archives") params$apply <- TRUE
    if (s == "extract_text") params$upgrade <- TRUE
    err <- tryCatch({ do.call(s, params); NULL },
                    error = function(e) conditionMessage(e))
    plan$outcome[k] <- if (is.null(err)) "ok" else paste("failed:", err)
    if (!is.null(err)) {
      message(plan$outcome[k])
      if (s == "build_queue") break
    }
  }
  message("\nsteps:")
  print(plan[c("step", "outcome")], row.names = FALSE)
  invisible(plan)
}
