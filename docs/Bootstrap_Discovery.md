# Read-only bootstrap discovery and planning (v1)

The existing human/agent entrypoint now supports a bounded pre-service slice:

```bash
./start_ui.sh discover --json
./start_ui.sh plan --runtime container --model frustrampnn --json
# Same interface without the shell wrapper:
python3 scripts/manage_desktop_services.py plan --model protenix --json
```

Omit `--json` for a human-readable rendering of the same report. Repeat `--model`
for multiple selections. No selection means controller prerequisite observations,
not an implicit request to install every model. Model dependency closure comes
from `model_registry.model_runtime_dependencies`; if its locked Python dependencies
are not installed, the report names `dependency_authority_unavailable` rather than
inventing a fallback registry. Existing runtime-profile environment overrides and
configuration are used; these actions do not save or export a profile.

## Contract

- `schema_version`: `bms.bootstrap.v1`.
- `action`: `discover` or `plan`.
- `status`: `blocked`; `ready`: false; `read_only`: true in this slice.
- `observations`: host, configured local CPU/RAM budgets, profile location,
  storage roots/free bytes/device IDs, executable presence, and explicit unchecked
  GPU/service-privilege state. Storage on the same device shares free capacity;
  do not sum it. Write-access hints are not write probes or privilege approval.
- `dependencies`: selected reviewed image/weight references, **not verified bytes**.
- `blockers`: machine-readable `code` and human `message`.
- `effects`: all false. `plan` additionally reports non-executable future steps.

Exit **3** means the report was produced but installation readiness is blocked.
Successful observation is never exit-zero installation success. Both text and
JSON use this exit rule. Invalid CLI syntax/options use argparse exit 2 and stderr,
not the report contract. Legacy lifecycle/status output and exit behavior are
unchanged; `status` is not an installation acceptance gate.

Executable lookup does not run versions, Docker, systemctl, GPU utilities, network
requests, scientific validators, or notifications. No directories, bytecode,
profile exports, service units, journals, downloads, or registrations are created
by these actions. `--notify` and `--target` are rejected for bootstrap.

The profile authority currently permits legacy normalization and heuristic roots.
Discovery reports invalid/unrecognized profile fields and checkout-backed storage
as blockers; it does not migrate configuration or change those existing defaults.

## Deliberate limitations

This is **not a completed installer**, an executable acquisition plan, or clean-VM
scientific acceptance. Approved pinned acquisition, separate licensed-weight
acceptance/acquisition, staging/expansion peak disk sizes, transactional configure,
qualification/registration, installation readiness verification, and durable
resume are still missing from this entrypoint. Consequently required disk bytes
and sufficiency are null, not zero or true; discovery always remains blocked.
Existing FrustraMPNN/Protenix/MD scientific validators and launch admission remain
unchanged and authoritative. No arbitrary observed bytes may be registered.
Tailnet is not requested or inspected here; optional ingress configuration belongs
to a later reviewed slice. No services are restarted and no jobs are changed.

Focused tests (from `platform/api`):

```bash
uv run --frozen --group dev python -m pytest tests/test_bootstrap_cli.py tests/test_manage_desktop_services_cli.py -vv -s
```

These include real shell and Python CLI invocations in empty HOME/XDG directories,
read-only snapshots, JSON/nonzero semantics, registry reuse, malformed profiles,
missing tools, disk exhaustion/access hints, and unknown model closure. They do
not constitute clean-machine install acceptance.
