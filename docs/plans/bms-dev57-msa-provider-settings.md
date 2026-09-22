# BMS-DEV-57: scientific-only MSA provider settings

Base: `test@6438833c044a63ae67120fb023d81f7c4e2c9a56`.
Scope: fix the shared launcher-to-provider scientific-settings projection and
make closed-schema refusals actionable. No provider, worker or scientific run is
part of the offline verification. No inference or transport settings are changed.

## Reproduction and correction

The reported launcher payload contains five ColabFold scientific aliases and
three transport keys: `colabfold_api_host`, `colabfold_api_min_interval`, and
`colabfold_api_poll_interval`. The baseline projection admits all eight, retaining
the three transport names unchanged. `validate_settings('colabfold_api', ...)`
then refuses them. The Neurosnap projection and validation succeed on the same
mixed payload.

`provider_settings()` now selects exact membership in the existing five-alias
map, not every key beginning with `colabfold_`. The existing Neurosnap field set,
provider-specific exclusion, defaults and typed argv conversion are unchanged.
ColabFold's scientific values (including false booleans and nondefault pairing)
are retained; unsupported scientific values are still rejected by the validator.
Other ColabFold transport, inference, path, credential and future undeclared keys
do not enter provider settings, HTTP scientific fields or cache identity.

The closed schema remains closed. Neither provider's defaults or accepted
scientific keys are expanded. Direct invalid settings still raise `MSAAPIError`,
now naming the offending keys in sorted order without their values. For safe UI
and log presentation, only identifier-shaped names up to 80 characters are
shown; malformed/oversized labels are redacted. At most 16 labels are shown,
with an explicit remaining count. Normal field names are not redacted.

Example direct refusal (passing transport keys directly to the validator):

```text
unsupported scientific setting keys: colabfold_api_host, colabfold_api_min_interval, colabfold_api_poll_interval
```

## Verification

`test_msa_provider_projection.py` exercises both providers with mixed launcher
parameters and argv-style values, the exact reported payload, every ColabFold
scientific mapping, invalid scientific booleans, undeclared prefixed keys,
provider request-identity invariance, and actual preparation forwarding with
only the provider/cache IO boundary doubled. It checks named refusals, omission
of values, bounded diagnostics and input immutability. No provider call is made.

From `platform/api`, use the repository's locked environment and default network
isolation, retaining JUnit case identities and failure/skip counts. The bundle
and prepared-MSA metadata suites require an approved real Nextflow executable
(`BMS_NEXTFLOW_BIN`), installed before network isolation, not a launcher stub:

```bash
uv run --frozen --group dev python -m pytest -q --randomly-seed=57 \
  tests/test_msa_provider_projection.py \
  tests/test_msa_provider_setup.py \
  tests/test_msa_provider_controls.py \
  tests/test_model_msa_handoff.py \
  tests/test_fold_cp_input_msa_metadata.py \
  tests/test_msa_bundle_integration.py \
  tests/test_msa_controller_handoff.py \
  tests/test_msa_policy_saved_admission.py \
  tests/test_ngs_molbio_runtime_record_builder.py \
  --junitxml=/tmp/bms-dev57-api.xml
```

Run the root-owned offline MSA client suite separately using the same locked
interpreter and a route-free network namespace plus pytest-socket. Do not collect
it alongside API tests: the API conftest globally blocks fork during collection,
but its per-item restore hook does not apply to tests outside its directory.
The root suite includes a legitimate offline process-lock regression. Do not
set a live-hardware opt-in to work around this test-scope interaction.

```bash
# From the repository root after uv sync --frozen --group dev in platform/api;
# run inside the same route-free namespace used for the API selection.
platform/api/.venv/bin/python -m pytest -q --randomly-seed=57 \
  --disable-socket --allow-unix-socket tests/test_msa_api_client.py \
  --junitxml=/tmp/bms-dev57-client.xml
```

The first offline run also exposed an existing assertion mismatch in
`test_msa_policy_saved_admission::test_api_mutagenesis_is_actually_blocked_at_admission`:
without configured provider setup it receives the setup-required 422 before the
mutagenesis-specific message it expects. It fails identically on the unmodified
parent. Preserve this failure in baseline/candidate evidence; do not skip it,
change production admission ordering, or claim that selection is entirely green.
Require every added regression and the standalone client suite to pass, no new
API failures or skips, and all baseline case identities to remain present.

Run the two-provider reproduction on the parent and bound candidate; require
ColabFold to validate after projection, Neurosnap to remain identical, and direct
invalid settings to retain refusal with named keys. Generate the runtime source
record from the final record-free committed archive and raw commit object, then
commit the record separately and require the updater validator to accept that
exact final revision. Do not publish a record-free tip to canonical `test`.

BMS-DEV-53/54/55 fixes are preserved, not reopened. BMS-DEV-56 remains open and
untouched: absent status, idle GPUs, or an absent supervisor do not authorize
attempt retirement or ownership release. This patch is not a live launch,
provider-acceptance or cancellation-recovery claim. A subsequent supervised
launcher attempt must confirm the reported pre-job refusal is gone.
