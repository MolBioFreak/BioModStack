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

## Exact remaining scope / qualification gaps

MAFFT parser controls not yet exposed/qualified (no blanket exclusion):

`--10mermultipair`, `--10merpair`, `--6mermultipair`, `--6merpair`, `--GEXP`, `--GOP`, `--adhoc0`, `--adhoc1`, `--adhoc2`, `--alga`, `--allowshift`, `--anchors`, `--bestfirst`, `--bunkatsunashi`, `--focus`, `--gep`, `--gexp`, `--gop`, `--hybridpair`, `--kappa`, `--large`, `--leavegappyregion`, `--lhlimit`, `--localgenafpair`, `--longshortpair`, `--memsavetree`, `--memsavetreex`, `--minimumweight`, `--multipair`, `--noscore`, `--oldgenafpair`, `--oneiteration`, `--pileup`, `--randomchain`, `--shiftpenalty`, `--shortlongpair`, `--similaritylevel`, `--simplehillclimbing`, `--skipiterate`, `--smoothing`, `--sp`, `--sparsepickup`, `--stepadd`, `--terminalmargin`, `--text`, `--textmatrix`, `--unalignlevel`, `--youngestlinkage`.

MAFFT auxiliary format/artifact controls remain missing: Clustal/PHYLIP output,
distance/node/score output, line/name/numeric-header formatting and core/debug
artifacts; see each native spelling in the inventory. Add/profile/seed,
external search, arbitrary text alignment and structure-extension operations
are not implemented. The inventory intentionally retains uncertain expert
items as gaps rather than claiming user-approved scope changes.

MUSCLE selected-engine/shared-source controls still needing implementation or
specific reachability qualification:

`-linkage`, `-guidetreeout`, `-hmmin`, `-hmmout`, `-anchor_letter`, `-substmx`, `-kmerdist`, `-randseed`, `-treeiters`, `-gapopen`, `-center`, `-accs`, `-randomchaintree`, `-mega`.

The other MUSCLE declaration-only or distinct-command rows retain their exact
consumer files and explicit dispositions. In particular they are not counted
as proven irrelevant without a transitive scope audit. Full native interaction
coverage, full browser/API persistence/retry journeys, all native-effective
algorithm observations, and benchmark accuracy remain open. This document
supersedes the older statements that FAMSA tree import, MAFFT tree/matrix and
TWILIGHT matrix were wholly absent; it does not declare whole-module completion.
