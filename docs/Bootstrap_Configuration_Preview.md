# Clean-install configuration preview (not apply)

The existing launcher now accepts a read-only candidate document:

```sh
./start_ui.sh configure-preview --document /absolute/path/install.json --json
# Direct Python callers must disable bytecode at interpreter startup:
python3 -B scripts/manage_desktop_services.py --json configure-preview --document /absolute/path/install.json
```

The shell uses `python3 -B`. Direct invocation without `-B` cannot guarantee
no interpreter-startup bytecode writes, even though preview disables subsequent
module bytecode. No profile, export, directory, service, download, ingress or
registration is written by preview. Syntax-valid previews exit 0 with
`valid: true`, **`ready: false`, `apply_available: false`**. Invalid documents or
configuration resolution failures exit 2 with JSON blockers. Argparse usage
errors remain ordinary stderr/exit 2. `apply_available: false` describes this
read-only preview action, not the separate [transactional configure command](Transactional_Configuration.md).

## Closed input v1

```json
{
  "schema_version": "bms.install.v1",
  "profile": {},
  "ingress": {"mode": "local-only"}
}
```

All three keys are required. The structural schema is
`config/schemas/install-document.v1.schema.json`; Python validation additionally
checks the existing runtime port and local capacity contracts and path safety.
Duplicate JSON keys, unknown top-level/profile/feature/ingress keys, coercible
strings, nulls, booleans as integers, non-finite numbers and legacy port aliases
are rejected **before** permissive legacy normalization.

This deliberately bounded version supports:

- Host storage paths: `data_root`, `results_dir`, `inputs_dir`, `db_path`,
  `container_dir`, `dev_data_root`, `dev_results_dir`, `weights_root`,
  `colabfold_db`, `msa_cache_dir`, `sabdab_cache_dir`, `work_dir`,
  `analysis_cache_dir`.
- Integer ports: `api_host_port` (fixed at 18000), `dev_api_host_port`,
  `dev_web_host_port`, `web_host_port`. Existing governed range, reserved-port
  and collision rules apply; old port aliases are not migrated here.
- Boolean `core_runtime_mode`; boolean `features.bioxp` and
  `features.molecular_dynamics`; integer `local_cpu_threads` and finite positive
  numeric `local_memory_gib`, bounded by the existing host capacity API.

Other legacy settings, including project roots, image selectors, CORS and
workflow URLs, are intentionally unsupported in this v1 input. They are not
silently dropped. This is not a second model/dependency registry.

Paths must be absolute, trimmed and free of control characters and `..`.
Missing directories are allowed without creation. Non-directory ancestors,
dangling/looping symlinks, non-file database destinations, source-overlapping
state/export paths, and overlapping production/development data roots fail.
Resolution is an observation, not a permission, free-space, mount-layout,
concurrency or future filesystem-safety guarantee.

For an empty home directory, production state defaults to
`${XDG_STATE_HOME:-$HOME/.local/state}/biomodstack`; development state defaults
to the sibling `biomodstack-dev`. HOME must be explicitly absolute; unset,
empty or relative HOME fails instead of choosing the checkout. Nonempty XDG
state/config overrides must be absolute. Runtime environment overrides are
ignored for the candidate, matching the existing export resolver's explicit
`environ={}` policy. Derived paths use `resolve_runtime_paths`, not duplicated
runtime mapping logic. Export destinations use the existing profile API.

## Ingress intent, not enforcement

`{"mode":"local-only"}` explicitly requests no Tailnet setup; it forbids a
target. Optional Tailnet intent is
`{"mode":"tailnet","target":"production"}` or target `development`.
Neither choice changes listener binding, starts/stops Serve, checks auth,
revokes existing ingress or alters legacy lifecycle behavior. Preview marks
intent `applied: false`, `qualified: false`. Future execution must preserve the
existing Tailnet ownership/authentication/provenance/rollback safeguards.

## Legacy compatibility and apply gaps

This install document is **not** the persisted legacy `install_profile.json`
format. Legacy normalization and fallback heuristics remain available to legacy
installations; incomplete managed generations fail closed and legacy writers
cannot modify them.
Preview never reads, merges or migrates an existing profile (even malformed
profiles); it reports its presence and previews an independent candidate.
External defaults apply only to this explicitly versioned clean-install input,
not to existing installations. No migration or replacement is authorized.

First-install configuration and recovery are implemented by the separate
[transactional configure command](Transactional_Configuration.md). It rejects
existing installations rather than dropping deployment-only settings/secrets.
The transaction uses immutable staged files, a durable journal, no-overwrite
destination links (which may span filesystems), and one activation pointer; it
does not claim multi-file atomic replacement. Supported managed readers fail
closed while incomplete. Legacy migration, acquisition, scientific admission,
ingress enforcement and full clean-machine readiness remain separate blockers.
