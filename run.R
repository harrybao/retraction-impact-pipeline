# CS-43: one entry point. Open cs43.Rproj and run
#
#     source("run.R")
#
# Everything in this project is reachable from R, and this file is the only
# thing anyone has to remember. Stages 1 and 2 are Python - a browser crawler
# and a text extractor - and they are not rewritten; they are called as child
# processes with an interpreter this project builds itself, so nobody installs
# Python by hand. Stage 3, every table and every figure, is R.
#
# After sourcing, cs43_help() lists what can be run.

local({
  root <- tryCatch(rprojroot::find_root(rprojroot::has_file("requirements.txt")),
                   error = function(e) getwd())
  if (!identical(normalizePath(root, winslash = "/"),
                 normalizePath(getwd(), winslash = "/"))) {
    message("note: run.R expects the working directory to be the project root (", root, ")")
  }
})

for (f in c("python.R", "stages.R", "handover.R", "analysis_data.R",
            "citations.R", "citation_panel.R", "publisher_families.R",
            "control_group.R", "did.R", "labelling.R",
            "llm.R", "llm_text.R", "llm_eval.R", "llm_evidence.R",
            "notice_news.R", "notice_rules.R", "reproduce.R")) {
  source(file.path("R", f))
}

cs43_help <- function() {
  cat("
CS-43 - retracted literature: collection, cleaning and impact analysis

FIRST RUN
  use_pipeline_python()     build or find the Python interpreter stages 1-2 need
  install_browser()         the browser the crawler drives
  set_api_keys(...)         Altmetric / Elsevier keys, if you have them
  RWPIPE_CONTACT            contact email for API requests; default
                            xbao0816@uni.sydney.edu.au. Set a valid ASCII email
                            with Sys.setenv(RWPIPE_CONTACT = 'you@example.edu').

  The analysis needs data/clean, data/controls, data/llm and data/labelling
  from the dataset package (see HANDOVER.md). Without them,
  ensure_analysis_inputs() unpacks what it can from the bundle in data/share;
  the citation analysis runs on that alone, the media and notice analysis
  does not.

EVERY TABLE AND FIGURE, FROM THE LOCAL DATASET
  reproduce_results()       runs 14 collection and Stage 3 analyses, one R process
                            each, logs to logs/reproduce/, and says which of
                            the result files it wrote are identical to ours
                            (data/reports_reference.csv).
                            Needs the handover dataset; no internet, no model.
                            Includes news coverage union and social media limits.

CHECK IT WORKS  (a few DOIs from the Retraction Watch CSV, end to end,
                 into a new folder under runs/ that leaves the dataset alone)
  check_package('<package>/SHA256-cs43.csv')
                            every file of a copied package present and unchanged
  res <- run_pipeline(c('10.1371/journal.pone.0066275', 'record:70871'))
                            paper or notice DOIs, or 'record:<Record ID>'
                            (at most 1,000 per run); one row per page in the result and in
                            <data_dir>/run_report.csv, with how it was obtained
                            or why not, and its OpenAlex, Altmetric, PlumX data
  save_page('<doi>', attr(res, 'data_dir'), open = TRUE)
                            a captured page as an .mhtml file, opened in the
                            browser (Chrome or Edge); data_dir NULL = the dataset

STAGE 1 AND 2 - collection and cleaning
  pipeline_steps()          the order a full rebuild follows
  rebuild_all()             print the plan; rebuild_all(confirm = TRUE) runs it
                            (days, and needs the keys listed in the plan)
  Individual steps: build_queue(), crawl(), verify_archives(),
  recover_*(), enrich_metadata(), collect_openalex(), collect_altmetric(),
  collect_altmetric_notices(), collect_plumx(), collect_plumx_notices(),
  collect_wikipedia(), extract_text(), export_all(), build_share().
  Every one takes data_dir to work somewhere else.
  verify_archives() is read-only unless apply = TRUE; replacing old extracted
  text requires extract_text(upgrade = TRUE). Back up before an approved update.
  Altmetric collectors leave X posts out by default (exclude = 'twitter'): the key's allowance of
  X post ids is spent. Pass exclude = NULL once it is renewed.

STAGE 3 - the analysis
  Part 1, the matched control group (already collected; three days of metered
  OpenAlex lookups, shipped in data/share/cs43_control_group.zip):
    control_groups()        build the matching groups
    collect_controls()      fill them, resumable, stops at the daily allowance
    merge_controls()        -> data/controls/controls_v1.csv

  Part 2, what retraction does to citations:
    Rscript analysis/citation_impact.R
    -> data/reports/citation-impact-*.csv and two figures
    The method is in R/did.R. citation_panel() caches the panel both arms
    are read from.

  Labelling (seven labellers, majority vote, coordinator breaks ties):
    score_labelling('data/labelling/pilot')   once the sheets come back
    -> agreement, the majority label per item, and the ties to settle

  Part 3, classifying the media items and the notices. The client has no
  budget for a commercial model, so this runs on an open-weight model on
  local hardware, through Ollama. Start the server first; the model name
  comes from CS43_LLM_MODEL.
    llm_available()         is a model server answering?
    Rscript analysis/llm_validate.R
      Asks the candidate models the same questions the seven labellers
      answered, and scores them the way a labeller is scored: each labeller
      set aside in turn, the other six vote, model judged against the same
      six. -> data/reports/llm-validation.csv
    classify_corpus(c('A', 'AN', 'B'))
      (or Rscript analysis/llm_classify_corpus.R A|AN|B [limit])
      A: media texts under paper DOIs; AN: under notice DOIs; B: the
      notices. The whole corpus, resumable. Items with no readable text are answered
      A0 by rule rather than asked (R/llm_text.R explains why).
    evidence_check(...)     is a quoted reason really in the text? On media
                            items an invented quote roughly halves the chance
                            the label matches the labellers.

WHERE THINGS ARE
  HANDOVER.md               the guide for the client
  data/reports/             every number this project has published
  data/share/               the bundle the group and the client work from
  tests/                    Rscript tests/r/test_*.R, and pytest for Python
")
  invisible(NULL)
}

message("CS-43 loaded. cs43_help() lists what can be run.")
