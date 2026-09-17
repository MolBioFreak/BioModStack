#!/usr/bin/env python3
"""Reject clone polishing when primary read RG metadata contradicts/is unbound to its model.

Read-group metadata is input provenance, not proof of instrument authenticity.
No model is inferred from a filename, a user override, or a matching model path.
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
    groups={};used=set();count=0
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
                raise ValueError('CLONE_INPUT_MODEL_UNBOUND: primary reads require model-bearing RG metadata')
            model=groups[ids[0]]
            if model!=expected_model:
                raise ValueError(f'CLONE_INPUT_MODEL_MISMATCH: expected {expected_model}, read group declares {model}')
            used.add(ids[0])
    if not count:
        raise ValueError('CLONE_INPUT_NO_PRIMARY_READS')
    return {'status':'valid','expected_model':expected_model,'primary_reads_checked':count,
            'read_groups':sorted(used),'evidence_basis':'primary_read_RG_to_header_DS_basecall_model',
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
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    result={'schema':'biomodstack.clone_input_model.v1','status':'invalid','expected_model':args.expected_model}
    try:
        before=digest(args.bam)
        with tempfile.TemporaryFile(mode='w+t') as error:
            process=subprocess.Popen([args.samtools,'view','-h',str(args.bam)],stdout=subprocess.PIPE,stderr=error,text=True)
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
