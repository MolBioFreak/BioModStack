#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build a new standalone bundle, never modify the managed image/cache.
Usage: build.py --image MANAGED.sif --source AmberTools24_rc5.tar.bz2 --output NEW_DIR
Requires existing gcc/ar/apptainer. No installation, download or image rebuild.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

IMAGE_SHA = "625d81608118af006295601b8275389e7681a1d98a6fb0622e6241c5d101df4b"
LIB_SHA = "c5c339ab2601e28368bea84b0bbd099c8cada70019c2aa2e793f306ef91fdbf9"
SOURCE_SHA = "52fb4fb3370a89b7ce738a2dc3e513c2fc1943fde4b4381846d9e75cc48d840f"


def digest(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--image', type=Path, required=True)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if digest(a.image) != IMAGE_SHA:
        raise RuntimeError('managed image differs')
    if digest(a.source) != SOURCE_SHA:
        raise RuntimeError('upstream source differs')
    a.output.mkdir(parents=True, exist_ok=False)
    out = a.output.resolve()
    src = out / 'source'
    src.mkdir()
    here = Path(__file__).resolve().parent
    for name in ('amber_fd_helix.c', 'build.py', 'NOTICE.md'):
        shutil.copyfile(here/name, src/name)
    # Static system runtime licenses remain independently applicable. Supply
    # copyright/license notices plus relinkable native object and launcher source.
    for source,name in [('/usr/share/doc/libc6/copyright','glibc-copyright'),
                        ('/usr/share/doc/gcc-11-base/copyright','gcc-runtime-copyright'),
                        ('/usr/share/common-licenses/LGPL-2.1','LGPL-2.1')]:
        shutil.copyfile(source,src/name)
    # Read only selected source entries: no archive path traversal or extraction.
    with tarfile.open(a.source, 'r|bz2') as t:
        for member in t:
            if member.name.endswith('/src/nab/fd_helix.c') or member.name.endswith('/src/nabc/fd_helix.c'):
                stream = t.extractfile(member)
                if stream is None:
                    raise RuntimeError('fd_helix source entry is not a file')
                (src/'fd_helix.c').write_bytes(stream.read())
    if not (src/'fd_helix.c').exists():
        raise RuntimeError('fd_helix source not found')
    extract = '''import pathlib,shutil
r=pathlib.Path('/opt/md-preparation'); o=pathlib.Path('/bundle')
(o/'source/include').mkdir()
for name in ('nabc.h','sff.h'):
    shutil.copyfile(r/'include'/name,o/'source/include'/name)
shutil.copyfile(r/'lib/libnabc.a',o/'libnabc.a')
shutil.copyfile(r/'LICENSE',o/'source/COPYING')
'''
    subprocess.run(['apptainer','exec','--cleanenv','--containall','--bind',f'{out}:/bundle:rw',str(a.image.resolve()),'/opt/md-preparation/bin/python','-c',extract],check=True)
    if digest(out/'libnabc.a') != LIB_SHA:
        raise RuntimeError('managed library differs')
    # Only fd_helix.c.o is pulled from the static archive; all other library code
    # is unreferenced. Keep the exact used object in the receipt/bundle.
    obj = subprocess.check_output(['ar','p',str(out/'libnabc.a'),'fd_helix.c.o'])
    (out/'fd_helix.c.o').write_bytes(obj)
    flags = ['-O2','-static','-Wl,--build-id=none']
    cmd = ['gcc',*flags,'-I',str(src/'include'),str(src/'amber_fd_helix.c'),str(out/'libnabc.a'),'-lm','-o',str(out/'amber-fd-helix')]
    subprocess.run(cmd,check=True)
    # Independently compile upstream corresponding source. It must reproduce the
    # exact native stdout for all bases/forms, not merely a plausible helix.
    subprocess.run(['gcc',*flags,'-I',str(src/'include'),str(src/'amber_fd_helix.c'),str(src/'fd_helix.c'),'-lm','-o',str(out/'source-rebuilt')],check=True)
    cases = [('abdna','ACGT'),('adna','ACGT'),('arna','ACGU'),('abdna','A'),('arna','U')]
    native = []
    for form,seq in cases:
        expected = subprocess.check_output([str(out/'amber-fd-helix'),form,seq])
        actual = subprocess.check_output([str(out/'source-rebuilt'),form,seq])
        if expected != actual:
            raise RuntimeError('corresponding source differs from installed native geometry')
        name = f'{form}-{seq}.pdb'
        (out/name).write_bytes(expected)
        native.append(dict(form=form,sequence=seq,sha256=hashlib.sha256(expected).hexdigest()))
    # Do not redistribute the rest of AmberTools' archive: only the actually
    # linked object, with its full corresponding preferred source, is retained.
    (out/'libnabc.a').unlink()
    receipt = dict(schema_version='bms.amber-fd-helix-build.v1',ambertools_version='24.8',
                   image_sha256=IMAGE_SHA,library_sha256=LIB_SHA,source_archive_sha256=SOURCE_SHA,
                   native_object_sha256=digest(out/'fd_helix.c.o'),
                   wrapper_sha256=digest(out/'amber-fd-helix'),
                   compiler=subprocess.check_output(['gcc','--version'],text=True).splitlines()[0],
                   toolchain_sha256={name:digest(subprocess.check_output(['gcc',f'-print-file-name={name}'],text=True).strip())
                                     for name in ('libc.a','libm.a','libgcc.a','libgcc_eh.a','crt1.o','crti.o','crtn.o')},
                   compiler_sha256=digest(shutil.which('gcc')),
                   linker=subprocess.check_output(['ld','--version'],text=True).splitlines()[0],
                   flags=flags, source_url='https://ambermd.org/downloads/AmberTools24_rc5.tar.bz2',
                   cases=native,license='GPL-3.0-or-later',
                   sources={str(x.relative_to(src)):digest(x) for x in sorted(src.rglob('*')) if x.is_file()})
    (out/'receipt.json').write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n')
    print(json.dumps(receipt,indent=2))


if __name__ == '__main__':
    main()
