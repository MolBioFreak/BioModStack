"""Render bounded Vast VM startup from immutable, checksum-verified assets."""
from __future__ import annotations

import hashlib
import re
import shlex
from pathlib import PurePosixPath

# Baseline GNU x86-64 install-only distribution pinned by BMS's uv 0.8.22.
PYTHON_URL = ('https://github.com/astral-sh/python-build-standalone/releases/download/20250918/'
              'cpython-3.12.11%2B20250918-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz')
PYTHON_SHA256 = 'd8f71e55f8fd6a4cc9d18ce697969b3326a3325615147e70a9c4b1fa1c3698a8'
HELPER_URL_PATTERN = (r'https://raw\.githubusercontent\.com/[A-Za-z0-9_.-]+/'
                      r'[A-Za-z0-9_.-]+/[0-9a-f]{40}/platform/api/tools/bms_artifact_cache\.py')


def render_vm_cache_template_fields(*, helper_bytes: bytes, helper_url: str,
                                    worker_root: str = '/opt/biomodstack',
                                    existing_env: str = '') -> dict:
    """Pin the exact helper without embedding its growing body in bounded fields.

    Only immutable GitHub commit URLs are accepted. Python installation is a VM
    prerequisite, not a scientific/support-runtime installer. Modern guests keep
    their interpreter; old guests get a separate pinned prefix and a persistent
    /usr/local/bin/python3 link. Distro /usr/bin/python3 is never modified.
    """
    if not helper_bytes or len(helper_bytes) > 2 * 1024 * 1024:
        raise ValueError('Invalid helper size')
    if not re.fullmatch(HELPER_URL_PATTERN, helper_url):
        raise ValueError('Helper URL must identify an immutable source commit')
    if not re.fullmatch(r'/[A-Za-z0-9_/-]+', worker_root) or '..' in PurePosixPath(worker_root).parts or worker_root == '/':
        raise ValueError('Invalid worker root')
    environment = {
        'BMS_CACHE_HELPER_URL': helper_url,
        'BMS_CACHE_HELPER_SHA256': hashlib.sha256(helper_bytes).hexdigest(),
        'BMS_CACHE_WORKER_ROOT': worker_root,
    }
    script = '''#!/bin/bash
set -euo pipefail
umask 077
export PATH=/usr/local/bin:$PATH
python3 - <<'BMS_CACHE_INIT'
import hashlib,os,pathlib,platform,re,shlex,subprocess,sys,tempfile,urllib.request
keys=('BMS_CACHE_HELPER_URL','BMS_CACHE_HELPER_SHA256','BMS_CACHE_WORKER_ROOT')
config={k:os.environ[k] for k in keys if k in os.environ}
if len(config)!=len(keys):
    for line in pathlib.Path('/etc/environment').read_text().splitlines():
        k,sep,v=line.partition('=');k=k.strip()
        if sep and k in keys and k not in config:
            parts=shlex.split(v,comments=True)
            if len(parts)!=1: raise RuntimeError('invalid cache bootstrap setting')
            config[k]=parts[0]
url,digest,root=(config[k] for k in keys)
if not re.fullmatch(HELPER_PATTERN,url) or not re.fullmatch('[0-9a-f]{64}',digest): raise RuntimeError('invalid helper identity')
if not re.fullmatch('/[A-Za-z0-9_/-]+',root) or '..' in pathlib.PurePosixPath(root).parts or root=='/': raise RuntimeError('invalid cache root')
class HTTPSOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,url):
        if not url.startswith('https://'): raise RuntimeError('HTTPS required')
        return super().redirect_request(req,fp,code,msg,headers,url)
def fetch(url,limit,digest):
    with urllib.request.build_opener(HTTPSOnly()).open(url,timeout=180) as response: data=response.read(limit+1)
    if len(data)>limit or hashlib.sha256(data).hexdigest()!=digest: raise RuntimeError('cache bootstrap hash mismatch')
    return data
payload=fetch(url,2097152,digest)
runner=pathlib.Path(root)/'runner';current=pathlib.Path('/')
for part in runner.parts[1:]:
    current=current/part
    if current.is_symlink(): raise RuntimeError('unsafe cache bootstrap path')
    current.mkdir(mode=0o700,exist_ok=True)
python=sys.executable
if sys.version_info<(3,11):
    if os.geteuid()!=0 or platform.machine()!='x86_64': raise RuntimeError('Python setup requires root x86_64 VM')
    local=pathlib.Path('/usr/local/bin/python3');dest=runner/'python-3.12.11-20250918'
    if local.exists() or local.is_symlink() or dest.exists() or dest.is_symlink(): raise RuntimeError('refusing existing Python destination')
    with tempfile.TemporaryDirectory(prefix='.python-',dir=runner) as staging:
        staging=pathlib.Path(staging);archive=staging/'python.tar.gz'
        archive.write_bytes(fetch(PYTHON_ASSET,67108864,PYTHON_DIGEST))
        subprocess.run(['tar','xzf',str(archive),'--no-same-owner','-C',str(staging)],check=True)
        subprocess.run([str(staging/'python/bin/python3'),'-I','-c','import sys,ssl,sqlite3;assert sys.version_info[:3]==(3,12,11)'],check=True)
        os.rename(staging/'python',dest)
    local.symlink_to(dest/'bin/python3');python=str(local)
fd,temp=tempfile.mkstemp(prefix='.cache-bootstrap-',dir=runner)
try:
    with os.fdopen(fd,'wb') as stream: stream.write(payload);stream.flush();os.fsync(stream.fileno())
    os.chmod(temp,0o500);destination=runner/('cache-'+digest+'.py');os.replace(temp,destination)
    subprocess.run([python,str(destination),'--root',str(pathlib.Path(root)/'cache/artifacts/v1')],input=b'{"action":"init"}',check=True)
finally:
    if os.path.exists(temp): os.unlink(temp)
BMS_CACHE_INIT
'''
    script = (script.replace('HELPER_PATTERN', repr(HELPER_URL_PATTERN))
              .replace('PYTHON_ASSET', repr(PYTHON_URL)).replace('PYTHON_DIGEST', repr(PYTHON_SHA256)))
    if len(script) > 4048:
        raise ValueError('Vast onstart field exceeds documented limit')
    if not isinstance(existing_env, str) or 'BMS_CACHE_' in existing_env:
        raise ValueError('Existing template cache environment requires explicit reconciliation')
    env = (existing_env.strip() + ''.join(' -e ' + shlex.quote(key + '=' + value)
                                         for key, value in environment.items())).strip()
    if len(env) > 4096:
        raise ValueError('Vast env field exceeds verified provider limit')
    return {'onstart': script, 'environment': environment, 'env': env}


def helper_identity(helper_path):
    return hashlib.sha256(helper_path.read_bytes()).hexdigest()
