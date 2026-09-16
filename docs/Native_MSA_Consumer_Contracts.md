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

## Neurosnap hosted search and required native handoffs

The authoritative [service schema](https://neurosnap.ai/api/service/mmseqs2%20MSA%20Generation),
[generated API example](https://neurosnap.ai/service/mmseqs2%20MSA%20Generation)
and [API tutorial](https://neurosnap.ai/blog/post/66b00dacec3f2aa9b4be703a)
were read on 2026-09-11 without credentials or sequence submission. These public
reads are contract evidence, not live provider acceptance.

- Submit is multipart `POST /api/job/submit/mmseqs2%20MSA%20Generation`,
  authenticated with the controller-owned `X-API-KEY`. The response is a JSON
  job-ID string. `Query Sequence` is a JSON object with `aa` name-to-sequence
  mapping and empty `dna`/`rna` mappings, not a FASTA upload. The existing adapter
  submits one named `query` per independent monomer operation.
- All five exposed scientific controls are sent explicitly: Coverage (default
  35, bounds 10–90), Identity threshold (50, 25–100), Max Sequences (1000000,
  10–1000000), Force Uppercase and Pad Sequences (both false). The last two
  transformations destroy native A3M insertion/column semantics and remain
  explicitly incompatible with these native A3M consumers, not silently reset.
- Poll is `GET /api/job/status/{id}`, returning a JSON string: pending, running,
  failed, completed, deleted or cancelled. Output metadata is
  `GET /api/job/data/{id}` with `out: [[filename, size-label], ...]`; download is
  `GET /api/job/file/{id}/out/{filename}`. The adapter requires one listed native
  monomer A3M and exact query/aligned-row validity, never guesses archive layout.
  Existing signed-output handling allows only its constrained credential-free
  download hop; the public tutorial does not itself specify a CDN identity.
- Safe-read throttling/outage becomes `PendingMSA` with the retained ticket and
  Retry-After delay (seconds or HTTP date), consumed by the ordinary Nextflow
  preparation waiter for both placements. Authentication/content errors remain
  errors. POSTs are never retried: an ambiguous submit stays reconciliation-
  required. `POST /api/job/cancel/{id}` requests cancellation; only a later
  terminal status proves the remote outcome. Stopping local polling does not.
- The public schema permits 1–10 sequences of length 20–25000 and markets pairing
  support, but exposes **no pairing-mode field or paired output/chain mapping**.
  BMS therefore executes explicitly unpaired multichain requests as independent
  monomer jobs, retains ordered chain indices/receipts and reuses repeated-chain
  cache entries. This is not paired generation or ColabFold-compatible pairing.
  A requested paired Neurosnap operation requires authoritative provider output
  and pairing evidence before implementation; marketing text is insufficient.
- Protenix consumes `unpairedMsaPath` without an invented paired file. At pin
  bd54a05, `runner/msa_search.py:35–59` does not re-search when that path exists;
  `msa_featurizer.py:603–631` accepts either role independently. The controller
  binds the once-compiled native invocation before packaging, retaining ordered
  task/chain identities. The worker verifies the sealed manifest and relocates
  paths through `hydrate_prepared_protenix_task`, not search or string replacement.
- Boltz2 and Boltz-CP consume the independent unpaired A3Ms through the same
  `prepare_model_msa` cache boundary and existing native artifact adapters above.
  Supplied alignments, explicit no-MSA modes and the deferred generated-antibody
  cache-miss case remain distinct; no provider fallback is introduced.

Setup is the shared `provider_readiness()` boundary, consumed by
`GET /api/msa/providers`, `MsaProviderReadiness` and
`python scripts/manage_msa_providers.py --require neurosnap_api`. It checks the
protected credential reference's metadata and both cache/controller journal
storage without reading the key or submitting science. `configured` does not
mean authenticated or scientifically accepted.

### Bounded acceptance prerequisites (not authorization)

First qualify offline provider/recovery and relocated native-consumer tests on
one frozen source. A separately approved live check then requires an existing
managed private credential reference, operator-approved public protein sequence
of 20–25000 residues, approved credit/request ceiling and selected native runtime.
Use the ordinary typed Job launch with `msa_provider=neurosnap_api`, all five
explicit provider settings above and the native model's MSA-enable flag; require
successful preview/admission before launch. Reopen the same request to prove
cache replay without a second POST, then consume its prepared inputs with a
relocated worker root through the normal model launch. Record ticket, requested/
effective settings, native A3M hashes, ordered chain identities and actual native
output/receipt. Pairing-required acceptance additionally needs the missing
provider-specific pairing contract, not a fabricated paired fixture. No live
provider, inference, credential change or paid operation was performed here.
