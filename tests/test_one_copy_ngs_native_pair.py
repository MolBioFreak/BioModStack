"""Tiny generated POD5 conversion in the readonly installed raw-signal image.
Candidate validator/lookup source is explicitly mounted; not a baked-image claim.
"""
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


SCRIPT = r'''
import argparse, array, hashlib, json, os, socket, subprocess, sys, threading, uuid
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pod5
sys.path.insert(0, '/candidate')
import ont_raw_signal_validate as validator
import ont_raw_signal_lookup as lookup
root = Path('/fixture')
info = pod5.RunInfo(
 acquisition_id='one-copy-tiny', acquisition_start_time=datetime(2020,1,1,tzinfo=timezone.utc),
 adc_max=8191, adc_min=-8192, context_tags={'sample_frequency':'4000'}, experiment_name='inert-fixture',
 flow_cell_id='fixture', flow_cell_product_code='FLO-MIN114', protocol_name='fixture',
 protocol_run_id='fixture', protocol_start_time=datetime(2020,1,1,tzinfo=timezone.utc),
 sample_id='fixture', sample_rate=4000, sequencing_kit='SQK-LSK114',
 sequencer_position='fixture', sequencer_position_type='MinION', software='fixture',
 system_name='fixture', system_type='fixture', tracking_id={})
read_id = uuid.UUID('00000000-0000-4000-8000-000000000001')
read = pod5.Read(read_id=read_id, pore=pod5.Pore(channel=1,well=1,pore_type='not_set'),
 calibration=pod5.Calibration(offset=0,scale=1), read_number=1,start_sample=0,
 median_before=0,end_reason=pod5.EndReason(pod5.EndReasonEnum.UNKNOWN,False),run_info=info,
 signal=np.array([1,2,3,4], dtype=np.int16))
source = root/'source.pod5'
with pod5.Writer(source) as writer: writer.add_read(read)
records, _, _ = validator._pod5_records([source])
fingerprint = records[str(read_id)]['group']
blow5 = root/(fingerprint+'.blow5')
index = Path(str(blow5)+'.idx')
subprocess.run(['blue-crab','p2s','-c','zstd','-s','svb-zd','--iop','1','--threads','1','--batchsize','1',str(source),'-o',str(blow5)], check=True)
subprocess.run(['slow5tools','index',str(blow5)], check=True)
s = source.stat(); r = root.stat()
args=argparse.Namespace(pod5=[source], expected_sha256=[hashlib.sha256(source.read_bytes()).hexdigest()], expected_size=[s.st_size], expected_device=[s.st_dev], expected_inode=[s.st_ino], expected_mtime_ns=[s.st_mtime_ns], expected_ctime_ns=[s.st_ctime_ns], governed_root=[root],expected_root_device=[r.st_dev],expected_root_inode=[r.st_ino], received_fds=[], blow5=[blow5],index=[index],routing=root/'routing.json',metrics=root/'metrics.jsonl')
receipt=validator.semantic_validate(args)
assert receipt['retained_indexed_pair'] is True
assert receipt['indexed_lookup_count']==1
for key,path in [('blow5',blow5),('index',index)]:
 st=path.stat(); expected=receipt['output_identities'][fingerprint][key]
 assert st.st_mode & 0o222 == 0
 assert st.st_ino==expected['inode'] and st.st_ctime_ns==expected['ctime_ns']
# Full lookup CLI with genuine SCM_RIGHTS and the actual native indexed reader.
fds=[os.open(root,os.O_RDONLY),os.open(blow5,os.O_RDONLY),os.open(root,os.O_RDONLY),os.open(index,os.O_RDONLY)]
sockpath=root/'source.sock'
server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); server.bind(str(sockpath)); server.listen(1)
def serve():
 conn,_=server.accept()
 with conn: conn.sendmsg([b'F'],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',fds))])
 server.close()
thread=threading.Thread(target=serve, daemon=True); thread.start()
argv=['lookup','--blow5','/not-mounted/source.blow5','--index','/not-mounted/source.blow5.idx','--max-samples','100','--fd-socket',str(sockpath),'--read-id',str(read_id),'--output',str(root/'waveform.json'),'--retained-pair']
for key,path in [('blow5',blow5),('index',index)]:
 ident=receipt['output_identities'][fingerprint][key]
 for field,value in ident.items():
  option='size' if field=='bytes' else field.replace('_','-')
  argv.extend([f'--expected-{key}-{option}',str(value)])
 argv.extend([f'--expected-{key}-root-device',str(r.st_dev),f'--expected-{key}-root-inode',str(r.st_ino)])
sys.argv=argv
assert lookup.main()==0
thread.join(5)
for fd in fds: os.close(fd)
wave=json.loads((root/'waveform.json').read_text())
assert wave['samples']==[1.,2.,3.,4.]
for key,path in [('blow5',blow5),('index',index)]:
 expected=receipt['output_identities'][fingerprint][key]
 assert path.stat().st_ino==expected['inode'] and path.stat().st_ctime_ns==expected['ctime_ns']
assert not list(root.glob('.ont-waveform-*'))
(root/'evidence.json').write_text(json.dumps({'semantic':receipt,'waveform':wave},sort_keys=True))
'''


def test_native_pod5_derived_retained_pair_and_indexed_waveform(tmp_path):
    image = os.environ.get('ONE_COPY_RAW_IMAGE')
    if not image:
        pytest.skip('explicit installed raw-signal image required')
    script = tmp_path/'qualify.py'
    script.write_text(SCRIPT)
    command = ['docker','run','--rm','--pull=never','--network=none','--read-only',
               '--user',f'{os.getuid()}:{os.getgid()}',
               '--mount',f'type=bind,src={tmp_path},dst=/fixture',
               '--mount',f'type=bind,src={ROOT}/scripts,dst=/candidate,readonly',
               '--env','TMPDIR=/fixture','--env','PYTHONDONTWRITEBYTECODE=1',
               '--workdir','/fixture','--entrypoint','python3',image,'/fixture/qualify.py']
    result=subprocess.run(command,capture_output=True,text=True,timeout=120)
    assert result.returncode==0, result.stdout+result.stderr
    assert (tmp_path/'evidence.json').is_file()
