# BioXP Compact Control Plane

## Status

This document describes the current BioModStack BioXP integration after the
replacement of the monolithic robot proxy. It is the canonical BMS-side operator
and developer contract. Dated BioXP plans and Phase-0/Phase-1 audit artifacts are
historical evidence, not current runtime documentation.

## Scope

BioModStack owns a deliberately small control plane:

- one persisted target profile;
- one process-local connection that always starts disconnected;
- explicit connect, disconnect, and probe actions;
- orthogonal connection/readiness evidence with freshness;
- deterministic offline protocol validation;
- durable local jobs and append-only transition events;
- bounded, typed command admission;
- a separate emergency-stop delivery path;
- local command history only.

BioModStack does **not** own robot process lifecycle, SSH, systemd, reboot,
remote log collection, arbitrary proxy paths, raw motion, liquid handling,
thermal control, camera/vision control, or robot-local motor semantics.

## API inventory

The feature-gated router is mounted at `/api/bioxp` only when
`BMS_FEATURE_BIOXP` is enabled.

| Method | Path | Contract |
|---|---|---|
| GET, PUT, DELETE | `/profile` | Read masked profile, save validated profile, or forget it |
| GET | `/status` | Connection/readiness evidence and server-advertised controls |
| POST | `/connection/connect` | Explicitly activate the saved profile |
| POST | `/connection/disconnect` | Close the process-local connection and advance generation |
| POST | `/connection/probe` | Refresh bounded readiness evidence for the active generation |
| GET | `/logs` | BMS-local command history; never remote robot logs |
| POST | `/protocols/compile` | Deterministic offline validation only |
| POST | `/protocols/submit` | Persist a local job; currently records `submission_blocked` without delivery |
| GET | `/jobs` | List bounded local job projections |
| GET | `/jobs/{job_id}` | Read one job and append-only transition events |
| POST | `/commands` | Admit one typed normal command through global and command-specific policy |
| GET | `/commands` | Read bounded local command history |
| GET | `/commands/{command_id}` | Read one local command result |
| POST | `/emergency-stop` | Attempt short-timeout delivery without claiming physical effect |

The router exposes 13 unique paths and 16 method/path operations. Arbitrary path,
command-name, shell, lifecycle, commissioning, and hardware route families are
absent.

## Startup and state truth

A saved profile is configuration, not an active connection. API startup:

1. loads/migrates local state;
2. initializes the BioXP runtime disconnected;
3. performs no robot HTTP request;
4. advertises no normal command unless every policy gate is satisfied.

A connection snapshot keeps these facts separate:

- configured;
- active;
- generation;
- reachable;
- runtime ready;
- hardware ready;
- observed timestamp and freshness;
- capabilities;
- command-in-progress state;
- last error.

Unknown facts are `null`, not optimistic booleans. Stale or unknown evidence does
not authorize normal commands. Each process starts with a new opaque generation
epoch; disconnect and target replacement advance it. Delayed requests from a prior
process or target therefore cannot match a newly connected target.

## Persistence

Under the resolved BMS data root:

- `bioxp/profile.json` stores the canonical target profile with private file mode;
- `bioxp/jobs.sqlite3` stores jobs and append-only state transitions; SQLite
  triggers reject direct update or deletion of transition events;
- legacy profile/job state is migrated once or quarantined when malformed.

An interrupted active job is recovered as `recovery_required` on process open or
immediately after legacy migration.
Idempotency keys are durable and cannot be rebound to different protocol content.

## Target policy

Saved targets must satisfy the BMS BioXP target policy:

- HTTP or HTTPS only;
- root URL only;
- no credentials, query, or fragment;
- approved port;
- host must be explicitly allowlisted, or a literal address must be inside an
  allowlisted CIDR;
- every resolved address must be inside an explicitly trusted network;
- only RFC1918 IPv4, CGNAT/Tailscale IPv4, and IPv6 ULA classes can be admitted;
  public/global, documentation, benchmarking, protocol-assignment, reserved,
  unspecified, multicast, link-local, and loopback answers fail closed even if
  a configured CIDR such as `0.0.0.0/0` or `::/0` would otherwise contain them;
- an empty trusted-network configuration fails closed.

The outbound transport connects to the first policy-validated address rather than
resolving the hostname again. It preserves the HTTP `Host` header and HTTPS SNI,
rejects redirects, and disables environment-proxy discovery. This closes the
DNS-rebinding/proxy bypass between validation and connection.

Configuration:

- `BMS_BIOXP_ALLOWED_HOSTS` (default `robot`);
- `BMS_BIOXP_ALLOWED_CIDRS` (explicitly required before connection; configure
  only the deployed robot's trusted network or networks).

The persisted URL is never returned raw through read/status routes; operator
surfaces receive a masked target.

## Mutation authorization

Every non-read mutation except deterministic offline compile requires both:

1. `BMS_BIOXP_MUTATIONS_ENABLED=1`; and
2. a matching transient operator token supplied as
   `X-BMS-BioXP-Operator-Token` or a Bearer token.

Credential source precedence is strict:

1. `BMS_BIOXP_OPERATOR_TOKEN_FILE` when configured;
2. otherwise `BMS_BIOXP_OPERATOR_TOKEN`.

A configured token file that is missing, unreadable, or empty returns a fail-closed
service error and never falls back to the environment token. No token is embedded
in frontend source or persisted by either operator surface.

Deterministic `/protocols/compile` is the only non-read route available while
mutations are disabled. Profile save/forget, connection actions, local submission,
normal commands, and emergency stop all require the kill switch and transient
operator credential.

## Command policy

Normal commands are discriminated typed requests. Unknown names, extra parameters,
stale generations, missing readiness/capabilities, concurrent normal commands,
and idempotency conflicts are rejected before delivery.

The default command registry contains no verified robot route mapping. Therefore
normal OEM commands remain unavailable until an online contract is independently
verified and intentionally enabled. This is not a placeholder success state.

Emergency stop is separate from the normal-command lock. Its result distinguishes:

- delivery attempted;
- remote acknowledged;
- physical effect verified (always false in the BMS transport result).

Only literal JSON boolean `true` in `acknowledged` or `ok` counts as a remote
acknowledgement. An HTTP/transport acknowledgement is never reported as proof of
physical stop. Concurrent emergency requests sharing an idempotency key join one
in-flight delivery; conflicting key reuse is rejected.

## Protocol and job semantics

Offline compile returns a canonical hash, required capabilities, explicit blockers,
`robot_compatible: null`, and `executable: false`.

Submission persists a durable local job and currently transitions it to
`submission_blocked` with `delivery_attempted: false`. The UI must not relabel this
as a robot run.

## Operator surfaces

Web and Electron share the same React route and API client:

- `/bioxp` renders status-first connection/readiness evidence, server-advertised
  commands, emergency-stop delivery evidence, offline validation, and local jobs;
- the feature-gated top-bar BioXP menu owns profile and explicit connection actions;
- the GTK panel reads only the compact `/status` route and contains no robot host
  lifecycle or remote-log actions;
- failed status refreshes suppress cached readiness and command controls, while
  cached positive evidence expires locally using `observed_at` plus the server
  freshness budget;
- malformed profile responses keep an invariant nullable profile envelope and
  surface their refusal detail;
- install-feature responses advertise only routes mounted in the running API;
  configured add/remove changes become effective after the required restart.

## Verification boundary

The default test lane denies INET/DNS before collection, runs inside a route-free
unprivileged network namespace, and blocks child-process bypasses. Live robot tests
require a separate explicit maintenance authorization and are not implied by an
offline green suite.

No robot host, USB, motion, reboot, service restart, or deployment action is part
of this control-plane replacement verification.
