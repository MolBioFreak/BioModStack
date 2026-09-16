# Read-only bootstrap discovery and planning (v1)

For executable locked prerequisite bootstrap and managed Development startup,
see [Non-production installation](Nonproduction_Installation.md). The discovery
operations below remain read-only and do not install those prerequisites.

The existing human/agent entrypoint now supports a bounded pre-service slice:

```bash
./start_ui.sh discover --json
./start_ui.sh plan --runtime container --model frustrampnn --json
# Same interface without the shell wrapper:
python3 -B scripts/manage_desktop_services.py plan --model protenix --json
# Options before the action are supported by the Python CLI:
python3 -B scripts/manage_desktop_services.py --json --runtime dev discover
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
- `effects`: all false for bootstrap application operations (`effects_scope`),
  not a claim about effects before application entry. `interpreter_startup`
  reports whether Python started with bytecode disabled. `plan` additionally
  reports non-executable future steps.

Exit **3** means the report was produced but installation readiness is blocked.
Successful observation is never exit-zero installation success. Both text and
JSON use this exit rule. Invalid CLI syntax/options use argparse exit 2 and stderr,
not the report contract. Legacy lifecycle/status output and exit behavior are
unchanged; `status` is not an installation acceptance gate.

Executable lookup does not run versions, Docker, systemctl, GPU utilities, network
requests, scientific validators, or notifications. Bootstrap application operations
do not create directories, profile exports, service units, journals, downloads, or
registrations. `--notify` and `--target` are rejected for bootstrap.

For the supported no-bytecode invocation, the shell starts Python with **`-B`**;
direct Python callers must use **`python3 -B`** (or set
`PYTHONDONTWRITEBYTECODE=1` before interpreter startup). This also covers a fresh
`PYTHONPYCACHEPREFIX` outside HOME/XDG. The manager disables bytecode at its first
executable boundary before importing argparse or project modules, independent of
action/option ordering. Without a startup flag, Python may already have written
stdlib/startup bytecode before that line; we cannot undo or attest those effects.
User-controlled `sitecustomize`, `.pth` hooks, import hooks or wrapper programs
can execute before/outside bootstrap and are not sandboxed by `-B`. Use a trusted
Python environment; this is not a process-wide filesystem sandbox. Embedded
callers likewise own their interpreter/import startup boundary.

The profile authority currently permits legacy normalization and heuristic roots.
Discovery opts into raw validation in the same profile authority before permissive
normalization: known nested features, nonempty string paths/configuration, strict
integer/decimal-string ports (no bool/float coercion), and finite local budgets.
Supported legacy feature spellings and port migrations remain intact. Invalid
configuration and path-resolution failures are JSON blockers, including symlink
loops and numeric overflow. Existing legacy normalization APIs are not tightened.
Checkout-backed storage is blocked; configuration is not migrated.

Storage observations follow the managed lane authority: Development uses its own
inputs/results/database/work and mutable caches; container mode inspects production
host destinations, including separately configured results/database/cache paths.
Images, weights and ColabFold reference data remain shared. Database free space is
observed at its parent directory, not by opening the database. Unused Development
storage is not a production write-access prerequisite. These are configured
prospective destinations, not proof of a running service or its installed revision.

Dependency references alone do not establish weight license requirements. Selected
models report license applicability as **unknown**, not an assertion that every
model needs licensed weights. No license metadata or acceptance is invented; no
selection adds no model-license observation.

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
uv run --frozen --group dev python -m pytest tests/test_bootstrap_cli.py tests/test_bootstrap_review_regressions.py tests/test_manage_desktop_services_cli.py -vv -s -p no:cacheprovider
```

These include real shell and Python CLI invocations in empty HOME/XDG directories,
read-only snapshots of archived source and fresh PYTHONPYCACHEPREFIX, both Python
argument orderings/startup contracts, JSON/nonzero semantics, registry reuse, malformed profiles,
missing tools, disk exhaustion/access hints, and unknown model closure. They do
not constitute clean-machine install acceptance.
