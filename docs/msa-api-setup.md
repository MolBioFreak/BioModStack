# MSA API setup

BMS prepares MSA inputs through one shared provider client and persistent cache, then passes native alignment files to inference. Model adapters do not own provider credentials. Public ColabFold submissions are serialized on BMS rather than fanned out across workers.

## Deployment configuration

- `BMS_MSA_CACHE`: existing managed MSA cache root. API-provider entries are stored under its `provider_api` child, keyed by provider, query identity and effective provider settings. Use the same persistent root for controller processes. Changing a model/job scratch directory must not create another provider cache.
- `BMS_NEUROSNAP_API_KEY_FILE`: absolute path to a private, nonempty, readable regular credential file containing the Neurosnap API key. Configure it through the normal protected managed-service environment. Keep permissions private (no group/other access). Never put the key in job parameters, source, logs, a worker bundle or browser settings. Configure credentials privately; do not paste them into chat.
- ColabFold public API does not require a key. No Protenix inference package, local MMseqs installation, or local search database is required merely to call either provider API.

Inspect configuration without reading credentials or submitting a request:

```sh
# Use the managed API Python environment and normal installation configuration.
python scripts/manage_msa_providers.py
python scripts/manage_msa_providers.py --require colabfold_api
python scripts/manage_msa_providers.py --require neurosnap_api
```

The same non-submitting data is available to browser and agents at `GET /api/msa/providers`. Configured, authenticated and live-accepted are different states: this endpoint checks configuration only. A missing Neurosnap key does not disable ColabFold or explicitly cache-only/supplied-input operation.

## Scientific selection

`msa_provider` selects `colabfold_api` or `neurosnap_api`. `auto` continues to resolve to ColabFold; a saved explicit provider is never silently changed. A model backend selector, where present, must agree. Local MSA search stays disabled; supported no-MSA modes and explicitly supplied alignments remain separate choices.

Neurosnap controls use typed `msa_neurosnap_*` fields: coverage percentage, identity threshold percentage, maximum sequences, force-uppercase and pad-sequences. These represent the actual provider's controls, not renamed local MMseqs tuning parameters. Model input compatibility may reject a transformation that changes A3M insertion/column semantics. Unsupported settings must be rejected before submission, not silently reset.

## Cache and transfer

Provider requests use content-verified cache entries. Cache hits must validate artifact hashes and query identities; changing provider or effective provider settings must not reuse an unrelated request. Completed native provider outputs are retained with provenance. An explicitly cache-only miss is an error, never permission to submit a search or disable MSA.

Prepared MSA inputs are transferred as mandatory workflow inputs independently of manual/automatic final-result retrieval policy. Workers consume the prepared model-native alignment paths; they do not receive Neurosnap credentials or independently query the public ColabFold service.

## Verification boundaries

Offline HTTP fixtures and relocated input tests prove client/control/packaging behavior, not a real provider result or scientific inference. Live checks must record the source revision, public/approved input, actual provider ticket, validated artifact digest and observed model consumption. Never label generated test fixtures as a live result. Ambiguous submission cannot be retried as a new paid/public job merely to make a test pass.

Provider references: https://github.com/sokrypton/ColabFold ; https://neurosnap.ai/service/mmseqs2%20MSA%20Generation ; https://neurosnap.ai/api/service/mmseqs2%20MSA%20Generation ; https://neurosnap.ai/blog/post/66b00dacec3f2aa9b4be703a .
