"""Render the VM-supported On-start field without secrets or mutable URLs."""
from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath


def render_vm_cache_template_fields(*, helper_bytes: bytes,
                                    worker_root: str = '/opt/biomodstack',
                                    existing_env: str = '') -> dict:
    """Vast's 4048-character On-start limit: payload is nonsecret template env.

    VM template variables are written to /etc/environment. Parse only our three
    closed keys if they are not exported by the guest; never source/eval that file.
    The caller must preserve existing template env and reject key collisions.
    """
    import base64
    import gzip
    if not helper_bytes or len(helper_bytes) > 2 * 1024 * 1024:
        raise ValueError('Invalid helper size')
    if not re.fullmatch(r'/[A-Za-z0-9_/-]+', worker_root) or '..' in PurePosixPath(worker_root).parts or worker_root == '/':
        raise ValueError('Invalid worker root')
    encoded = base64.b64encode(gzip.compress(helper_bytes, mtime=0)).decode()
    # Env is also capped at 4096 chars by Vast. Reserve room for existing flags;
    # the remaining immutable payload prefix fits in the On-start Python literal.
    prefix, suffix = encoded[:-3500], encoded[-3500:]
    environment = {
        'BMS_CACHE_HELPER_GZ_B64': suffix,
        'BMS_CACHE_HELPER_SHA256': hashlib.sha256(helper_bytes).hexdigest(),
        'BMS_CACHE_WORKER_ROOT': worker_root,
    }
    script = '''#!/bin/bash
set -euo pipefail
umask 077
python3 - <<'BMS_CACHE_INIT'
import base64,gzip,hashlib,io,json,os,pathlib,re,shlex,subprocess,sys,tempfile
keys=('BMS_CACHE_HELPER_GZ_B64','BMS_CACHE_HELPER_SHA256','BMS_CACHE_WORKER_ROOT')
config={k:os.environ[k] for k in keys if k in os.environ}
if len(config)!=len(keys):
    for line in pathlib.Path('/etc/environment').read_text().splitlines():
        k,sep,v=line.partition('=')
        k=k.strip()
        if sep and k in keys and k not in config:
            parts=shlex.split(v,comments=True)
            if len(parts)!=1: raise RuntimeError('invalid cache bootstrap setting')
            config[k]=parts[0]
encoded,digest,root=(config[k] for k in keys)
encoded=PAYLOAD_PREFIX+encoded
if len(encoded)>3000000 or not re.fullmatch('[0-9a-f]{64}',digest): raise RuntimeError('invalid helper identity')
if not re.fullmatch('/[A-Za-z0-9_/-]+',root) or '..' in pathlib.PurePosixPath(root).parts or root=='/': raise RuntimeError('invalid cache root')
with gzip.GzipFile(fileobj=io.BytesIO(base64.b64decode(encoded,validate=True))) as stream:
    payload=stream.read(2097153)
if len(payload)>2097152 or hashlib.sha256(payload).hexdigest()!=digest: raise RuntimeError('cache bootstrap hash mismatch')
runner=pathlib.Path(root)/'runner'
current=pathlib.Path('/')
for part in runner.parts[1:]:
    current=current/part
    if current.is_symlink(): raise RuntimeError('unsafe cache bootstrap path')
    current.mkdir(mode=0o700,exist_ok=True)
fd,temp=tempfile.mkstemp(prefix='.cache-bootstrap-',dir=runner)
try:
    with os.fdopen(fd,'wb') as stream:
        stream.write(payload);stream.flush();os.fsync(stream.fileno())
    os.chmod(temp,0o500)
    destination=runner/('cache-'+digest+'.py')
    os.replace(temp,destination)
    subprocess.run([sys.executable,str(destination),'--root',str(pathlib.Path(root)/'cache/artifacts/v1')],input=b'{"action":"init"}',check=True)
finally:
    if os.path.exists(temp): os.unlink(temp)
BMS_CACHE_INIT
'''
    script = script.replace('PAYLOAD_PREFIX', repr(prefix))
    if len(script) > 4048:
        raise ValueError('Vast onstart field exceeds documented limit')
    import shlex
    if not isinstance(existing_env, str) or any(key in existing_env for key in environment):
        raise ValueError('Existing template cache environment requires explicit reconciliation')
    env = (existing_env.strip() + ''.join(' -e ' + shlex.quote(key + '=' + value)
                                         for key, value in environment.items())).strip()
    if len(env) > 4096:
        raise ValueError('Vast env field exceeds verified provider limit')
    return {'onstart': script, 'environment': environment, 'env': env}


def helper_identity(helper_path):
    return hashlib.sha256(helper_path.read_bytes()).hexdigest()
