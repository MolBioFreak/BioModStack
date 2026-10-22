#!/usr/bin/python3
"""Install the native, localhost-only BNS bridge to the vendor MinKNOW API."""
import argparse
from pathlib import Path
import subprocess
import os
import secrets
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--installation-root', type=Path, required=True, help='Existing BNS installation prefix containing source and environment.sh')
parser.add_argument('--check', action='store_true', help='Validate installation paths without changing launchers, units or credentials')
args=parser.parse_args()
root=args.installation_root.expanduser().resolve()
assert (root/'source/platform/api/main.py').is_file(), 'The selected installation must contain its BNS source checkout'
assert (root/'environment.sh').is_file(), 'The selected installation must contain environment.sh'
if args.check:
    print('BNS installation paths are valid')
    raise SystemExit(0)
source=root/'source'
python=Path('/opt/ont/minknow/ont-python/bin/python')
assert python.is_file(), 'Install MinKNOW before the native bridge'
units=Path.home()/'.config/systemd/user';units.mkdir(parents=True,exist_ok=True)
unit=units/'bns-minknow-host-agent.service'
unit.write_text(f'''[Unit]
Description=BNS local MinKNOW device management bridge
After=network.target

[Service]
Type=simple
WorkingDirectory={source}
ExecStart={python} {source}/scripts/bms_host_agent.py --host 127.0.0.1 --port 18798
Environment=PYTHONDONTWRITEBYTECODE=1
Restart=on-failure
RestartSec=3
NoNewPrivileges=true

[Install]
WantedBy=default.target
''')
secret_path=Path.home()/'.config/biomodstack/native-minknow.env'
secret_path.parent.mkdir(parents=True, exist_ok=True)
if not secret_path.exists():
    token=secrets.token_urlsafe(48)
    fd=os.open(secret_path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write('BMS_MK1D_RECONNECT_LOCAL_PROXY_SECRET='+token+'\nBMS_DEV_MK1D_RECONNECT_PROXY_SECRET='+token+'\n')
secret_path.chmod(0o600)
frontend_dropin=units/'biomodstack-frontend.service.d'
frontend_dropin.mkdir(exist_ok=True)
(frontend_dropin/'minknow.conf').write_text('[Service]\nEnvironmentFile='+str(secret_path)+'\n')
dropin=units/'biomodstack-api.service.d';dropin.mkdir(exist_ok=True)
(dropin/'minknow.conf').write_text(f'''[Unit]
Wants=bns-minknow-host-agent.service
After=bns-minknow-host-agent.service

[Service]
EnvironmentFile={secret_path}
Environment=BMS_ONT_MINKNOW_ENABLED=1
Environment=BMS_HOST_AGENT_URL=http://127.0.0.1:18798
Environment=BMS_ONT_EXTERNAL_POD5_ROOT=/var/lib/minknow/data
''')
subprocess.run(['systemctl','--user','daemon-reload'],check=True)
subprocess.run(['systemctl','--user','enable','--now','bns-minknow-host-agent.service'],check=True)
print('Native MinKNOW bridge installed; restart the managed API to apply its configuration.')
