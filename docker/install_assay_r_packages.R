#!/usr/bin/env Rscript

options(repos = c(CRAN = "https://cloud.r-project.org"))

r_install_ncpus <- function() {
  parsed <- suppressWarnings(as.integer(Sys.getenv("BMS_R_INSTALL_NCPUS", "1")))
  if (is.na(parsed) || parsed < 1) {
    return(1L)
  }
  parsed
}

cran_only_packages <- c("chromConverter")

cran_packages <- c(
  "RDML",
  "qpcR",
  "chipPCR",
  "qPCRtools",
  "DoE.base",
  "FrF2",
  "rsm",
  "AlgDesign",
  "DoE.wrapper",
  "qcc",
  "emmeans",
  "lme4",
  "desirability"
)

rqdeltact_packages <- c("RQdeltaCT")
tidyqpcr_cran_deps <- c("tibble", "rlang", "dplyr", "ggplot2", "scales", "readr", "forcats", "assertthat", "tidyr")
tidyqpcr_tarball <- "https://ropensci.r-universe.dev/src/contrib/tidyqpcr_1.0.tar.gz"
bioconductor_packages <- c("HTqPCR")

# Debian bookworm's r-base ships Matrix 1.5.x, but current MatrixModels/lme4
# dependency chains require Matrix >= 1.6.0.  Matrix 1.6-5 is the last known-good
# CRAN archive release that still supports R 4.2, so pin it before installing
# the DOE/statistics R stack.
ensure_matrix_for_r42 <- function() {
  installed <- rownames(installed.packages())
  needs_matrix <- !("Matrix" %in% installed) || utils::packageVersion("Matrix") < "1.6.0"
  if (needs_matrix) {
    install.packages(
      "https://cran.r-project.org/src/contrib/Archive/Matrix/Matrix_1.6-5.tar.gz",
      repos = NULL,
      type = "source",
      dependencies = c("Depends", "Imports", "LinkingTo"),
      Ncpus = r_install_ncpus()
    )
  }
}

ensure_matrix_for_r42()

install_missing <- function(pkgs, repos = getOption("repos")) {
  for (pkg in pkgs) {
    if (pkg %in% rownames(installed.packages())) {
      next
    }
    message("Installing R assay package: ", pkg)
    install.packages(pkg, repos = repos, dependencies = c("Depends", "Imports", "LinkingTo"), Ncpus = r_install_ncpus())
    if (!(pkg %in% rownames(installed.packages()))) {
      stop("Failed to install R package: ", pkg)
    }
  }
  invisible(TRUE)
}

install_missing(cran_only_packages, repos = c(CRAN = "https://cloud.r-project.org"))
install_missing(cran_packages, repos = c(CRAN = "https://cloud.r-project.org"))

ensure_ggally_for_r42 <- function() {
  installed <- rownames(installed.packages())
  if (!("GGally" %in% installed)) {
    install.packages(
      "https://cran.r-project.org/src/contrib/Archive/GGally/GGally_2.1.2.tar.gz",
      repos = NULL,
      type = "source"
    )
  }
  if (!("GGally" %in% rownames(installed.packages()))) {
    stop("Failed to install archived GGally 2.1.2 required by RQdeltaCT on R 4.2")
  }
}

ensure_ggally_for_r42()
install_missing(rqdeltact_packages, repos = c(CRAN = "https://cloud.r-project.org"))
install_missing(tidyqpcr_cran_deps, repos = c(CRAN = "https://cloud.r-project.org"))
if (!("tidyqpcr" %in% rownames(installed.packages()))) {
  message("Installing R assay package: tidyqpcr")
  install.packages(tidyqpcr_tarball, repos = NULL, type = "source", dependencies = FALSE)
}
if (!("tidyqpcr" %in% rownames(installed.packages()))) {
  stop("Failed to install R package: tidyqpcr")
}

if (!requireNamespace("BiocManager", quietly = TRUE)) {
  install.packages("BiocManager", repos = "https://cloud.r-project.org")
}
BiocManager::install(bioconductor_packages, ask = FALSE, update = FALSE, Ncpus = r_install_ncpus())
missing_bioc <- setdiff(bioconductor_packages, rownames(installed.packages()))
if (length(missing_bioc) > 0) {
  stop("Failed to install Bioconductor packages: ", paste(missing_bioc, collapse = ", "))
}

message("Installed BMS assay R packages: ", paste(c(cran_only_packages, cran_packages, rqdeltact_packages, "tidyqpcr", bioconductor_packages), collapse = ", "))
