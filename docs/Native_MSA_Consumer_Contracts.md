# Native cached MSA consumer contracts

This is a packaging contract, not a claim of live model/GPU acceptance. The
controller consumes shared provider artifacts; no model-specific HTTP client is
introduced. Fixtures in `test_model_msa_handoff.py` are synthetic transport data.

## Boltz2 and Boltz-CP

- Boltz2 runtime source is pinned by `apptainer/boltz2.def` to
  [boltz-community 7ebf1be](https://github.com/Novel-Therapeutics/boltz-community/blob/7ebf1be087d4d61a02234c878402838bf3712d8b/src/boltz/data/parse/csv.py).
- CP's definition clones its upstream without a revision pin. The reviewed
  [CP parser 15f9775](https://github.com/NVIDIA-BioNeMo/boltz-cp/blob/15f9775bc2280cd8a9338feb4d29511c27beaf21/src/boltz/data/parse/csv.py)
  must be requalified if the runtime changes; deployment is not verified here.
- Both accept YAML `protein.msa` pointing to `.a3m` **or `.csv`**. CSV has exactly
  `key,sequence` columns. The parser stores key in an int32 field named
  `taxonomy`; the featurizer uses equality of keys, not a taxonomy lookup.
- Controller paired artifacts become one CSV per chain: query first with key
  `-1`, paired row N with key N (one-based, identical across chains), then all
  unpaired hits with key `-1`. These are **opaque provider row-group IDs**, not
  fabricated organism IDs. No `TaxID` or UniRef header is manufactured.
- Paired depth and coverage must agree for every requested chain. Lowercase
  insertions and gaps remain in native sequence strings. Chain roster/order,
  component metadata and YAML multi-copy `id` lists are unchanged. The role
  A3Ms and a row-group semantics manifest remain beside the generated CSVs.
- Boltz2's pinned parser drops duplicate **unpaired** sequences only; duplicate
  paired rows retain their keys, including paired query matches.
- CP unconditionally skips duplicate `sequence.replace('-', '').upper()`
  identities, including paired query matches. Such inputs are rejected with
  this exact parser limitation; otherwise paired CSV is supported. Altering
  residues, assigning unrelated taxonomy IDs, or silently losing groups is
  forbidden. Both parsers' normal unpaired deduplication and inference-time MSA
  depth limits remain native behavior, not an adapter promise of retained
  inference depth.
- Unpaired-only provider results remain A3M byte-for-byte after decompression.
  CP relocation validates and preserves CSV suffixes instead of rewriting CSV
  bytes into `.a3m` files. Supplied native CSV bypasses provider preparation.

Tests execute the two checked-in, source-attributed native `parse_csv` functions
verbatim with NumPy/pandas and native dtype widths; the MSA container and token
alphabet are lightweight scaffolding, not GPU model inference.

## RF3: real native format, separate BMS launch limitation

[Foundry b02eed6 format tests](https://github.com/RosettaCommons/foundry/blob/b02eed6a6bdf8f44d14a80cc36e3da13c9f2291c/models/rf3/tests/test_msa_format.py)
prove that RF3 consumes per-component JSON `msa_path` A3M, or CIF
`_msa_paths_by_chain_id`. AtomWorks `parse_a3m` extracts header `TaxID=<n>`;
missing taxonomy is empty, query taxonomy is `query`. `PairAndMergePolymerMSAs`
pairs by those IDs. RF3 is **not inherently unable to consume paired MSAs**.
Its [integration contract](https://github.com/RosettaCommons/foundry/blob/b02eed6a6bdf8f44d14a80cc36e3da13c9f2291c/models/rf3/tests/integration/test_msa_fold.py)
includes a shared-A3M homodimer and per-chain paired heteromer.

The present BMS standalone `structure_prediction` method allowlist excludes
`rf3`. This controller therefore fails before searching, rather than claiming
that packaging alone enables launch. Ordinary ColabFold paired-row indices are
not RF3 taxonomy; there is no documented separate paired file or opaque-key CSV
consumer in this interface. Under the no-invented-taxonomy requirement, raw
paired rows without genuine matching TaxIDs cannot be converted to RF3 paired
inputs losslessly. Genuine existing TaxID-bearing A3Ms are native-supported.
Completing RF3 requires its BMS launch/input compiler plus a provenance-aware
TaxID contract, not generic A3M concatenation.

## Protenix complex/default pairing: do not treat raw paired A3M as sufficient

The BMS runtime pin is [bd54a05](https://github.com/bytedance/Protenix/tree/bd54a05d047b8925a241056f36d619700604068a)
(`apptainer/protenix.def`). Native per-`proteinChain` inputs are
`pairedMsaPath` and `unpairedMsaPath` (legacy `pairing.a3m`/`non_pairing.a3m`
directories are converted by `runner/msa_search.py`). Copy `count` and sequence
ordering unchanged; role artifacts alone do not encode copy count.

At that pin, `protenix/data/msa/msa_featurizer.py` sets `need_pairing` when there
are **more than one distinct protein sequences**, and then calls
`MSAPairingEngine.pair_chains_by_species`. Homomer copies do not require this
heteromer pairing step. `msa_utils.py:get_species_ids` extracts species from
UniProt headers or regex `^UniRef100_[^_]+_([^_/]+)`, **not arbitrary FASTA row
labels or bare `TaxID=`**. Unrecognized paired headers get empty species IDs.

Native `protenix/web_service/colab_request_utils.py` lines 314–336 rewrites
ColabFold paired headers with a row-index suffix before writing `pairing.a3m`.
Thus preserving raw shared-client paired bytes into `pairedMsaPath` is not proof
of correct complex pairing. The integrated controller adapter applies that native
row-group/header convention and records `protenix-bd54a05-native-row-group-headers-v1`
as the conversion identity; cached provider bytes remain immutable. A regression
checks matching row-group extraction and unchanged alignment rows. This is native
input-contract proof, not live complex inference acceptance.
