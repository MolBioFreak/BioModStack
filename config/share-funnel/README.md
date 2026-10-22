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
