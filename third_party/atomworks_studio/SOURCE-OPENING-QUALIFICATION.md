# Selected source → private Studio qualification

Scope: source-opening closeout on base `d6aed7f`, branch
`task/atomworks-capture-closeout-20261028`. No deployment, live data, model
inference, scientific defaults or parser pins changed.

## Receiving contract

- The shared editor callback remains `open(slot, source, knownReceipt?)`.
  A display/native receipt is **not** reused as a Studio import representation.
  Opening explicitly reacquires `output_format: cif` through the existing
  `/api/files/materialize-structure` source owner and verifies `path/sha256`.
  The returned `source_structure` is original custody, not a shortcut to the
  derivative. Neither `native_path`, `decoded_path` nor `selected_path` is
  universally the final consumed CIF.
- The new representation is additive: native/PDB materialization and ordinary
  source-free/manual Generate remain unchanged. PDB conversion uses the already
  locked Gemmi 0.7.5 on original bytes, **not** the PDBIO MODEL slice that discards
  header declarations. The original path/hash and selected native PDB path/hash
  stay distinct from `derived.cif` and its hash. CIF/BCIF use existing column-level
  selection, preserving categories and model-local indices.
- Actual source MODEL IDs (7 and 42 in qualification) are selected before import.
  No-MODEL PDB remains source MODEL 0 under the existing Bio.PDB contract, despite
  Gemmi's implicit MODEL 1 convention. Unselected ensembles are not silently
  reduced by opening. The existing editor-only MODEL prompt is unchanged.
- `_condition_*` vector/pair loops now receive the same model-row filtering as
  `_annotation_*` loops. Shared loops without `model_num` retain their local
  indices. Legal empty CIF strings retain empty-text semantics rather than
  crashing Bio.PDB's writer or becoming unknown/inapplicable markers.
- Studio receives genuine text CIF with `minimal`, `assembly=asym`, `model=1`
  (**ordinal of the already selected derivative**), saved annotations enabled,
  and its existing `altloc=first` policy explicit. No assembly-1 transform is
  silently applied. Both alternate locations and assembly operators remain in
  materialized custody. The current source selector has no assembly/altloc
  selection fields; this change does not invent such selectors. The first-altloc
  import policy is displayed and recorded, not claimed to preserve all alternate
  atoms inside Studio's single-conformer document.
- Frozen export adoption remains exactly one response and the existing immutable
  upload. Original source selector, original producer identity, original retained
  path/hash, imported CIF path/hash, import options, source MODEL ancestry and
  exported MODEL readback are separate. Studio's MODEL 1 export never overwrites
  the original MODEL 42 ancestry. No session ID becomes durable authority.
- Callback attachment, accepted adopted bindings, monotonic dirty/source fencing,
  replaced-source recovery, child serialization and manual launch are unchanged.

## Executed evidence

Evidence root:
`/home/dalab/.hermes/profiles/fresh/cache/scratch/atomworks-capture-closeout/`

| Evidence | Executed result |
|---|---|
| `http.xml` / `tests/test_studio_source_materialization_http.py` | 5 passed, no skipped/error/failure cases |
| `api.xml` / existing source materialization, native identity and checked Complexa derivative suites | 16 passed, no skipped/error/failure cases |
| `mounted.xml` / source-opening, Studio receiving, capture receiving and preview capture suites | 34 passed, no skipped/error/failure cases |
| `typecheck.log` / frontend `tsc -p tsconfig.app.json` | exit 0 |

The HTTP suite starts the real qualified isolated Studio interpreter at
`scratch/atomworks-patched-host/supported-venv/bin/python` behind the actual
private BMS proxy. Its listeners, credentials, sessions, file routes and stores
are fixture-owned; no managed services or live database are used.

It proves synthetic PDB MODEL 7/42 → selected CIF → private Studio import → native
hotspot edit → one frozen export → immutable publish twice → exact download →
session deletion → cold materialization → new private Studio session with equal
saved native annotation. It checks SEQRES, insertion code, alternate-location
retention before parsing, no assembly translation, MODEL 1 output and exact
coordinates of the selected source. A separate source-free segment fixture
proves selected MODEL 42 saved internal-coordinate targets, coordinate group and
sequence conditions survive actual native parsing/export. CIF/BCIF fixtures prove
all-category preservation, exact model-local annotation rows and no accidental
full-source decoded import.

Per-MODEL `http/test_selected_pdb_cif_private_*/source-custody-evidence.json`
contains actual requests, receipts, import options, immutable frozen hash/size,
coordinates, cold hashes and native annotation readback. Ignore pytest's
`*_current` symlink when enumerating runs.

Deterministic input/converted hashes from the executed synthetic fixture:

- Original PDB: `1782cc9793ad28ca7bcf511eacdffadac750f2235b57fbf3cabd28adf49f80ac`
- MODEL 7 CIF: `10d5a3ae909caae360803bfba7b96702149c9f7a6d38e7ef03c843d3b8100a84`
- MODEL 42 CIF: `214aee9ea75445041bc33da1c67a4d415bb92f5db2419cf3498aeab69fd72d95`

Frozen exports include native serialization timestamps; their exact per-run
hashes are recorded, not normalized or assumed reproducible across new exports.

The new mounted tests separately prove actual callback compatibility, ignoring
an incompatible known PDB receipt, exact upload name/options/bytes, distinct
ancestry and derivative MODEL, A→B→A during materialization/import/capture, and
unselected-ensemble behavior. Existing tests retain dirty capture, source
replacement, Save/Template/Project callback, Preview and manual Generate checks.
They use a transport double, not WebGL or real Studio.

## Boundaries and known limits

This is real private HTTP/native parser acceptance plus separate mounted owner
acceptance, **not** a populated parent-browser/WebGL or Project/Template database
end-to-end run. No new browser acceptance is claimed here.

PDB conversion is not a universal chemistry-validation claim. Exact original
PDB bytes and the existing `declared_chemistry.pdb_records` ledger remain retained.
Gemmi's structural CIF conversion handles its supported header declarations;
in particular its raw `CONECT` map is not automatically emitted as `_struct_conn`.
Arbitrary PDB CONECT-only chemistry therefore remains unqualified for Studio
interpretation; no refusal gate, inferred bond rewrite or parser repin was added.
The qualified atom/SEQRES/assembly/altloc and saved-CIF-annotation cases must not
be expanded into a blanket all-PDB-chemistry success claim.

Initial qualification caught and repaired Bio.PDB's empty-string writer crash.
A mounted test initially expected unescaped URL slashes; its expectation was
corrected to the actual existing encoded download URL, without relaxing identity
or digest assertions. Final evidence above supersedes those intermediate runs.
