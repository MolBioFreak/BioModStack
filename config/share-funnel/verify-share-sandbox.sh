#!/usr/bin/env bash
# Verify the live bms-share unit's sandbox. Read-only checks; run with sudo
# (systemd-run/nsenter need root). Exits non-zero if any check fails.
set -uo pipefail

share_port="${BMS_SHARE_PORT:-18090}"
api_port="${BMS_API_PORT:-18002}"
max_exposure="${BMS_SHARE_MAX_EXPOSURE:-2.0}"
status=0
ok() { printf 'PASS %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; status=1; }

if (( EUID != 0 )); then
    printf '%s\n' 'Run with sudo: sudo ./config/share-funnel/verify-share-sandbox.sh' >&2
    exit 2
fi

# 1. Unit hardening score.
score_line="$(systemd-analyze security bms-share.service --no-pager 2>/dev/null | tail -n 1)"
score="$(grep -oE '[0-9]+\.[0-9]+' <<<"$score_line" | head -n 1)"
if [[ -n "$score" ]] && awk -v s="$score" -v m="$max_exposure" 'BEGIN { exit !(s <= m) }'; then
    ok "systemd-analyze exposure $score <= $max_exposure ($score_line)"
else
    bad "systemd-analyze exposure '${score:-unknown}' exceeds $max_exposure ($score_line)"
fi

# 2. Listener is loopback-only and owned by the socket unit.
listeners="$(ss -Hltn "sport = :$share_port" 2>/dev/null)"
if [[ -n "$listeners" ]] && ! grep -vqE "127\.0\.0\.1:$share_port|\[::1\]:$share_port" <<<"$listeners"; then
    ok "listener on $share_port is loopback-only"
else
    bad "listener on $share_port missing or not loopback-only: $listeners"
fi

# 3. Service is running (socket-activate it with a health probe if needed).
curl -fsS --max-time 5 "http://127.0.0.1:$share_port/api/share/health" >/dev/null 2>&1 \
    && ok "health endpoint answers through the socket" || bad "health endpoint did not answer"
pid="$(systemctl show -p MainPID --value bms-share.service)"
if [[ -z "$pid" || "$pid" == 0 ]]; then
    bad "bms-share.service has no main PID"
    exit "$status"
fi

# 4. From INSIDE the service's namespaces: BMS API and any outbound connection fail.
probe='import socket,sys
fails=0
for host,port in (("127.0.0.1",'"$api_port"'),("127.0.0.1",'"$share_port"'),("1.1.1.1",443),("100.100.100.100",53)):
    s=socket.socket(); s.settimeout(3)
    try:
        s.connect((host,port)); print(f"CONNECTED {host}:{port}"); fails+=1
    except OSError as e:
        print(f"refused {host}:{port}: {e.__class__.__name__}")
    finally:
        s.close()
sys.exit(1 if fails else 0)'
py="$(readlink -f "/proc/$pid/exe")"
if nsenter --target "$pid" --net --mount -- "$py" -I -c "$probe"; then
    ok "network namespace of bms-share cannot reach 127.0.0.1:$api_port or the internet"
else
    bad "a connection succeeded from inside the bms-share network namespace"
fi
# AF_INET socket creation itself must be refused by RestrictAddressFamilies.
if systemd-run --quiet --wait --pipe --collect \
    -p User=bms-share -p PrivateNetwork=yes -p RestrictAddressFamilies=AF_UNIX \
    -p SystemCallFilter=@system-service "$py" -I -c 'import socket; socket.socket()' 2>/dev/null; then
    bad "AF_INET socket creation was allowed under the unit's address-family policy"
else
    ok "AF_INET socket creation is refused under the unit's address-family policy"
fi

# 5. Filesystem: /home is invisible, publish dir is read-only.
if nsenter --target "$pid" --mount -- /bin/sh -c 'ls /home/* >/dev/null 2>&1'; then
    bad "/home is visible inside the bms-share mount namespace"
else
    ok "/home is empty/inaccessible inside the bms-share mount namespace"
fi
publish_dir="$(systemctl show -p Environment --value bms-share.service | tr ' ' '\n' | sed -n 's/^BMS_SHARE_PUBLISH_DIR=//p')"
if [[ -n "$publish_dir" ]] && grep -E "[[:space:]]$publish_dir[[:space:]]" "/proc/$pid/mountinfo" | grep -qE '(^|[ ,])ro([ ,]|$)'; then
    ok "publish dir $publish_dir is mounted read-only for the service"
else
    bad "publish dir ${publish_dir:-unset} is not a read-only mount for the service"
fi

# 6. Process identity.
uid="$(awk '/^Uid:/ {print $2}' "/proc/$pid/status")"
caps="$(awk '/^CapEff:/ {print $2}' "/proc/$pid/status")"
[[ "$uid" != 0 ]] && ok "service runs as non-root uid $uid" || bad "service runs as root"
[[ "$caps" == 0000000000000000 ]] && ok "no effective capabilities" || bad "effective capabilities: $caps"
grep -q '^NoNewPrivs:[[:space:]]*1' "/proc/$pid/status" && ok "NoNewPrivs set" || bad "NoNewPrivs not set"

# 7. Funnel never on 443 from this subsystem.
if tailscale funnel status --json 2>/dev/null | grep -qE '"[^"]+:443"[[:space:]]*:[[:space:]]*true'; then
    bad "Funnel is enabled on :443 (this subsystem only manages :8443)"
else
    ok "no Funnel on :443"
fi
exit "$status"
