#!/usr/bin/python3
"""Install fixed socket recovery for the native user-service MinKNOW bridge."""
from __future__ import annotations
import argparse
import grp
import os
from pathlib import Path
import pwd
import re
import subprocess
import tempfile


def render_native_helper(source: Path, user: str) -> dict[str, tuple[str, int]]:
    if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', user):
        raise ValueError('Invalid native API user')
    account = pwd.getpwnam(user)
    if account.pw_uid == 0:
        raise ValueError('Native API user must be non-root')
    group = grp.getgrgid(account.pw_gid).gr_name
    replacements = {
        '__MINKNOW_SYSTEMD_SERVICE__': 'minknow.service',
        '__BMS_NATIVE_API_USER__': user,
        '__BMS_NATIVE_API_UID__': str(account.pw_uid),
        '__BMS_RECOVERY_GROUP__': group,
        '__BMS_REPO_ROOT__': str(source.parents[1]),
    }
    files = {}
    for name, target, mode in (
        ('bms-reconnect-mk1d-native.template', '/usr/local/sbin/bms-reconnect-mk1d', 0o750),
        ('bms-reconnect-mk1d.socket.template', '/etc/systemd/system/bms-reconnect-mk1d.socket', 0o644),
        ('bms-reconnect-mk1d@.service.template', '/etc/systemd/system/bms-reconnect-mk1d@.service', 0o644),
        ('bms-mk1d-reconnect.conf', '/etc/tmpfiles.d/bms-mk1d-reconnect.conf', 0o644),
    ):
        text = (source / name).read_text()
        for before, after in replacements.items():
            text = text.replace(before, after)
        if '__BMS_' in text or '__MINKNOW_' in text:
            raise ValueError('Unresolved recovery template')
        files[target] = (text, mode)
    files['/etc/biomodstack/mk1d-reconnect.env'] = (f'BMS_MK1D_RECOVERY_GID={account.pw_gid}\n', 0o644)
    return files


def atomic_root_write(path: Path, text: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.'+path.name, dir=path.parent)
    try:
        os.fchmod(fd, mode)
        os.fchown(fd, 0, 0)
        with os.fdopen(fd, 'w') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-user', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run this reviewed installer as root')
    source = Path(__file__).resolve().parent
    files = render_native_helper(source, args.api_user)
    for name, (text, mode) in files.items():
        atomic_root_write(Path(name), text, mode)
    for cmd in (
        ['systemd-tmpfiles', '--create', '/etc/tmpfiles.d/bms-mk1d-reconnect.conf'],
        ['systemctl', 'daemon-reload'],
        ['systemctl', 'enable', 'bms-reconnect-mk1d.socket'],
        ['systemctl', 'restart', 'bms-reconnect-mk1d.socket'],
    ):
        subprocess.run(cmd, check=True)
    print('Installed native Mk1D reconnect socket; no instrument action was performed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
