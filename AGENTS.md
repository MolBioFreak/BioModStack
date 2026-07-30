# BioModStack Service Guide

## Purpose

BioModStack is operated as a managed service. Keep the repository limited to code, configuration, schemas, tests, reproducible runtime definitions, and approved release assets. Do not commit generated output, caches, credentials, local databases, logs, temporary scripts, historical copies, or unused/deprecated implementation code.

## Branch model

- `test` is the sole development and integration branch.
- `main` is the production/default branch.
- Develop, test, and integrate on `test`; create a reviewed `test → main` promotion only when the release owner authorizes it.
- Before any push, fetch the remote branch, rebase or otherwise reconcile with its current tip, run focused validation, and verify the remote SHA after the push.
- Never force-push, reset shared branches, or bulk-merge historical experimental branches. Port only current, tested behavior.

## Standard service operation

1. Install dependencies from the repository’s locked manifests. Do not substitute package managers or rewrite lockfiles unless dependency work is intentional.
2. Configure service settings through the supported environment/config files; never hard-code host-specific paths, addresses, credentials, or port numbers into source.
3. Start the managed API, frontend, worker/workflow services, and optional desktop shell through the repository’s supported service/launcher commands.
4. Confirm service ownership using the actual process, listener, health/readiness, source revision, and database identity before treating a service as live.
5. Stop or restart services only through their supported management path. Snapshot or preserve meaningful state before migration, restore, or destructive maintenance.

## Development versus production

- **Development:** use `test`, development configuration, isolated development state, and local/managed development services. Validate behavior here first.
- **Production:** use `main`, production configuration, managed persistent state, and the approved deployment path. Do not make direct source edits or ad-hoc service substitutions in production.
- A service is not deployed merely because code was pushed. Prove the deployed revision and runtime ownership separately.

## Tailnet / Tailscale Serve

- Use Tailscale Serve only as the supported private ingress layer in front of an already healthy managed service.
- Bind the application service according to its own configuration; configure Serve to forward to that supported local listener rather than adding a second application server or proxy implementation.
- Keep the ingress configuration declarative and reviewable. Confirm the configured target, HTTPS/Tailnet origin, service health, and access policy after changes.
- Do not expose administrative, maintenance, recovery, motion, or secret-bearing endpoints merely by publishing an ingress route. Preserve the application’s existing authorization and admission controls.

## Orchestration and validation

- Use the API/workflow registry and supported launch paths. Do not invoke detached scripts or retired Nextflow workflows as production orchestration.
- Run focused tests from the owning subsystem. For the Python API, run from `platform/api`:
  ```bash
  uv run --frozen --group dev python -m pytest <focused tests>
  ```
- Run `git diff --check` before every commit. Do not stage virtual environments, dependency directories, build output, results, model weights, databases, or logs.
- Treat a successful command as insufficient when subordinate readiness, cleanup, authorization, or artifact checks report failure or ambiguity.

## Code-retirement rule

Deprecated source must be removed from `test` once an audit proves it is not required by current runtime entrypoints, dependency manifests, schemas, tests, supported workflows, or explicit migration/recovery needs. Do not retain dead code for archaeology; Git history is the archive. Remove associated stale tests, docs, package metadata, and UI references in the same change, then validate the surviving supported path.
