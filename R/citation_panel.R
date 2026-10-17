# The retracted papers and their matched controls as one citation panel.
#
# Both sides end up in the same shape: a metadata table, and an integer matrix
# of citations per work per year covering 2012-2025 with real zeros (see
# R/citations.R for why that window and not the raw counts_by_year).
#
# ## One event date, from one file
#
# Two files carry a retraction year and they do not agree for 1,375 papers:
#
#   * data/controls/papers.csv `retraction_date` - the earliest record whose
#     nature is "Retraction", falling back to the earliest notice of any kind
#     (R/control_group.R). This is the date every control was given as its
#     pseudo-retraction date.
#   * data/clean/openalex_engagement.csv `retraction_year` - the earliest
#     Retraction Watch record of ANY nature, so for a paper that carried an
#     Expression of Concern first, it is the year of the concern.
#
# Taking the treated year from one and the control year from the other put the
# two arms on different clocks: 547 of those papers are dated at an Expression
# of Concern on the treated side and at the real Retraction on the control
# side, 1 to 27 years apart. Differencing two arms over non-overlapping years
# is not a before-and-after comparison of anything.
#
# So the event date comes from papers.csv for both arms, because papers.csv is
# what the matching was actually built on, and the same goes for publication
# year (papers.csv `year` is OpenAlex's, which is what the groups were keyed
# on; Retraction Watch's own original_year differs for 1,879 papers). The
# engagement values are kept beside them as validation columns, never used for
# a window. build_panel() asserts the two arms agree before returning.
#
# Reading and parsing the 58 MB control file takes a couple of minutes, so the
# result is cached. Nothing here decides anything about the analysis - it only
# puts the numbers where any analysis can reach them.

source("R/citations.R")
source("R/analysis_data.R")

PANEL_CACHE <- file.path("data", "interim", "citation_panel.rds")

citation_panel <- function(force = FALSE, cache = PANEL_CACHE) {
  if (!force && file.exists(cache)) return(readRDS(cache))
  ensure_analysis_inputs()

  message("reading the retracted papers...")
  eng <- utils::read.csv(file.path("data", "clean", "openalex_engagement.csv"),
                         stringsAsFactors = FALSE, fileEncoding = "UTF-8-BOM")
  eng <- eng[!is.na(eng$openalex_id) & eng$openalex_id != "", ]
  pap <- utils::read.csv(file.path("data", "controls", "papers.csv"),
                         stringsAsFactors = FALSE)

  # papers.csv is the event table; engagement supplies the citations.
  keep <- c("doi", "group_id", "year", "type", "source_id", "source_name",
            "field_id", "retraction_date", "retraction_nature")
  papers <- merge(pap[keep], eng[c("doi", "record_id", "openalex_id", "journal",
                                   "publisher", "work_type", "cited_by_count",
                                   "counts_by_year", "retraction_year",
                                   "original_year", "retraction_nature")],
                  by = "doi", suffixes = c("", "_eng"))
  papers$event_year <- as.integer(substr(papers$retraction_date, 1L, 4L))
  papers$pub_year <- suppressWarnings(as.integer(papers$year))
  papers$record_id <- suppressWarnings(as.integer(papers$record_id))
  papers$cited_by_count <- suppressWarnings(as.integer(papers$cited_by_count))
  # Kept only so the report can say how many papers the two files disagree on.
  papers$eng_event_year <- suppressWarnings(as.integer(papers$retraction_year))
  papers$rw_pub_year <- suppressWarnings(as.integer(papers$original_year))
  papers$event_disagrees <- !is.na(papers$eng_event_year) &
    papers$event_year != papers$eng_event_year
  papers$nature_disagrees <- papers$retraction_nature != papers$retraction_nature_eng

  message("  parsing yearly citations for ", nrow(papers), " papers...")
  mp <- counts_matrix(papers$doi, papers$counts_by_year)
  papers$history_visible <- history_visible(mp, papers$cited_by_count)
  papers$counts_by_year <- NULL
  papers$retraction_year <- NULL
  papers$original_year <- NULL

  message("reading the control group (58 MB)...")
  ctl <- utils::read.csv(file.path("data", "controls", "controls_v1.csv"),
                         stringsAsFactors = FALSE)
  # A control can serve more than one retracted paper, so its citation history
  # is parsed once and the link table points at it.
  u <- !duplicated(ctl$control_doi)
  message("  parsing yearly citations for ", sum(u), " distinct controls...")
  mc <- counts_matrix(ctl$control_doi[u], ctl$counts_by_year[u])
  cited_u <- suppressWarnings(as.integer(ctl$cited_by_count[u]))
  vis <- history_visible(mc, cited_u)

  controls <- data.frame(
    control_doi = ctl$control_doi,
    retracted_doi = ctl$retracted_doi,
    group_id = ctl$group_id,
    match_level = suppressWarnings(as.integer(ctl$match_level)),
    control_type = ctl$control_type,
    pub_year = suppressWarnings(as.integer(ctl$control_year)),
    event_year = as.integer(substr(ctl$pseudo_retraction_date, 1L, 4L)),
    cited_by_count = suppressWarnings(as.integer(ctl$cited_by_count)),
    stringsAsFactors = FALSE)
  controls$row <- match(controls$control_doi, rownames(mc))
  controls$history_visible <- vis[controls$row]
  papers$row <- seq_len(nrow(papers))

  # The two arms must be on the same clock. This is the check that would have
  # caught the mismatched event dates, so it runs on every build.
  paired <- match(controls$retracted_doi, papers$doi)
  ok <- is.na(paired) | controls$event_year == papers$event_year[paired]
  if (!all(ok, na.rm = TRUE)) {
    stop(sum(!ok, na.rm = TRUE), " control rows are clocked on a different ",
         "event year than their retracted paper; the two arms disagree.")
  }
  orphan <- sum(is.na(paired))
  if (orphan) message("  note: ", orphan, " control rows name a paper not in the panel")

  panel <- list(papers = papers, mp = mp, controls = controls, mc = mc,
                years = as.integer(colnames(mp)))
  dir.create(dirname(cache), showWarnings = FALSE, recursive = TRUE)
  saveRDS(panel, cache, compress = "xz")
  message("cached: ", cache)
  panel
}

# The Retraction Watch dimensions the client asked for, in his order:
# reason, country, publisher, discipline. One row per record_id per value,
# because a retraction can carry several reasons, countries and subjects.
panel_dimensions <- function() {
  ensure_analysis_inputs()
  rd <- function(f) utils::read.csv(file.path("data", "clean", f),
                                    stringsAsFactors = FALSE, fileEncoding = "UTF-8-BOM")
  list(reason = rd("reasons.csv"),
       country = rd("countries.csv"),
       subject = rd("subjects.csv"),
       publisher = ensure_publisher_families())
}
