#!/usr/bin/env python3
"""Rerunnable isolated Development acceptance; writes only a new owned /tmp run.

Host requires existing rootless Podman, QEMU/KVM, qemu-img, Git and Python stdlib.
No host installations, privilege escalation, Docker socket, host services or mounts
into the VM. A digest-verified official base is read-only backing storage only.
"""
import argparse
import base64
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid

HERE=Path(__file__).resolve().parent
BASE_SHA='d0fe84bb5f80853425fa6be28e2c106f30104c3cfe8611933f2e65c9b63f0e30'
BASE_URL='https://cloud-images.ubuntu.com/releases/noble/release-20260826/ubuntu-24.04-server-cloudimg-amd64.img'
PYTHON_IMAGE='docker.io/library/python@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254'
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--source',required=True,type=Path)
p.add_argument('--ref',default='HEAD')
p.add_argument('--base',type=Path,required=True,help='Task-owned official base image; pinned URL and SHA256 are checked')
p.add_argument('--timeout',type=int,default=2100)
a=p.parse_args()
run=Path(tempfile.mkdtemp(prefix='bms-nonprod-kvm-',dir='/tmp'))
print('RUN_DIRECTORY='+str(run),flush=True)
meta={'run_directory':str(run),'host_uid':os.getuid(),'base_url':BASE_URL,'base_sha256':BASE_SHA,
      'source':str(a.source.resolve()),'commands':[], 'host_installations':False,'vm_host_mounts':[],
      'production_acceptance':False,'python_seed_image':PYTHON_IMAGE}

def save(): (run/'host.json').write_text(json.dumps(meta,indent=2))
def command(label,argv,timeout=120):
    env={k:v for k,v in os.environ.items() if k not in {'CONTAINER_HOST','DOCKER_HOST','CONTAINER_CONNECTION'}}
    cp=subprocess.run(argv,env=env,capture_output=True,text=True,timeout=timeout)
    meta['commands'].append(dict(label=label,command=argv,exit_code=cp.returncode,stdout=cp.stdout,stderr=cp.stderr));save()
    if cp.returncode: raise RuntimeError(f'{label} refused: {cp.returncode}: {cp.stderr}')
    return cp.stdout
try:
    if os.getuid()==0: raise RuntimeError('Rootless host execution is required')
    fd=os.open('/dev/kvm',os.O_RDWR)
    try: meta['kvm_api']=fcntl.ioctl(fd,0xAE00,0)
    finally: os.close(fd)
    if command('rootless-info',['/usr/bin/podman','--remote=false','info','--format','{{.Host.Security.Rootless}}']).strip()!='true':
        raise RuntimeError('Podman refused rootless authority')
    meta['base_observed_sha256']=hashlib.file_digest(a.base.open('rb'),'sha256').hexdigest()
    if meta['base_observed_sha256']!=BASE_SHA: raise RuntimeError('Official base digest mismatch')
    sha=command('source-sha',['git','-C',str(a.source),'rev-parse',a.ref+'^{commit}']).strip()
    meta['source_sha']=sha
    command('source-export',['git','-C',str(a.source),'archive','--format=tar','--output='+str(run/'source.tar'),sha])
    manifest={}
    with tarfile.open(run/'source.tar') as tf:
        for member in tf.getmembers():
            if member.isfile():
                stream=tf.extractfile(member)
                if stream is None:raise RuntimeError('Missing tracked source bytes: '+member.name)
                manifest[member.name]=hashlib.sha256(stream.read()).hexdigest()
    (run/'source-manifest.json').write_text(json.dumps(manifest,sort_keys=True))
    (run/'source-sha').write_text(sha+'\n')
    meta['source_tar_sha256']=hashlib.file_digest((run/'source.tar').open('rb'),'sha256').hexdigest()
    shutil.copyfile(HERE/'guest_acceptance.py',run/'guest_acceptance.py')
    root_script=r'''#!/bin/bash
set -euo pipefail
exec > >(tee /var/log/bms-acceptance-root.log /dev/ttyS0) 2>&1
trap 'printf "BMS_VM_ACCEPTANCE_FINISHED\\n"; poweroff' EXIT
uname -a
systemd-detect-virt
mkdir -p /mnt/seed /home/ubuntu/bms /home/ubuntu/evidence
mount -o ro /dev/sr0 /mnt/seed
tar -xf /mnt/seed/source.tar -C /home/ubuntu/bms
chown -R ubuntu:ubuntu /home/ubuntu/bms /home/ubuntu/evidence
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3-pip python3-venv git curl
python3 - <<'PY'
import hashlib,io,json,pathlib,tarfile,urllib.request
version='v22.16.0'; name='node-'+version+'-linux-x64.tar.xz'; base='https://nodejs.org/dist/'+version+'/'
manifest=urllib.request.urlopen(base+'SHASUMS256.txt',timeout=60).read().decode()
expected=next(line.split()[0] for line in manifest.splitlines() if line.split()[-1]==name)
with urllib.request.urlopen(base+name,timeout=120) as response:
    data=response.read(60*1024*1024+1)
assert len(data)<=60*1024*1024,'Node archive exceeded bound'
actual=hashlib.sha256(data).hexdigest();assert actual==expected
with tarfile.open(fileobj=io.BytesIO(data),mode='r:xz') as tf: tf.extractall('/opt',filter='data')
pathlib.Path('/opt/bms-node').symlink_to('/opt/node-'+version+'-linux-x64')
for executable in ('node','npm','npx'):
    pathlib.Path('/usr/local/bin/'+executable).symlink_to('/opt/bms-node/bin/'+executable)
receipt={'url':base+'SHASUMS256.txt','archive':base+name,'expected_sha256':expected,'actual_sha256':actual,'bytes':len(data),'guest_base_prep_not_automatic_bms':True}
pathlib.Path('/home/ubuntu/evidence/node-base-prep.json').write_text(json.dumps(receipt,indent=2))
PY
loginctl enable-linger ubuntu
systemctl start user@1000.service
runuser -u ubuntu -- env HOME=/home/ubuntu python3 -B /mnt/seed/guest_acceptance.py
'''
    config={'write_files':[{'path':'/root/bms-acceptance.sh','permissions':'0755','content':root_script}],
            'runcmd':[['bash','/root/bms-acceptance.sh']]}
    (run/'user-data').write_text('#cloud-config\n'+json.dumps(config))
    (run/'meta-data').write_text('instance-id: bms-'+uuid.uuid4().hex+'\nlocal-hostname: bms-clean-nonprod\n')
    seed_code="""import pathlib,pycdlib
p=pathlib.Path('/data'); iso=pycdlib.PyCdlib();iso.new(interchange_level=3,joliet=3,rock_ridge='1.09',vol_ident='cidata')
for i,name in enumerate(['user-data','meta-data','source.tar','source-sha','source-manifest.json','guest_acceptance.py']):
 iso.add_file(str(p/name),iso_path='/FILE'+str(i)+';1',rr_name=name,joliet_path='/'+name)
iso.write('/data/seed.iso');iso.close()
"""
    (run/'make_seed.py').write_text(seed_code)
    command('create-nocloud-seed',['/usr/bin/podman','--remote=false','run','--rm','--pull=missing',
        '--name','bms-seed-'+uuid.uuid4().hex[:12],'--cap-drop=ALL','--security-opt=no-new-privileges',
        '--pids-limit=128','--memory=512m','--cpus=1','--volume',str(run)+':/data:rw',PYTHON_IMAGE,
        'sh','-c','python -m pip --disable-pip-version-check --no-cache-dir install pycdlib==1.14.0 >/tmp/install.log && python /data/make_seed.py && cat /tmp/install.log'],180)
    command('create-fresh-overlay',['/usr/bin/qemu-img','create','-f','qcow2','-F','qcow2','-b',str(a.base.resolve()),str(run/'guest.qcow2'),'24G'])
    argv=['/usr/bin/qemu-system-x86_64','-accel','kvm','-machine','q35','-cpu','host','-smp','4','-m','8192',
          '-drive',f'file={run}/guest.qcow2,if=virtio,format=qcow2',
          '-drive',f'file={run}/seed.iso,media=cdrom,readonly=on','-nic','user,model=virtio-net-pci',
          '-display','none','-serial','stdio','-monitor','none','-no-reboot']
    meta['qemu_command']=argv;save()
    with (run/'serial.log').open('w') as log:
        proc=subprocess.Popen(argv,stdout=log,stderr=subprocess.STDOUT)
        try: code=proc.wait(timeout=a.timeout)
        except subprocess.TimeoutExpired:
            meta['timed_out']=True;proc.terminate()
            try: code=proc.wait(timeout=20)
            except subprocess.TimeoutExpired:proc.kill();code=proc.wait()
        finally:
            if proc.poll() is None:proc.kill();proc.wait()
    meta.update(qemu_exit=code,vm_running=False);save()
    serial=(run/'serial.log').read_text(errors='replace')
    marker='BMS_EVIDENCE_BEGIN='
    evidence_meta=next((json.loads(line.split(marker,1)[1]) for line in serial.splitlines() if marker in line),None)
    if evidence_meta is None:raise RuntimeError('Guest did not deliver evidence; inspect serial.log')
    chunks={}
    for line in serial.splitlines():
        if 'BMS_EVIDENCE_CHUNK=' not in line:continue
        index,chunk=line.split('BMS_EVIDENCE_CHUNK=',1)[1].split(':',1)
        index=int(index)
        if index in chunks and chunks[index]!=chunk.strip():raise RuntimeError('Conflicting guest evidence chunk')
        chunks[index]=chunk.strip()
    if set(chunks)!=set(range(evidence_meta['chunks'])):raise RuntimeError('Incomplete guest evidence transport')
    archive=base64.b64decode(''.join(chunks[i] for i in range(evidence_meta['chunks'])),validate=True)
    if len(archive)!=evidence_meta['bytes'] or hashlib.sha256(archive).hexdigest()!=evidence_meta['sha256']:raise RuntimeError('Guest evidence digest mismatch')
    if 'BMS_EVIDENCE_END='+evidence_meta['sha256'] not in serial:raise RuntimeError('Missing guest evidence completion marker')
    (run/'evidence.tar.gz').write_bytes(archive)
    with tarfile.open(fileobj=io.BytesIO(archive),mode='r:gz') as tf:tf.extractall(run,filter='data')
    summary=json.loads((run/'evidence/summary.json').read_text())
    print(json.dumps(summary,indent=2),flush=True)
    if not summary['tracked_source_unchanged']:raise RuntimeError('Tracked source mutated inside guest')
    if not summary['nonproduction_managed_start_passed']:raise RuntimeError('Nonproduction acceptance failed; exact commands and service evidence preserved')
    if summary['production_activated']:raise RuntimeError('Unexpected Production service activation')
except BaseException as exc:
    meta['failure']=repr(exc);save();raise
finally:
    print('EVIDENCE_DIRECTORY='+str(run),flush=True)
