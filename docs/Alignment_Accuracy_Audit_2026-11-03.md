# Alignment accuracy audit — 3 November 2026

## Qualification status

The application-level defects listed below are corrected. **Universal scientific
accuracy is not certified. MUSCLE 5.3 fails a known-edit pUC19 alignment control.**
A zero process exit code, preserved input bases, or 100% identity over a partial
overlap cannot establish that an inferred alignment is biologically correct.

This audit used the public, nonviral pUC19 reference and synthetic DNA/RNA/protein
fixtures. No capsid plasmid alignments were run and no user sequence records were
modified. Independent reviewers covered statistics, native execution, and source
coordinates/retention; the integration pass also checked the molecular pairwise
viewer and displayed interpretation.

## Public reference and reproducibility

Reference: [NCBI GenBank M77789.2, pUC19](https://www.ncbi.nlm.nih.gov/nuccore/M77789.2),
2,686 bases. Uppercase concatenated sequence SHA-256:
`7c1183b16368b1435452d99152d7c95aa04fc4a5412e563a5ef1d0fbf0cf873b`.
The independently downloaded NCBI FASTA exactly matches the copy distributed with
pLannotate. The checked-in fixture and source manifest are under
`packages/bms-alignment-core/tests/fixtures/puc19.*`. Test edits are computational
controls with known differences; they are not proposed biological designs.

Pinned native image SHA-256:
`198186c9238df383a95215117ce3b55402cb9547f9f6ea58d2f4af25d634717a`.
Observed versions: MAFFT 7.525, MUSCLE 5.3, FAMSA 2.5.2, TWILIGHT 0.3.0.

## Confirmed defects corrected

- MAFFT's managed execution now creates the submitted tree/matrix/anchor files
  and preserves the requested focus-row mapping.
- Native completion and historical native-result reading share output-integrity
  validation. Changed bases, unknown/duplicate rows, unrequested missing rows,
  empty and ragged alignments cannot masquerade as successful intact alignments.
  Explicit native filtering/trimming is distinguished from corruption. Authorized
  FAMSA column trimming permits verified residue deletion only; source coordinates
  remain unknown when no exact column mask is retained. TWILIGHT tree pruning
  does not authorize deleting input sequences.
- Authorized MAFFT reverse complements retain their row identity, orientation,
  and source positions through the document, comparison, and viewer. The marker
  alone is insufficient evidence. [MAFFT direction semantics](https://mafft.cbrc.jp/alignment/software/adjustdirection.html).
- Invalid engine/molecule combinations and mutated invalid input rosters fail
  before execution. MUSCLE Align is blocked at admission/execution because the
  pinned implementation ignores the forced molecule flags; historical results
  remain readable. Saved modes are not silently rewritten.
- Summary calculations reject duplicate IDs and invalid/expanding Unicode input.
  Identity remains exact, case-insensitive symbol identity at nongap pairs;
  matching N symbols do not establish a known matching base. The blanket claims
  that 100% proves identical sequences and that plasmids generally exceed a fixed
  identity threshold are removed. [EMBL-EBI notes that identity definitions differ](https://ebi-biows.gitdocs.ebi.ac.uk/documentation/faqs/bioinformatics/).
- Molecular pairwise alignment rejects multi-turn circular results and unsupported
  global/circular settings, reports reverse-query coordinates explicitly, and
  correctly reports local clipping. RNA reverse complements use evidence from
  both inputs. Circular search padding is not reported as biological flanks.
- Pairwise paste rejects multiple FASTA records instead of joining them; Unicode
  case folding cannot manufacture nucleotide symbols before validation. Insertion
  boundaries and circular block ends no longer display fictitious residue numbers.
- Export classification comes from the verified saved molecule. Missing or
  conflicting molecule metadata is rejected instead of defaulting to protein.

## Acceptance evidence

The routine regression tests contain the independent oracles; generated logs and
native outputs remain outside the source repository.

- Summary: 27 independent tests, including 729 symbol combinations, 135 seeded
  alignments, exact bin mass/position checks, directional coverage, means,
  gaps/no overlap, and summary limits. Existing read-model tests also pass.
- Molecular pairwise: 23 tests, including 200 independently scored affine-gap
  cases and nine full-length pUC19 cases: identity, substitution, insertion,
  deletion, reverse orientation, circular rotation, reverse rotation, local
  reverse-fragment coordinates, and rejection of repeated circular copies.
- Native execution: real MAFFT/MUSCLE/FAMSA/TWILIGHT runs, native ensembles,
  expert settings, cancellation/ownership, and deliberate output corruption.
- Source retention: native Job artifact → Project receipt → source removal →
  export/import → readback preserves artifact identity and sequence data.
- Frontend: mounted authoring, coordinate, renderer, shared-state, export, and
  handoff tests; direct formatting/input tests and TypeScript validation.

## Remaining scientific qualification failures

The five-row pUC19 known-edit control contains the original, identical copy, and
single-substitution/insertion/deletion controls. MAFFT and TWILIGHT recover the
expected differences. MUSCLE Align and Super5 both introduce extra mismatched
columns: 26,848 matching and eight mismatched pairwise columns, versus the planted
alignment's 26,852 matching and four mismatched columns, with the same gap budget.
The deletion pair alone reports 2,684/2,685 identical paired bases instead of
2,685/2,685. A direct MUSCLE CLI run reproduces the adapter output. This failure
remains an explicit strict expected qualification failure, not a passing accuracy
check. Nucleotide MUSCLE results are labeled unqualified for identity confirmation.

The pinned [MUSCLE Align implementation](https://github.com/rcedgar/muscle/blob/2cf9d33078c9a85697e38a6f3ad827e6862420df/src/align.cpp)
guesses nucleotide versus protein scoring despite the forced-type flags. Super5
honors those flags, but still fails the pUC19 known-edit control above.

This audit does not prove inferred homology for arbitrary inputs or every expert
parameter combination. Ten auxiliary MAFFT output/extraction options remain
explicitly unimplemented in the existing capability inventory; this work does not
claim to qualify those controls. Native unsupported combinations fail visibly.
Circular origin equivalence is tested in the molecular pairwise mode; native MSA
engines are not silently presented as circular aligners. Physical browser pixel
inspection and instrument acquisition are not part of this scientific test pass.
