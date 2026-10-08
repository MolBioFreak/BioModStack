# Pryor Golden Gate empirical observation bundle

Copyright © 2020 Pryor et al. **CC BY 4.0**:
https://creativecommons.org/licenses/by/4.0/ (legal code:
https://creativecommons.org/licenses/by/4.0/legalcode).

Pryor JM, Potapov V, Bilotti K, Pokhrel N, Lohman GJS (2020).
*Enabling one-pot Golden Gate assemblies of unprecedented complexity using
data-optimized assembly design.* PLOS ONE 15(9): e0238592.
https://doi.org/10.1371/journal.pone.0238592

These are the unchanged primary supplementary workbooks S1–S5. Individual
primary URLs and SHA-256 hashes are in manifest.json. The original article's
CC BY permission and assay methods are retained verbatim in
primary-license-methods.xml, extracted from the publisher's manuscript XML:
https://journals.plos.org/plosone/article/file?id=10.1371/journal.pone.0238592&type=manuscript
The workbooks were recovered from prior primary-source research: S1–S4 copies
were independently verified byte-identical to PLOS downloads; S5 was downloaded
directly from PLOS. The hashes were reverified during packaging. The grant used
here is the primary PLOS CC BY grant, not the Tatapov mirror's blanket ND notice.
No Tatapov code or older Potapov dataset is included.

Changes: BMS derives lossless JSON tables from each workbook, preserving both
independent axis orders and every integer observation. JSON is compressed using
gzip with timestamp zero. No symmetrization, normalization, smoothing, omission
of zero observations or imputation is applied. `convert_pryor.py` is the
reproducible standard-library-only conversion leaf; run it in place to verify
source and derived hashes and reproduce the parsed assets. Worksheet and
conversion version are in each asset and manifest. Python gzip headers can vary
between interpreter versions; release bytes were generated with Python 3.11.15
(zlib gzip timestamp zero) and content-checked with Python 3.12/openpyxl. Scientific content is JSON, not gzip metadata.

Attribution and these notices must accompany redistributed assets. The source
authors do not endorse BMS. This data license does not alter application-code
licensing. Reaction conditions describe the measurements, not a prescribed
protocol or permission to infer equivalent component concentrations for mixes.

Axes are physical protruding strands, each 5′→3′. The visual diagonal is
complementary joining, not same-sequence joining. S2 is 42°C/16°C cycling, not
37°C or a one-hour static assay. S5 has three-base ends. Observations are not
rate constants, yields, nick-closing probabilities or independent raw reads.
