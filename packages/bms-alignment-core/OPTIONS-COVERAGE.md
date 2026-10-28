# Pinned family-MSA native settings and option dispositions

This is a **partial integration**, not a 100% upstream-coverage claim. The
machine-readable [option-dispositions.json](option-dispositions.json) records
an explicit disposition for each captured option, including missing controls
and unresolved source applicability rather than hiding them as exclusions.

## Inventory basis and scope

- MAFFT 7.525: 173 non-commented wrapper option tokens in the pinned
  `mafft-7.525-without-extensions/core/mafft.tmpl` argument branches, with source
  SHA256. This includes disabled branches, aliases and auxiliary workflows;
  it is not a claim that all 173 are valid inference settings.
- FAMSA 2.5.2-2598410: 28 options in the actual pinned `famsa -help` output.
- MUSCLE 5.3 (2cf9d33): 124 declarations from pinned `src/myopts.h`, with source
  SHA256 and per-option C++ consumers. `-align` and `-super5` map to `mode`;
  other native commands are outside this full-family surface. Consumer-file
  lists are source evidence, not a fully traced transitive call graph. Options
  without a detected consumer are labeled declaration-only, not proven native
  capabilities. Remaining command applicability is stated explicitly.
- TWILIGHT 0.3.0 CPU: 43 long options in actual pinned `--help` output. Its
  approved scope here is nucleotide accurate pairwise/TCS, not protein,
  merge/add or a replacement mapping/search workflow.

## Typed files and newly exposed controls

The closed request owner is `bms_alignment/models.py`. The generated browser
schema and schema-driven editor expose the same fields. File inputs accept
local UTF-8 text or direct text editing, never a host path or arbitrary URL.
They have an explicit null/omit button: an empty file remains an empty string,
not a silently substituted native default. No file-format or tree-completeness
observation creates a new submission gate.

| Native input | Request/UI field | Materialization and semantics |
|---|---|---|
| MAFFT `--treein` | `treein` | Exact native **merge-table text**, not Newick; 1-based roster indices and branch lengths; `treein.txt` retained. No invented Newick conversion. |
| MAFFT `--aamatrix` | `aamatrix` | Amino-acid header, lower triangular scores, optional native frequency/norescale record; exact `aamatrix.txt` retained. Compiled after named substitution models. |
| FAMSA `-gt import` | `guide_tree=import`, `guide_tree_newick`, `leaf_mapping` | Original Newick retained separately; only explicitly supplied leaf→row mappings translate to reversible native row IDs. Unknown/missing leaves are observations. Native input leaves can already be `bmsrow00000001`, etc. |
| TWILIGHT `--matrix` | `matrix` | Exact nucleotide matrix header/scores retained as `matrix.txt`; no BMS normalization. |

MAFFT expert controls added: `fft`, `threadit`, `threadtb`, PartTree/DP-PartTree,
`partsize`, `groupsize`, minimum/average/mixed linkage, `unweight`,
`termgappenalty`, `legacygappenalty`, `anysymbol`, `preservecase`, both direction
adjustment choices, memory-saving choice, `fmodel`, N wildcard/zero scoring,
`opdist`, and `exp`. Prior iterative/local/skip penalties, substitution models,
seed, strategy, iterations, output order and tree output remain available.
The compiler uses explicit None tests; zero does not select a fallback and
false does not emit a boolean flag. If overlapping native controls are chosen,
the saved values and argv retain them: no conflict rewriting is added.
FFT precedes no-FFT; named linkage precedes mixed linkage; named substitution
models are BLOSUM, JTT, TM, Kimura, then the custom matrix. Native strategy/auto
logic may still override or ignore options; receipts do not claim to decode
that internal state. Direction adjustment keeps raw input/native output and
does not fabricate a source-position map for reversed residues.

MUSCLE additions: consistency/refinement iterations, explicit `minsuper`,
output ordering, verbosity, six pair-HMM transition controls. Null permutation
and perturbation now mean omit the flag (single-run native defaults remain
none/zero). **Explicit** `permutation=none` or `perturb=0` is never dropped,
including with an ensemble. Old saved ensemble drafts containing those explicit
values now reach the native conflict instead of being silently repaired; choose
null to request native ensemble defaults. The editor shows this distinction.
The native `-align` + triggered `-minsuper` path fails in this pin with
`globalinputms.cpp(70) assert failed: g_GlobalMS == 0`; this failure is retained
and tested, not replaced by a successful direct Super5 run. Direct Super5 works.

FAMSA full-family inference help controls are exposed including tree import;
`gt_export`/`dist_export` replace alignment with a tree/distance-only operation,
not a simultaneous MSA option. Gzip is unimplemented output transport, not a
scientific inference control. Profile-profile input remains excluded.

## Tests and limits

The executed scratch CPU SIF SHA256 is
`198186c9238df383a95215117ce3b55402cb9547f9f6ea58d2f4af25d634717a`.
Source revisions: FAMSA `259841074f86361b50367bed396b4e270dfe343a`,
MUSCLE `2cf9d33078c9a85697e38a6f3ad827e6862420df`,
TWILIGHT `4839e073048c2f60f2949a8342b6dc0a704ddbed`.
The scoped package run (excluding HTTP and project-remap owners) passed **94
tests with no skips**, with all four native wrapper variables and `BMS_MSA_SIF`
set. The focused mounted inventory/Project editor suite passed 12 tests with
`BMS_ALIGNMENT_SCHEMA` set; the separate editor/Project suite passed 12 tests.
A scratch mounted test additionally exercised new file upload/text, empty
string, null omission and zero/false serialization (1 passed). These mounted
checks mock API/Project services; they are not a real HTTP journey.

`test_native_settings_files.py` executes the pinned CPU image on harmless tiny
synthetic protein/DNA rosters. It verifies tree and matrix file bytes, native
FASTA parsing/row count/degapped content, receipts, imported FAMSA mappings,
selected MAFFT expert combinations, TWILIGHT matrix, MUSCLE HMM/iteration/order
settings, and the genuine minsuper failure. `test_settings_inventory.py` checks
generated schema parity and explicit false/zero/null compilation; native engine
tests cover baseline molecules/ensembles. These are not accuracy benchmarks,
not proof of every option's scientific effect, and not a deployed/API/browser
save/retry/clone acceptance claim. Native invalid interactions are retained as
native outcomes, not new BMS admission rules.

## Reconciled remaining native controls

The previous 48 MAFFT and 14 MUSCLE ambiguous rows are now resolved against
pinned execution consumers, not merely renamed. `option-dispositions.json`
contains per-option parser lines and execution evidence.

- **MAFFT: 40 of those spellings implemented; 8 concretely unavailable/out of scope.**
  Typed `pair_method`, `tree_method`, and `refinement_policy` preserve the exact
  selected aliases (including large/memsavetree and adhoc2/simplehillclimbing).
  The original strategy, scoring controls, and defaults are unchanged; overrides
  are explicit and both the requested settings and actual argv are retained.
  Additional scalar controls cover shift/unalign scoring, global pair penalties,
  local-homology limits, minimum weight, sparse pickup, rigorous SP, iteration
  scheduling and terminal anchors. `anchors` preserves the original text file:
  seven integers per row (i, j, starti, endi, startj, endj, score), with 1-based
  sequence and position indices, as read by `io.c:6378-6445`.
  `focus_rows` is an explicit one-row-ID-per-line selection. It materializes
  reversible `_focus_` native IDs and a retained row-map; it does not relabel
  the source roster or infer focus from a display label. Unknown selected IDs
  are observations. Native failures are not converted into admission gates.
- **MAFFT exclusions:** 6mer/10mermultipair's `ktuplesmulti` branch requires
  fragments (`mafft.tmpl:2657-2667`); multipair explicitly requires
  addfragments (`1849-1853`). Smoothing sets add2ndhalfarg, rejected without
  add (`695-696`, `2094-2100`). Uppercase GOP/GEXP are consumed only by the
  globalgenaf execution branch (`2595-2596`), whose sole parser selection is
  disabled and exits (`526-532`). Text/textmatrix force a nonbiological extended
  alphabet and model (`735-752`); biological custom matrices remain available.
- **MUSCLE: 7 of those controls implemented; 7 concretely excluded.** Implemented:
  `guidetreeout`, `hmmin`, `hmmout`, `anchor_letter`, `randseed`, `accs`, and
  `randomchaintree`; plus separate `native_log` retention for native `-log`.
  HMM files are exact text, and a genuine native Super5 HMM dump is reimported
  in both align and Super5 tests. The dump precedes CmdLineUpdate and is not
  represented as a complete effective-parameter report. Align has no hmmout
  writer; the requested flag is still passed, never silently removed.
- **MUSCLE exclusions:** linkage readers are in separate upgma5/upgma5_msa
  commands and M3 configuration; MPC alignment hardcodes LINKAGE_Biased
  (`mpcflat.cpp:194`). Substmx/kmerdist/treeiters/gapopen/center are consumed
  by M3/benchmark command paths, not align/Super5's pair-HMM initialization.
  Mega replaces the FASTA roster reader with rich feature input
  (`loadinput.cpp:5-8`) and Super5 explicitly rejects it (`super5.cpp:555-556`).
  Per-row evidence records the actual M3 SetFromCmdLine call sites; shared
  declaration files are not treated as proof of reachability.

### Actual artifacts and native limitations

MAFFT node/distance/score-output controls are also exposed. The adapter retains
all actual attempt files, including hat2/clodist, tree, notused and anchors,
instead of a fixed filename allowlist. Native stdout/stderr stays in engine.log.
MUSCLE native internal logs are separate muscle.native.log artifacts.

**MUSCLE guidetreeout is a diagnostic, not alignment plus tree:** the pinned
MPC writer saves the real guide tree and calls `exit(0)` before alignment
(`mpcflat.cpp:196-203`), including when an ensemble was requested. The typed
field and editor explicitly disclose this. The empty output, exit status and
tree are retained; no second invocation or alternate algorithm is substituted.
Random-chain returns before that writer, so no tree is fabricated in that case.

Synthetic pinned probes observed MAFFT hybridpair crashing in pairlocalalign
and scoreout with L-INS-i crashing in tbfast. Those reachable controls remain
explicit and their genuine failures are tested/retained. They are not described
as successful alignment configurations. Ten-mer distance succeeds on DNA but
is natively rejected for proteins. Noscore refinement succeeds with explicit
threadit=0; allowshift requires an appropriate globalpair/shift penalty.
No wrapper auto-correction or new submission restriction was added.

`test_remaining_native_controls.py` covers the new controls' typed roundtrip,
exact argv, native outcomes, actual artifact bytes, row-map identity and
unchanged degapped source sequence content for produced synthetic alignments.
The final focused core run passed **138 tests, no skips** (remaining controls,
native file settings, settings inventory, engines, and core), with the CPU SIF
and all four native wrapper variables set. The mounted editor/inventory/Project
run passed **15 tests across 4 files**, including every new control's rendering
and exact alias/file/zero/false submission; it uses mocked API services and
printed a jsdom XHR diagnostic from the existing suite. Generated schema
parity and preservation of every original field/default were also checked.
It also verifies the guide-only zero-exit and native crash cases as observations,
not as successful alignments. Native accuracy, exhaustive option interactions,
and deployed browser/API/retry acceptance are separate claims.

### Outside this bounded reconciliation

The auxiliary MAFFT Clustal/PHYLIP formatting, line/name/numeric-header controls
and core/debug output workflows remain unimplemented in the inventory. They
are not relabeled as scientifically excluded. Add/profile/seed, external
search, nonbiological text and structure-extension operations remain outside
approved fixed-family sequence MSA. This reconciliation closes the specifically
listed 48+14 controls, not every upstream declaration or whole-module release
acceptance. Deployment is handled by the release owner.
