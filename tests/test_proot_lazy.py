"""Real pinned-engine lazy private inode contracts (no mocked syscalls)."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / 'containers/transport/proot/proot-x86_64-4_8_0'

CASES = {
    'read': "f=os.open(p,0);assert os.read(f,8)==b'original';assert os.stat(p).st_blocks>0;assert os.fstat(f).st_ino==os.stat(p).st_ino",
    'earlier_fd': "f=os.open(p,0);g=os.open(p,os.O_WRONLY);os.write(g,b'private!');assert os.read(f,8)==b'private!'",
    'shared_mmap': "import mmap;f=os.open(p,0);m=mmap.mmap(f,8,flags=mmap.MAP_SHARED,prot=mmap.PROT_READ);g=os.open(p,os.O_RDWR);w=mmap.mmap(g,8);w[:]=b'private!';assert m[:]==b'private!';assert os.read(f,8)==b'private!'",
    'opath_proc': "f=os.open(p,os.O_PATH);g=os.open('/proc/self/fd/'+str(f),os.O_RDWR);os.write(g,b'private!');assert open(p,'rb').read()==b'private!'",
    'hardlink_symlink': "f=os.open(p,0);os.link(p,p+'2');os.symlink(p+'2',p+'3');open(p+'3','w').write('private!');assert os.read(f,8)==b'private!';assert os.stat(p).st_ino==os.stat(p+'2').st_ino",
    'rename_unlink': "f=os.open(p,os.O_RDWR);os.rename(p,p+'2');os.unlink(p+'2');os.write(f,b'private!');os.lseek(f,0,0);assert os.read(f,8)==b'private!'",
    'fork': "f=os.open(p,0);pid=os.fork();\nif pid==0:\n open(p,'w').write('private!');os._exit(0)\nassert os.waitpid(pid,0)[1]==0;assert os.read(f,8)==b'private!'",
    'openat2': "import ctypes;libc=ctypes.CDLL(None,use_errno=True);how=(ctypes.c_uint64*3)(0,0,0);f=libc.syscall(437,-100,os.fsencode(p),ctypes.byref(how),24);assert f>=0,ctypes.get_errno();assert os.read(f,8)==b'original'",
    'metadata': "f=os.open(p,0);os.fchmod(f,0o600);os.utime(f,ns=(123000000000,123000000000));assert os.stat(p).st_mtime_ns==123000000000;assert os.fstat(f).st_mode&0o777==0o600;os.setxattr(f,'user.lazy',b'x');assert os.getxattr(f,'user.lazy')==b'x'",
}

def project(tmp_path, members):
    lower, upper = tmp_path/'lower', tmp_path/'upper'
    lower.mkdir(); upper.mkdir()
    pairs = []
    for name, content, mode in members:
        a,b = lower/name,upper/name
        a.parent.mkdir(parents=True,exist_ok=True);b.parent.mkdir(parents=True,exist_ok=True)
        a.write_bytes(content);a.chmod(mode & ~0o222)
        b.touch();os.truncate(b,len(content));b.chmod(mode)
        pairs.append((a,b))
    mapping=tmp_path/'map'
    mapping.write_bytes(b''.join(os.fsencode(b)+b'\0'+os.fsencode(a)+b'\0' for a,b in pairs))
    return upper,mapping,pairs


def run(mapping, command, *options):
    return subprocess.run([str(ENGINE),'-0','-r','/',*options,*command],env=dict(os.environ,BMS_PROOT_COW_MAP=str(mapping)),capture_output=True,text=True,timeout=30)

@pytest.mark.parametrize('case', CASES)
def test_descriptor_semantics(tmp_path,case):
    upper,mapping,pairs=project(tmp_path,[('data',b'original',0o644),('untouched',b'x'*(4*1024*1024),0o644)])
    before=[(a.stat().st_ctime_ns,hashlib.sha256(a.read_bytes()).hexdigest()) for a,b in pairs]
    result=run(mapping,[sys.executable,'-c','import os,sys;p=sys.argv[1];'+CASES[case],'/lazy-fixture/data'],'-b',str(upper)+':/lazy-fixture')
    assert result.returncode==0,result.stderr
    assert (upper/'untouched').stat().st_blocks==0
    assert before==[(a.stat().st_ctime_ns,hashlib.sha256(a.read_bytes()).hexdigest()) for a,b in pairs]


def test_concurrent_private_views(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    donor = tmp_path / 'donor'
    donor.write_bytes(b'original'); donor.chmod(0o444)
    before = donor.stat().st_ctime_ns
    mappings = []
    for index in range(2):
        upper = tmp_path / ('view' + str(index))
        upper.touch(); os.truncate(upper, 8)
        mapping = tmp_path / ('map' + str(index))
        mapping.write_bytes(os.fsencode(upper) + b'\0' + os.fsencode(donor) + b'\0')
        mappings.append((upper, mapping))
    def execute(item):
        upper, mapping = item
        return run(mapping, [sys.executable, '-c',
            "import os,sys;p=sys.argv[1];f=os.open(p,0);assert os.read(f,8)==b'original';"
            "open(p,'w').write(p[-1]*8);os.lseek(f,0,0);assert os.read(f,8)==(p[-1]*8).encode()",
            str(upper)])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(execute, mappings))
    assert all(result.returncode == 0 for result in results), [r.stderr for r in results]
    assert donor.read_bytes() == b'original' and donor.stat().st_ctime_ns == before
    assert mappings[0][0].read_bytes() == b'00000000'
    assert mappings[1][0].read_bytes() == b'11111111'


def test_exec_elf_script_loader(tmp_path):
    # Bind sparse private ELF, its interpreter and a script to guest paths.
    binary=Path('/bin/dash').resolve()
    output=subprocess.check_output(['readelf','-l',str(binary)],text=True)
    loader=Path(output.split('Requesting program interpreter: ')[1].split(']')[0])
    upper,mapping,pairs=project(tmp_path,[('shell',binary.read_bytes(),0o755),('loader',loader.read_bytes(),0o755),('script',b'#!/lazy-shell\nprintf lazy-exec-ok\n',0o755),('untouched',b'x'*1048576,0o644)])
    before=[a.stat().st_ctime_ns for a,b in pairs]
    result=run(mapping,['/lazy-script'],'-b',str(upper/'shell')+':/lazy-shell','-b',str(upper/'loader')+':'+str(loader),'-b',str(upper/'script')+':/lazy-script')
    assert result.returncode==0,result.stderr
    assert result.stdout=='lazy-exec-ok'
    assert all((upper/n).stat().st_blocks>0 for n in ['shell','loader','script'])
    assert (upper/'untouched').stat().st_blocks==0
    assert before==[a.stat().st_ctime_ns for a,b in pairs]
