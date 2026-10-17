# Figures for the report and the handover, in base R graphics - no package
# beyond what renv.lock already restores, so reproduce_results() redraws them
# on the client's machine.
#
# One chart form serves every figure here: a horizontal bar per category, one
# series, one colour. The rules it follows (the project's dataviz checklist):
# a single series gets a single colour and no legend - the title says what is
# plotted; the value sits at the bar's tip; gridlines are hairline and
# recessive; text is in the text colours, never the bar colour; bars are sorted
# by value unless the categories have their own order. Every figure is drawn
# from a CSV in data/reports, which is its table view.

FIG_COL <- list(bar = "#2a78d6", text = "#0b0b0b", text2 = "#52514e", grid = "#e2e1dc",
                surface = "#ffffff")

# A name short enough for an axis: "IEEE: Institute of ..." -> "IEEE",
# "American Society for ... (ASBMB)" -> "ASBMB".
short_name <- function(x, width = 34) {
  ab <- regmatches(x, regexpr("[(][A-Z][A-Za-z&]+[)]$", x))
  x[grepl("[(][A-Z][A-Za-z&]+[)]$", x)] <- gsub("[()]", "", ab)
  x <- sub(":.*$", "", x)
  ifelse(nchar(x) > width, paste0(substr(x, 1, width - 1), "…"), x)
}

# values: percentages; labels: one per bar; sort: largest at the top, or keep
# the given order top to bottom (for categories that have one, like time).
hbar <- function(file, labels, values, title, subtitle = NULL, source = NULL,
                 sort = TRUE, xmax = NULL) {
  if (sort) {
    o <- order(values)
  } else {
    o <- rev(seq_along(values))
  }
  labels <- labels[o]; values <- values[o]
  n <- length(values)
  # Room for the value labels past the longest bar, and no further: a 0-100
  # axis under bars that stop at 20 leaves them in a corner.
  if (is.null(xmax)) xmax <- min(100, ceiling(max(values) * 1.2 / 10) * 10)
  width_in <- 8.5
  row_in <- 0.32
  sub_lines <- if (is.null(subtitle)) 0 else length(strwrap(subtitle, 105))
  top_in <- 0.55 + 0.2 * sub_lines
  bottom_in <- if (is.null(source)) 0.55 else 0.8
  height_in <- top_in + bottom_in + n * row_in + 0.2
  grDevices::png(file, width = width_in, height = height_in, units = "in", res = 200,
                 bg = FIG_COL$surface)
  on.exit(grDevices::dev.off())
  graphics::par(family = "sans", col.axis = FIG_COL$text2, col.lab = FIG_COL$text2, mgp = c(2, 0.4, 0))
  left_in <- max(graphics::strwidth(labels, units = "inches", cex = 0.85)) + 0.25
  graphics::par(mai = c(bottom_in, left_in, top_in, 0.75))
  graphics::plot.new()
  graphics::plot.window(xlim = c(0, xmax), ylim = c(0.4, n + 0.6), xaxs = "i", yaxs = "i")
  ticks <- pretty(c(0, xmax), n = 5)
  ticks <- ticks[ticks <= xmax]
  graphics::abline(v = ticks, col = FIG_COL$grid, lwd = 1)
  half <- 0.30
  graphics::rect(0, seq_len(n) - half, values, seq_len(n) + half, col = FIG_COL$bar, border = NA)
  # Each value on a patch of the surface colour, so a gridline never runs
  # through the number.
  lab <- sprintf("%.1f%%", values)
  x0 <- values + xmax * 0.012
  w <- graphics::strwidth(lab, cex = 0.8)
  h <- graphics::strheight("0", cex = 0.8)
  graphics::rect(x0 - xmax * 0.004, seq_len(n) - h * 0.9, x0 + w + xmax * 0.004,
                 seq_len(n) + h * 0.9, col = FIG_COL$surface, border = NA, xpd = TRUE)
  graphics::text(x0, seq_len(n), lab, adj = 0, cex = 0.8, col = FIG_COL$text, xpd = TRUE)
  graphics::axis(2, at = seq_len(n), labels = labels, las = 1, tick = FALSE, cex.axis = 0.85,
                 col.axis = FIG_COL$text)
  graphics::axis(1, at = ticks, labels = paste0(ticks, "%"), tick = FALSE, cex.axis = 0.8)
  graphics::mtext(title, side = 3, line = 0, adj = 0, outer = FALSE, font = 2, cex = 1.05,
                  col = FIG_COL$text, at = graphics::grconvertX(0.02, "ndc", "user"),
                  padj = -(sub_lines * 1.25 + 0.6))
  if (!is.null(subtitle)) {
    graphics::mtext(paste(strwrap(subtitle, 105), collapse = "\n"), side = 3, line = 0.4,
                    adj = 0, cex = 0.78, col = FIG_COL$text2,
                    at = graphics::grconvertX(0.02, "ndc", "user"))
  }
  if (!is.null(source)) {
    graphics::mtext(source, side = 1, line = 2.1, adj = 0, cex = 0.7, col = FIG_COL$text2,
                    at = graphics::grconvertX(0.02, "ndc", "user"))
  }
  invisible(file)
}
