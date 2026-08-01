# Mk1D reconnect helper (manual root installation)

`POST /api/ont/devices/reconnect` is a deliberately narrow **local BMS host
operator** recovery action:

- it accepts only JSON `{"confirm_reconnect": true}` (unknown fields and every
  other value are rejected);
- the Tailnet production proxy explicitly returns `403` for this exact route;
  it never forwards it to bms-web or bms-api; and
- bms-api accepts only its loopback bms-web proxy peer plus the separate,
  server-only `BMS_MK1D_RECONNECT_LOCAL_PROXY_SECRET` injected by bms-web.
  It ignores `Tailscale-User-Login` and all forwarded headers for this action.

The secret is held only by bms-api and bms-web; it is not a browser header and
must not be placed in Tailnet proxy configuration or policy. If it is missing,
the endpoint returns `503`. Direct bms-api requests and requests with forged
Tailnet/forwarded headers are denied. Use the local bms-web address on the BMS
host (normally `http://127.0.0.1:18080/bms/`) to use the operator control.

The root-owned, no-argument socket helper ignores input and runs one serialized
transaction. It first takes a root-owned nonblocking lock. A simultaneous
request gets the fixed non-secret `busy` receipt and **does not reach MinKNOW
or Docker**. The admitted transaction:

1. reads MinKNOW's systemd `ActiveState`;
2. starts `minknow.service` only if it is `inactive` or `failed`, then waits at
   most 45 seconds for `active`;
3. leaves active MinKNOW alone—especially no restart when it may own a
   protocol/acquisition;
4. runs exactly `docker compose --env-file /dev/null -p <installed project> -f
   /etc/biomodstack/mk1d-reconnect-compose.json up -d --no-build --no-deps
   --force-recreate bms-host-agent`; and
5. waits at most 45 seconds for the literal host-agent container's healthcheck
   and makes only bounded GETs to `/health` and `/ont/status`.

The installer renders that minimal, root-owned JSON Compose artifact from the
reviewed runtime configuration **at installation time**. The installed helper
never reads the checkout, its Compose file, or a project `.env`. It never
starts sequencing or a hardware check, calls no flow-cell API, changes no flow
cell, and does not start/restart BMS API, web, or other services.

Receipts distinguish `host_agent_recreate: requested` from
`host_agent_health: verified`; a requested recreate is not presented as a
verified recovery. `connected: true` is reserved for a separate read-only,
post-action Mk1D observation that has no connection error. Missing/error
observations remain unconfirmed.

## Install (requires an intentional root action)

Review the checkout and run from its root:

```bash
sudo ./config/mk1d-reconnect/install-root-helper.sh
```

If this host uses a different *systemd unit name*, supply it only during the
trusted root installation; it is validated and baked into the resulting helper:

```bash
sudo MINKNOW_SYSTEMD_SERVICE=custom-minknow.service ./config/mk1d-reconnect/install-root-helper.sh
```

The installer uses the actual native BMS API service user—by default the
validated invoking `SUDO_USER`, falling back to `dalab`—and renders the systemd
socket with that user's **primary group**. It does not create a separate
`bms-mk1d-recovery` group. The root-owned helper, socket/service units, and
recovery Compose/config files remain root-owned. `/etc/biomodstack/mk1d-reconnect.env`
contains one validated, atomic `BMS_MK1D_RECOVERY_GID` assignment. On the next
normal managed runtime restart, `scripts/run_biomodstack_core_runtime.sh`
imports it only when it is a root-owned regular `0644` file with exactly one
numeric assignment; otherwise it fails closed. The container API receives that
numeric group explicitly, while a native API process has access through its
primary group. Do not manually copy this GID into a user/project `.env`.

The API and web surfaces must already be running for an operator to press the
button. If the core runtime is down after a reboot, recover it through its
normal managed service path first; this feature deliberately does not start
BMS core/API/web automatically.

## Removal

Disable the socket and remove the installed root files only under an approved
host-change procedure. Removing the socket leaves the API endpoint fail-closed
with `Reconnect helper unavailable/not installed`.
