#!/usr/bin/env Rscript

options(repos = c(
  ropensci = "https://ropensci.r-universe.dev",
  CRAN = "https://cloud.r-project.org"
))

cran_packages <- c(
  "chromConverter",
  "RDML",
  "qpcR",
  "chipPCR",
  "qPCRtools",
  "RQdeltaCT",
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

runiverse_packages <- c("tidyqpcr")
bioconductor_packages <- c("HTqPCR")

install_missing <- function(pkgs, repos = getOption("repos")) {
  installed <- rownames(installed.packages())
  missing <- setdiff(pkgs, installed)
  if (length(missing) == 0) {
    return(invisible(TRUE))
  }
  install.packages(missing, repos = repos, dependencies = TRUE, Ncpus = max(1, parallel::detectCores() - 1))
  still_missing <- setdiff(pkgs, rownames(installed.packages()))
  if (length(still_missing) > 0) {
    stop("Failed to install R packages: ", paste(still_missing, collapse = ", "))
  }
  invisible(TRUE)
}

install_missing(cran_packages)
install_missing(runiverse_packages)

if (!requireNamespace("BiocManager", quietly = TRUE)) {
  install.packages("BiocManager", repos = "https://cloud.r-project.org")
}
BiocManager::install(bioconductor_packages, ask = FALSE, update = FALSE, Ncpus = max(1, parallel::detectCores() - 1))
missing_bioc <- setdiff(bioconductor_packages, rownames(installed.packages()))
if (length(missing_bioc) > 0) {
  stop("Failed to install Bioconductor packages: ", paste(missing_bioc, collapse = ", "))
}

message("Installed BMS assay R packages: ", paste(c(cran_packages, runiverse_packages, bioconductor_packages), collapse = ", "))
