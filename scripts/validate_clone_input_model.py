#!/usr/bin/env python3
"""Report observed read-group models alongside the declared native override.

Missing or conflicting provenance is informational, not an admission condition.
Input parsing and native samtools failures remain errors. No RG is fabricated.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable

MODEL = re.compile(r'(?:^|[\s;])basecall_model=([^\s;]+)')


def inspect_records(lines: Iterable[str], expected_model: str) -> dict:
    groups={};used=set();observed=set();count=0;unknown=0
    for line in lines:
        fields=line.rstrip('\r\n').split('\t')
        if line.startswith('@RG\t'):
            tags={field[:2]:field[3:] for field in fields[1:] if len(field)>3 and field[2]==':'}
            identity=tags.get('ID','')
            if not identity or identity in groups:
                raise ValueError('CLONE_INPUT_READ_GROUP_INVALID')
            models=MODEL.findall(tags.get('DS',''))
            groups[identity]=models[0] if len(models)==1 else None
        elif line.startswith('@'):
            continue
        elif line.strip():
            if len(fields)<11:
                raise ValueError('CLONE_INPUT_SAM_INVALID')
            flag=int(fields[1])
            if flag & (0x100|0x800):
                continue
            count+=1
            ids=[field[5:] for field in fields[11:] if field.startswith('RG:Z:')]
            if len(ids)!=1 or ids[0] not in groups or groups[ids[0]] is None:
                unknown+=1
                continue
            observed.add(groups[ids[0]])
            used.add(ids[0])
    return {'status':'valid','expected_model':expected_model,'declared_model':expected_model,
            'observed_models':sorted(observed),'unknown_primary_reads':unknown,
            'model_conflict':bool(observed - {expected_model}),'primary_reads_checked':count,
            'read_groups':sorted(used),'evidence_basis':'observed_read_groups' if observed else 'unknown',
            'instrument_authenticity_verified':False}


def digest(path: Path) -> str:
    value=hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b''): value.update(chunk)
    return value.hexdigest()


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bam',type=Path,required=True)
    parser.add_argument('--expected-model',required=True)
    parser.add_argument('--samtools',default='samtools')
    parser.add_argument('--runtime-lock',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    result={'schema':'biomodstack.clone_input_model.v1','status':'invalid','expected_model':args.expected_model}
    try:
        before=digest(args.bam)
        command=[args.samtools]
        if args.runtime_lock:
            from lib.container_runtime import container_executable
            lock=json.loads(args.runtime_lock.read_text())
            image=next(item for item in lock['containers']['images'] if 'wf-clone-validation:' in item['uri'])
            cache=Path(lock['containers']['cache_dir'])
            if not cache.is_absolute(): cache=(args.runtime_lock.parent/cache).resolve()
            command=[container_executable() or 'apptainer','exec','--bind',f'{args.bam.resolve().parent}:{args.bam.resolve().parent}:ro',
                     str(cache/image['cache_file']),args.samtools]
        with tempfile.TemporaryFile(mode='w+t') as error:
            process=subprocess.Popen([*command,'view','-h',str(args.bam.resolve())],stdout=subprocess.PIPE,stderr=error,text=True)
            try:
                assert process.stdout is not None
                result.update(inspect_records(process.stdout,args.expected_model))
                code=process.wait()
                if code:
                    error.seek(0)
                    raise ValueError(f'CLONE_INPUT_MODEL_SAMTOOLS_FAILED: {code}: {error.read(2000)}')
            finally:
                if process.poll() is None:
                    process.terminate()
                    try: process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill();process.wait()
                if process.stdout is not None: process.stdout.close()
        if digest(args.bam)!=before:
            raise ValueError('CLONE_INPUT_CHANGED_DURING_PROVENANCE_CHECK')
        result['source_bam_sha256']=before
    except (OSError,ValueError) as exc:
        result.update(status='invalid',reason=str(exc))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    if result['status']!='valid':
        print(result.get('reason','clone input model validation failed'),file=sys.stderr)
        return 2
    return 0


if __name__=='__main__':
    raise SystemExit(main())
