"""Real cold/warm SIF -> private rootfs -> udocker -> pinned PRoot path."""
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.request

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'platform/api'))
sys.path.insert(0,str(ROOT/'scripts'))
from tools import bms_container as container
from lib import shared_runtime_images as shared
from lib import runtime_image_views as views


def test_cold_warm_lazy_container(tmp_path,monkeypatch):
    wheel=os.environ.get('BMS_TEST_UDOCKER_WHEEL')
    engines=os.environ.get('BMS_TEST_UDOCKER_ENGINES')
    if not wheel or not engines:
        pytest.skip('supply independently acquired pinned udocker archives')
    source=(ROOT/'platform/api/services/remote_execution/bootstrap_worker.sh').read_text()
    embedded=source.split("<<'PY_INSTALL'\n",1)[1].split('\nPY_INSTALL',1)[0]
    monkeypatch.setattr(sys,'argv',['installer',str(tmp_path),str(ROOT/'containers/transport/proot/proot-x86_64-4_8_0')])
    monkeypatch.setattr(urllib.request,'urlopen',lambda url,**kw:Path(wheel if url.endswith('.whl') else engines).open('rb'))
    exec(compile(embedded,'pinned-installer','exec'),{})
    tools=tmp_path/'tools/udocker-1.3.17-bms3'
    rootfs=tmp_path/'fixture';rootfs.mkdir()
    shell=Path('/bin/bash')
    dependencies=re.findall(r'(/[^\s()]+)',subprocess.check_output(['ldd',str(shell)],text=True))
    for path in [shell,*map(Path,dependencies)]:
        target=rootfs/str(path).lstrip('/');target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,target);target.chmod(0o755)
    (rootfs/'etc').mkdir()
    (rootfs/'etc/passwd').write_text('root:x:0:0:root:/root:/bin/bash\n')
    (rootfs/'etc/group').write_text('root:x:0:\n')
    (rootfs/'data').write_text('original\n')
    os.link(rootfs/'data',rootfs/'alias')
    (rootfs/'untouched').write_bytes(b'x'*(32*1024*1024))
    (rootfs/'tmp').mkdir();(rootfs/'root').mkdir()
    squash=tmp_path/'root.squashfs';sif=tmp_path/'image.sif'
    subprocess.run(['mksquashfs',str(rootfs),str(squash),'-noappend','-processors','2','-no-progress'],check=True,stdout=subprocess.DEVNULL)
    subprocess.run(['apptainer','sif','new',str(sif)],check=True)
    subprocess.run(['apptainer','sif','add',str(sif),str(squash),'--datatype','4','--parttype','2','--partfs','1','--partarch','2'],check=True)
    digest=hashlib.sha256(sif.read_bytes()).hexdigest();store=tmp_path/'store'
    image=shared.publish_image(sif,store,digest)
    task=tmp_path/'task';task.mkdir();monkeypatch.chdir(task)
    for k,v in dict(BMS_RUNTIME_IMAGE_STORE=store,BMS_CONTAINER_WORK_ROOT=tmp_path/'work',BMS_UDOCKER=tools/'bin/udocker',UDOCKER_BIN=tools/'engines/bin',UDOCKER_LIB=tools/'engines/lib').items():
        monkeypatch.setenv(k,str(v))
    measurements=[]
    real_run=container.run_owned
    def measured(command,environment,fds,identity):
        private=Path(next(x.split('=',1)[1] for x in command if x.startswith('--location=')))
        def blocks():
            found={}
            for p in private.rglob('*'):
                if p.is_file() and not p.is_symlink():
                    st=p.stat();found[(st.st_dev,st.st_ino)]=st.st_blocks
            return sum(found.values())
        before=blocks()
        result=real_run(command,environment,fds,identity)
        after=blocks();measurements.append((before,after))
        assert (private/'untouched').stat().st_blocks==0
        assert (private/'data').stat().st_ino==(private/'alias').stat().st_ino
        assert (private/'data').read_text()=='private\n'
        print('LAZY_VIEW_BLOCKS_512',before,after,flush=True)
        return result
    monkeypatch.setattr(container,'run_owned',measured)
    invocation=container.parse_exec([str(image),'/bin/bash','-c','read -r x </data; test "$x" = original; printf "private\\n" >/alias; read -r y </data; test "$y" = private'])
    identity=dict(boot_id=container.owner.boot_id(),supervisor_pid=os.getpid(),supervisor_start_ticks=container.owner.process_start_ticks(os.getpid()))
    assert container.execute(invocation,identity)==0
    donor=views.derived_path(store,digest)/'rootfs'
    stamp=views.snapshot_tree(donor)
    assert (donor/'data').read_text()=='original\n'
    assert container.execute(invocation,identity)==0
    assert views.snapshot_tree(donor)==stamp
    assert len(measurements)==2
    assert not list((tmp_path/'work').iterdir())
