# Robot-control service restart

BMS exposes an explicit service-only recovery action, independent of the native
API connection and observations. It restarts the existing immutable-release
owner `bioxp-api.service`; it does not update its release or reboot the PC.
It sends no hardware initialization, homing, counter reset, Stop, USB reset,
reclaim, or `handlerctl` command. The unit's own normal startup remains unchanged.

## Server configuration

Set both on the BMS API server:

- `BMS_BIOXP_SERVICE_SSH_TARGET`: a trusted SSH host/alias, optionally `user@host`.
  Only ordinary ASCII hostname/alias and username syntax is accepted; no URI,
  port suffix, whitespace, options or shell syntax. Use trusted server SSH config
  for key/port selection; provision known_hosts beforehand.
- `BMS_BIOXP_SERVICE_API_URL`: the robot API URL bound to that SSH destination.
  It must match the saved profile after local scheme/hostname/default-port/root
  slash normalization. Matching performs no DNS or robot request.

The existing `BMS_BIOXP_MUTATIONS_ENABLED=1` authorization is required for POST.
Missing/invalid/mismatched service configuration disables only this capability;
no existing robot action gains a new gate. No live connection is required.
The BMS process account needs noninteractive SSH and remote sudo permission for
**only** `systemctl restart bioxp-api.service`. No credentials are accepted by
these endpoints. SSH stderr is discarded rather than exposed to clients.

## HTTP contract

- `GET /api/bioxp/service`: local metadata only, returning
  `{available: boolean, detail: string|null, unit: "bioxp-api.service", restart_in_progress: boolean}`.
  `available` describes configuration binding, not SSH reachability, mutation
  authorization, API readiness, or robot health. No status/hardware call occurs.
- `POST /api/bioxp/service/restart`: required strict empty JSON object `{}`;
  unknown fields/non-object/missing bodies return 422. There are no client-owned
  target, unit, command, freshness, reference or history parameters.
- Success returns `{restarted: true, unit: "bioxp-api.service", active_state: string,
  sub_state: string, invocation_id: string, pid: integer}` using the exact returned
  systemd observation. This is **not** a claim that the API or hardware is ready.
- 409 means another restart is in progress; duplicate requests do not queue.
  The single API-process owner holds its nonblocking lock through child cleanup.
  Deploy this action through the existing single-process BMS API owner, not
  independent replicated restart owners.
- 503 means disabled mutation access or unavailable/mismatched configuration.
  502 means SSH/transport/timeout or invalid observation; the remote outcome is
  uncertain and there is no automatic retry. Cancellation also cannot establish
  whether systemd completed remotely. A later explicit attempt is not prohibited.

The only remote command is the fixed string:

```sh
sudo -n systemctl restart bioxp-api.service && systemctl show bioxp-api.service --property=ActiveState --property=SubState --property=InvocationID --property=MainPID
```

BMS uses `asyncio.create_subprocess_exec` with no local shell, SSH batch mode,
`ConnectTimeout=10`, `StrictHostKeyChecking=yes`, `ConnectionAttempts=1`, no TTY,
and a 60-second deadline. Timeout/cancellation kills and reaps the local SSH
child (including cancellation during launch); that cannot roll back the remote
command. The action never retries or polls the native API afterward.

Qualification is offline: actual FastAPI routes, profile store and service owner,
with an inert SSH process boundary inside the repository's route-free network
namespace. No live SSH, service restart, robot command or deployment is part of
these tests.
