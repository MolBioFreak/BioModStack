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

## MSA hub

The top bar has an "MSA" button in place of the old "MSA Server" button. It opens a hub with two parts.

Remote providers is always shown. It lists every provider in `PROVIDER_DESCRIPTORS` (`platform/api/services/msa_provider_credentials.py`) with its readiness message. A provider that needs a key gets a key field with Save and Clear. The field is never pre-filled; the hub shows "Key saved" or "Set by environment" instead. Adding a provider is one new descriptor entry plus its transport in `biomodstack_msa_api.py`.

Local MSA server is shown only when the installation turns on the `local_msa` feature. It holds the legacy local MMseqs2 server settings and start/stop controls unchanged.

### Saved keys

A key saved from the hub is written to `<BMS config dir>/msa-providers/<provider id>.key`, mode 0600 inside a 0700 directory, written atomically. Lookup order for a provider that needs a key:

1. the file named by the provider's environment variable (`BMS_NEUROSNAP_API_KEY_FILE` for Neurosnap);
2. the key saved from the hub.

An environment setting always wins, even when the file it names is unusable, so a deployment-owned setting is never silently replaced. A key is never returned by any route, logged, or copied into job parameters or provenance.

### Routes

- `GET /api/msa/providers` returns the readiness document plus `items`, one row per provider: `id`, `label`, `needs_key`, `configured`, `source` (`environment`, `saved` or null), `ready`, `message`.
- `PUT /api/msa/providers/{id}/credential` with body `{"api_key": "..."}` saves or replaces a key.
- `DELETE /api/msa/providers/{id}/credential` clears a saved key.

Save and clear are accepted from the local machine only, like the Tools screen's install routes. An unknown provider id answers 404 and an empty key 422.

### ColabFold

ColabFold needs no key. Public searches are sent from one computer per installation, and that computer has to be declared. In the hub, the ColabFold row has a "Use this computer for searches" button; the command line equivalent is `python scripts/manage_msa_providers.py --enable-colabfold`. Either writes `<BMS config dir>/msa-controller.json` for the current machine. Nothing is submitted by enabling it. "Turn off for this computer" or `--disable-colabfold` removes the file.

The declaration is tied to `/etc/machine-id`. After moving an installation to another computer or reinstalling its operating system, ColabFold shows as off; press the button again and the existing file is rebound to the new machine, keeping its state directory.

A deployment that wants the file somewhere else sets `BMS_MSA_CONTROLLER_CONFIG` to an absolute path; the hub and the command then act on that file.

A job whose alignments are already in the provider cache replays them whether or not searches are enabled. A job that needs a fresh ColabFold search is refused before queueing while they are off.

### Local MSA switch

`local_msa` is an installation feature, off by default. Turn it on with `"features": {"local_msa": true}` in the install profile or `BMS_FEATURE_LOCAL_MSA=1`. When it is off the local server is not started, `POST /api/msa/server/start` answers that local MSA is not enabled for this installation, and the hub shows no local controls. The local server code and `scripts/lib/local_msa` remain in the repository for installations that run their own search.
