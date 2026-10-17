# Which finished groups matched on a notice type (retraction, erratum), and how
# many controls came from them. Local cache only.

dir <- file.path("data", "controls")
groups <- utils::read.csv(file.path(dir, "groups.csv"), colClasses = "character")
f <- file.path(dir, "shard_1", "cache", paste0(gsub("[^A-Za-z0-9]+", "_", groups$group_id), ".rds"))
groups$cached <- file.exists(f)
notice <- groups$type %in% c("retraction", "erratum")
cat("notice-type groups:", sum(notice), " cached:", sum(notice & groups$cached),
    " papers in them:", sum(as.integer(groups$n_papers[notice])), "\n")
ctl <- do.call(rbind, Filter(NROW, lapply(f[notice & groups$cached], readRDS)))
cat("controls cached for notice-type groups:", NROW(ctl), "\n")
if (NROW(ctl)) print(table(ctl$control_type))
