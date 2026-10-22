# Temporary public MolBio shares (Tailscale Funnel :8443)

Guests outside the tailnet can view a frozen, read-only bundle of selected
MolBio sequences/experiments (plus the full records of their lineage
ancestors) for at most 24 hours. Nothing here is enabled automatically.

## Pieces

| Piece | Where | Network |
|---|---|---|
| Grant create/list/revoke | main BMS API `/api/molbio/shares` (tailnet only) | unchanged |
| Public share server | `platform/api/share_server` → `/opt/bms-share` | **none** (`PrivateNetwork=yes`, socket-activated on `127.0.0.1:<port>`) |
| Guest frontend | `platform/frontend/share.html` (separate Vite build) | static |
| Funnel controller | `share_server.funnel_controller` timer/path units | runs `tailscale funnel --https=8443 …` only |

Data flow is one-way: the BMS API writes `grants.json` and frozen
`bundles/<grant>.json` into `BMS_SHARE_PUBLISH_DIR`; the share server reads
them read-only and appends only to `/var/log/bms-share/access.jsonl`, which
the BMS API reads for the share list's access log.

## Brute-force protection

* Every failed authentication (unknown link key or wrong passcode) is a strike
  against the client network: the IPv4 address, or the IPv6 /64.
* Three strikes within 24 h ban that network from every route (including the
  static page) for 7 days: HTTP 403 `share_client_banned`.
* Three wrong passcodes for one share, from any networks, lock that share for
  the rest of its life: HTTP 423 `share_locked`. Create a new share instead.
* Strikes and bans are kept in `/var/lib/bms-share/bans.json` (the unit's
  `StateDirectory`), so restarting the service does not reset them.
* The client address comes from Tailscale's `X-Forwarded-For` (trusted only
  from the loopback proxy). Loopback requests without it are never banned.
* Every failure, ban and lock is appended to `access.jsonl`. The tailnet share
  list (`GET /api/molbio/shares` → `security`) shows failed attempts in the
  last 24 h, active bans and recent failures, including attempts that did not
  match any share.
* A per-IP request rate limit (120/min) and per-share limit (600/min) still
  apply on top.

To lift a ban early: `sudo systemctl stop bms-share.service`, edit or delete
`/var/lib/bms-share/bans.json`, then let the socket restart it.

## Secrets

* Link key: 256-bit random, URL fragment only (`https://<host>:8443/s#<key>`),
  stored as SHA-256.
* Passcode: 8 characters from `ABCDEFGHJKMNPQRSTUVWXYZ23456789`, stored as
  scrypt (N=2^14, r=8, p=1) with a per-grant salt.
* Both are shown once at creation and never again.
* Agent token file (`BMS_SHARE_AGENT_TOKEN_FILE`, mode 0600, outside the
  repo, one `label:token` per line, token ≥ 32 chars). An agent token is
  accepted only by `/api/molbio/shares` routes and is subject to the same
  24 h cap.

## Configuration (BMS API supported env file)

```
BMS_SHARE_PUBLISH_DIR=/srv/bms-share/publish
BMS_SHARE_ACCESS_LOG_DIR=/var/log/bms-share
BMS_SHARE_PUBLIC_HOST=<node>.<tailnet>.ts.net
BMS_SHARE_AGENT_TOKEN_FILE=/etc/biomodstack/share-agent-tokens   # optional
```

## Install (manual, root)

```bash
# build the guest-only frontend into platform/frontend/dist-share/ (entry share.html)
sudo BMS_NATIVE_API_USER=<api user> BMS_SHARE_PORT=18090 \
    ./config/share-funnel/install-share-server.sh
sudo ./config/share-funnel/verify-share-sandbox.sh
# only after review:
sudo systemctl enable --now bms-share-funnel.timer bms-share-funnel.path
```

The controller enables `tailscale funnel --bg --https=8443
http://127.0.0.1:<port>` while at least one unexpired, unrevoked grant exists
and runs `tailscale funnel --https=8443 off` when none remain (or the index is
unreadable). It never issues a command for 443 or `tailscale serve`.
