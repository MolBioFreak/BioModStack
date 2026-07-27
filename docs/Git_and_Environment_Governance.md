# Git, GitHub, and Environment Governance

## Purpose

BioModStack has two permanent product environments and only two permanent development branches. Temporary AI work is disposable and must converge into the canonical development lane before it is closed.

## Permanent branches and worktrees

| Environment | Branch | Canonical worktree | Runtime | Promotion authority |
|---|---|---|---|---|
| Production | `main` | `/home/dalab/biomodstack/prod-main-canonical` | immutable API/web containers; `127.0.0.1:8000` and `127.0.0.1:18080` | explicit accepted release from `test` |
| Development | `test` | `/home/dalab/biomodstack/dev-test-canonical` | native dev API on `127.0.0.1:8002`; Vite on `127.0.0.1:5173` | completed AI/spec work |

Both canonical worktrees must be clean and exactly equal to their corresponding `origin/*` branch before deployment. A runtime may not claim an environment merely because it uses the expected port: its source root, full Git SHA, process/container owner, image labels, state root, and HTTP build identity must match the environment receipt.

`main` and `test` are the only long-lived GitHub branches. Do not create permanent `develop`, `staging`, release-train, per-agent, or per-feature branches.

## Environment contract

### Production

- Production is always built from an exact, clean `main` commit.
- Production uses `/mnt/BioModStack` and the managed production database.
- Normal service start never rebuilds images. Release promotion builds immutable images tagged by the full `main` SHA, verifies them, then deploys those exact image IDs.
- Production is changed only by an explicit `test` to `main` promotion after acceptance.

### Development

- Development always runs an exact, clean `test` commit.
- Development has a separate API port and state root (`8002` and `~/.biomodstack-dev` by default).
- Vite on `5173` proxies to the development API, not to Production.
- Every completed AI/spec tranche is promoted to `origin/test`, the canonical test worktree is fast-forwarded, and the development runtime is restarted and verified at that exact SHA.

### Tailnet

The stable Tailnet origin is routing/control plane only:

- Production root target: `127.0.0.1:18081`, normalizing Host and forwarding to Production on `18080`.
- Development root target: `127.0.0.1:5173`.
- Switching changes only the `/` handler. `/am`, `/vlm`, `/api/mobile-apk`, and the authenticated environment-control handler must remain byte-equivalent.
- `tailscale serve reset` is prohibited.
- A selector must reject Production unless runtime provenance equals canonical `main`, and reject Development unless runtime provenance equals canonical `test`.

## Temporary AI environments

Temporary environments are local ownership domains, not new shared branches or Tailnet deployments.

Use:

```bash
CANONICAL_TEST=/home/dalab/biomodstack/dev-test-canonical
AI_MANAGER="$CANONICAL_TEST/scripts/manage_ai_environment.py"

python3 "$AI_MANAGER" create --id <spec-slug> --repo "$CANONICAL_TEST"
python3 "$AI_MANAGER" start --id <spec-slug>
python3 "$AI_MANAGER" status --id <spec-slug>
```

The manager provides:

- one local-only `ai/<spec-slug>` branch based on exact `origin/test`;
- one registered worktree under `/home/dalab/worktrees/bms-ai/`;
- a unique loopback API/web port pair;
- an isolated database, work, cache, and input root;
- transient user-systemd ownership;
- `BMS_BIOXP_MUTATIONS_ENABLED=0`;
- no Tailscale Serve handler and no production container/unit names.

Temporary environments must never use ports `8000`, `8001`, `8002`, `5173`, `18080`, or `18081`; must never mount `/mnt/BioModStack` read-write; and must never actuate BioXP hardware.

## Spec completion and closeout

A temporary AI environment is complete only when all of these are true:

1. The written spec is mapped to executable evidence.
2. Focused and required broader tests pass after the last edit.
3. The AI worktree is clean and all intended changes are committed.
4. The branch is a fast-forward descendant of the current `origin/test`. If `origin/test` moved, rebase onto it and rerun the gates.
5. Promotion pushes `HEAD` directly to `origin/test`; it does **not** publish the temporary branch.
6. The canonical `test` worktree fast-forwards to the pushed SHA.
7. Development is restarted from the canonical test worktree and its API, Vite process, browser build, state root, and full SHA are verified.
8. The temporary runtime is stopped, the worktree is removed, and local `ai/*` branch state is deleted.

Commands:

```bash
python3 "$AI_MANAGER" promote --id <spec-slug> --repo "$CANONICAL_TEST"
python3 "$AI_MANAGER" close --id <spec-slug> --repo "$CANONICAL_TEST"
```

`close` fails if promotion has not occurred. `--discard` is an explicit exception for abandoned, clean, unpromoted experiments; it must never be used to hide meaningful dirty or committed work.

## Production promotion

Production promotion is separate from AI closeout:

1. Require clean `test` and `main` canonical worktrees exactly matching GitHub.
2. Require the accepted `test` SHA and all release gates.
3. Integrate `test` into `main` through one reviewed promotion; do not cherry-pick arbitrary feature branches into Production.
4. Push `main` without force.
5. Build immutable production images from the clean canonical main worktree.
6. Deploy exact image IDs and full revision labels.
7. Verify direct and Tailnet-routed API build identity, `/bms/`, BioXP visibility, security boundaries, updater continuity, and a clean browser console.
8. Keep Tailnet on Development during a production cutover if needed; switch it to Production only after Production is exact-ready.

## GitHub policy without branch hell

- Default branch: `main`.
- Long-lived branches: exactly `main` and `test`.
- No force pushes or history rewrites to either branch.
- No direct feature work in canonical worktrees.
- AI work remains local until promotion to `test`.
- A remote temporary branch is allowed only when an external PR/reviewer genuinely requires it; name it `ai/<spec-slug>`, delete it immediately after integration, and record why it existed.
- Use short linear commits. Squashing is optional, but the final `test` history must stay reviewable and bisectable.
- GitHub branch protection should require non-force updates and passing release checks. Production promotion may require an approval, while `test` remains automatable for serialized agent promotion.

## Recovery and archives

Before the 2026-07-27 harmonization, divergent local `main` and `test` tips were preserved in a verified Git bundle outside the repository. This is recovery evidence, not an active branch model. Do not recreate those divergent tips as normal remote branches.
