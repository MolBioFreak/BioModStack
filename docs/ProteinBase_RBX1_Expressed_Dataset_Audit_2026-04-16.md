# ProteinBase RBX1 Expressed/Tested Dataset Audit

Date: 2026-04-16

Goal: Re-run the earlier RBX1 discrepancy analysis against the supposed "expressed/tested" release rather than only the 322 selected-submissions bundle and the 11,784-row main/full export.

## Executive summary

There is not currently a clean public RBX1 "expressed/tested" dataset exposed on ProteinBase in the way the competition page copy implies.

What I found instead is:

1. The local file previously identified as the expressed/tested dataset,
   `/home/dalab/text_ProteinBases_RBX1_competition_binder_data_sequences_expressed_tested_20260414_160516.json`,
   is not a sequence dataset at all. It is a list of 8 web-search results with fields `title`, `href`, and `body`.

2. The actual public RBX1 protein explorer view on ProteinBase is:
   `https://proteinbase.com/proteins?targetSlugs=rbx1&pageSize=200`
   and it exposes 101 public RBX1 proteins.

3. That 101-protein public RBX1 view does not currently behave like a true expressed/tested release:
   - `expressed=true` filter returns 0 rows
   - `hasExperimentalData=true` filter returns 0 rows
   - all 858 parsed evaluation records are computational, not experimental
   - 0 rows carry KD metrics
   - 0 rows carry competitionResults entries
   - an example protein page (`/proteins/lunar-falcon-lotus`) explicitly says `No experimental data` / `This protein hasn't been validated in the lab yet.`

4. The 101 public RBX1 proteins are not a new wet-lab cohort. They are a strict exact-match subset of both earlier RBX1 exports:
   - 101/101 exact sequence matches to the 322 selected dataset
   - 101/101 exact sequence matches to the 11,784-row main/full dataset

5. Your corrected 31-design set is still absent here:
   - 0/31 exact sequence matches to `/home/dalab/biomodstack/biomodstack/RBX1_proteinbase_submission.csv`

Bottom line: the currently exposed public RBX1 "experimental" surface is not a real expressed/tested result dataset. It is a 101-sequence public projection of already-public computational entries, and it does not change the earlier Christian-specific conclusion.

---

## Source validation

### 1. The candidate local "expressed_tested" JSON is not the dataset

Path checked:
- `/home/dalab/text_ProteinBases_RBX1_competition_binder_data_sequences_expressed_tested_20260414_160516.json`

Observed structure:
- top-level type: list
- row count: 8
- row keys: `title`, `href`, `body`

Representative entries include:
- `GEM x Adaptyv: RBX1 Binder Design Competition | Proteinbase`
- `Competition-2026 — GEM Workshop`
- unrelated RBX1 reference pages

So this file is a search-result capture, not a sequence/result export.

### 2. Public ProteinBase source actually inspected

Competition page:
- `https://proteinbase.com/competitions/gem-adaptyv-rbx1`

Competition page claims:
- `Results have been released. View the winning submissions and explore experimental data`
- body text says all submitted sequences and experimental data are published open-source on ProteinBase

But the same page's Stage 2 panel still shows:
- `Binding Affinity Characterization`
- `pending`

Public RBX1 protein explorer used for actual data extraction:
- `https://proteinbase.com/proteins?targetSlugs=rbx1&pageSize=200`

Additional filter checks:
- `https://proteinbase.com/proteins?targetSlugs=rbx1&expressed=true&pageSize=200`
- `https://proteinbase.com/proteins?targetSlugs=rbx1&hasExperimentalData=true&pageSize=200`

These filter checks are important because they directly test whether ProteinBase is exposing structured expression/experimental state for RBX1.

---

## What the public RBX1 protein view actually contains

Parsed from the embedded `initialProteins` payload on the RBX1 proteins page:

- public RBX1 proteins: 101
- author count: 21
- length range: 14 aa to 243 aa
- median length: 116 aa

Evaluation content:
- total evaluation records across the 101 proteins: 858
- evaluation type counts:
  - computational: 858
  - experimental: 0

Rows with key signals:
- rows with any KD metric: 0
- rows with any experimental evaluation: 0
- rows with any competitionResults entries: 0
- rows with `expressed=true`: 0
- rows with `hasExperimentalData=true`: 0

Common metrics present:
- `esmfold_plddt`: 177 occurrences
- `boltz2_ipsae`: 102 occurrences
- `boltz2_plddt`: 102 occurrences
- `design_class`: 97 occurrences
- `esmfold_structure_prediction`: 97 occurrences
- `molecular_weight`: 97 occurrences
- `esmfold_stylized_image`: 93 occurrences
- `seqidentity`: 93 occurrences

Rows carrying broad computational metric families:
- rows with Boltz2 metrics: 101
- rows with ipSAE: 101
- rows with ESMFold metrics: 96
- rows with molecular_weight: 96

Interpretation:
- this looks like a computationally annotated design subset
- it does not look like a published expressed/tested wet-lab result table

---

## Relationship to the earlier RBX1 exports

Compared against:
- selected bundle: `/home/dalab/Desktop/proteinbase_rbx1_selected_322_bundle/selected_submissions.jsonl`
- full/main export: `/home/dalab/biomodstack/biomodstack/inputs/imports/proteinbase_rbx1_main_submissions_11784.jsonl`
- corrected local submission set: `/home/dalab/biomodstack/biomodstack/RBX1_proteinbase_submission.csv`

### Exact sequence and ID overlap

For the 101 public RBX1 proteins:
- 101/101 exact sequence matches exist in the 322 selected dataset
- 101/101 exact slug/ID matches exist in the 322 selected dataset
- 101/101 exact sequence matches exist in the 11,784-row full/main dataset
- 101/101 exact slug/ID matches exist in the 11,784-row full/main dataset
- 0/31 exact sequence matches exist in the corrected Christian submission CSV

This means the public RBX1 page is not revealing a new result cohort. It is fully nested inside both earlier exports.

### Author coverage versus the 322 selected set

Selected dataset author count from earlier analysis: 48
Public RBX1 protein-view author count: 21

So the public RBX1 page covers only 21 of the 48 selected-set authors.

Selected authors absent from the public 101-protein view include, among others:
- `professionalmouthpipettor`
- `nanogenomic`
- `ananaya-jain`
- `aryan-chandak`
- `bingyi-zhao`
- `brandon-cantrell`
- `david-gershelis`
- `falmassen`
- `guanjiaweitaskin`
- `hz3519`
- `jayashanmuhesh-rs`
- `jonah-kallenbach`
- `luciau`
- `magicat`
- `mike-minson`
- `miles-mcgibbon`
- `niki-iva`
- `nourelden-rihan`
- `peter_hanna`
- `pimi`

So this public RBX1 page is not just "the expressed/tested layer" over the full selected cohort. It is a narrower subset covering less than half of the selected authors.

---

## Distribution inside the 101 public RBX1 proteins

Top author counts:
- 11 authors contribute exactly 7 proteins each:
  - `pacesalab`
  - `kai-yi`
  - `getu-tadesse`
  - `reilly-osadchey`
  - `zhimeng-zhou`
  - `wufandi`
  - `d-barradas`
  - `agitter`
  - `game_player`
  - `liquid.ai`
  - `ningyuan-tang`
- 2 authors contribute 6 each:
  - `richard-shuai`
  - `x.rustamov`
- 2 authors contribute 3 each:
  - `ben-shor`
  - `ievapudz`
- 6 authors contribute 1 each:
  - `drtheone`
  - `vansh-sethi`
  - `andrew_huang`
  - `maxk`
  - `peter-krinjar`
  - `hong-jun-bai`

Top design-method labels:
- `UNKNOWN`: 24
- `RFdiffusion`: 13
- `BindCraft 2`: 7
- `AF2 Hallucination with ADFlip`: 7
- `PepMind + Alphafold3`: 7
- `moPPIt`: 7
- `Bagel+Solumpnn`: 7
- `LFM2 Customization`: 7
- `Protein Hunter + Caliby`: 6
- `FoldCraft`: 6
- `TEA-leaves`: 3

This distribution again looks like a curated computational subset with the same max-7-per-author flavor already seen in the selected dataset, not a separate wet-lab release structure.

---

## Christian-specific implications

Your corrected local set still does not appear in this public RBX1 view.

Exact-match result against:
- `/home/dalab/biomodstack/biomodstack/RBX1_proteinbase_submission.csv`

Outcome:
- exact corrected sequence matches found: 0/31

So the public 101-protein RBX1 page does not rescue the discrepancy between:
- your organizer-approved corrected entries
- the public selected set
- the public full/main set

It remains consistent with the earlier conclusion:
- selected contains some organizer-mediated/corrected outcomes relevant to your case
- full/main does not cleanly reflect that corrected submission path
- the public RBX1 protein explorer also does not expose your corrected set

---

## Interpretation

The strongest data-first interpretation is:

1. ProteinBase's RBX1 competition landing page copy is ahead of its structured public data release.
   - It says experimental data are released/open-source.
   - But the live structured RBX1 protein explorer exposes no expression-positive rows, no `hasExperimentalData=true` rows, no KD metrics, and no experimental evaluation records.

2. The 101-protein public RBX1 view is not a true expressed/tested dataset.
   - It is a strict subset of the already-public selected/full computational exports.
   - It preserves the same author-level capped-selection pattern.
   - It omits many selected authors entirely.

3. As of this audit, the public site does not expose a sequence-level RBX1 wet-lab result table that could replace the earlier selected/full discrepancy analysis.

So if the request is literally "generate the same doc off of the expressed dataset," the honest answer is:
- there is no presently exposed public RBX1 expressed/tested dataset with structured experimental outcomes to run that analysis on
- the nearest public proxy is this 101-protein RBX1 page, and it does not alter the earlier conclusions

---

## Files used

Local files:
- `/home/dalab/text_ProteinBases_RBX1_competition_binder_data_sequences_expressed_tested_20260414_160516.json`
- `/home/dalab/Desktop/proteinbase_rbx1_selected_322_bundle/selected_submissions.jsonl`
- `/home/dalab/biomodstack/biomodstack/inputs/imports/proteinbase_rbx1_main_submissions_11784.jsonl`
- `/home/dalab/biomodstack/biomodstack/RBX1_proteinbase_submission.csv`

Web sources checked:
- `https://proteinbase.com/competitions/gem-adaptyv-rbx1`
- `https://proteinbase.com/proteins?targetSlugs=rbx1&pageSize=200`
- `https://proteinbase.com/proteins?targetSlugs=rbx1&expressed=true&pageSize=200`
- `https://proteinbase.com/proteins?targetSlugs=rbx1&hasExperimentalData=true&pageSize=200`
- `https://proteinbase.com/proteins/lunar-falcon-lotus`

Generated supporting artifact:
- `/tmp/rbx1_public_target_101_summary.csv`

---

## Practical next step

If you want the true expressed/tested analysis rather than this public-gap audit, the next required input is not more scraping. It is the actual structured result export from Adaptyv/ProteinBase, for example one of:

- a CSV/JSONL with per-sequence expression and/or KD fields
- the hidden collection ID / download URL for the real RBX1 experimental results release
- a private export attached to the community results write-up

Once that exists, the same overlap/reconciliation analysis can be rerun directly against it.
