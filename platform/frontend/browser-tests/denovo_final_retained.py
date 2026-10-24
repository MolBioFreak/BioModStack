"""Import exact retained inert native receiving rows into scratch SQLite.

Never a live database. Read-only source access; artifacts stay unchanged at
retained scratch paths. Path/custody read failures are reported, not bypassed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args()
assert a.root.resolve().is_relative_to(Path('/home/dalab/.hermes/profiles/fresh/cache/scratch'))
base=Path('/home/dalab/.hermes/profiles/fresh/cache/scratch/denovo-complete-parent/phase2/api-receiving/basetemp')
sources=[(base/'test_complete_receiving_real_e1/state.sqlite', ['jobs','job_artifacts']),
         (base/'test_partial_zero_unexpected_a2/test.db', ['jobs','rfd3_local_redesign_requests','rfd3_local_redesign_candidates','rfd3_local_redesign_artifacts'])]
manifest=[]
with sqlite3.connect(a.root/'core.db') as target:
    for source,tables in sources:
        assert source.resolve().is_relative_to(base)
        source_manifest={'source':str(source),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'tables':{},'files':[]}
        with sqlite3.connect('file:'+str(source)+'?mode=ro',uri=True) as origin:
            origin.execute('pragma query_only=ON');origin.row_factory=sqlite3.Row
            for table in tables:
                rows=origin.execute('select * from '+table).fetchall();source_manifest['tables'][table]=len(rows)
                for row in rows:
                    columns=row.keys()
                    values=dict(row)
                    if table == 'rfd3_local_redesign_artifacts' and values['role'] == 'source_structure':
                        source_path=Path(values['storage_path'])
                        destination=a.root/'data/retained-source'/source_path.name
                        destination.parent.mkdir(parents=True,exist_ok=True)
                        destination.write_bytes(source_path.read_bytes())
                        source_manifest.setdefault('storage_bindings',[]).append({'original':str(source_path),'scratch':str(destination),'sha256':hashlib.sha256(destination.read_bytes()).hexdigest()})
                        values['storage_path']=str(destination)
                    target.execute('INSERT INTO '+table+' ('+','.join('"'+c+'"' for c in columns)+') VALUES ('+','.join('?' for _ in columns)+')',tuple(values[c] for c in columns))
                    for key in ['storage_path','output_dir']:
                        if key not in columns or not row[key]:continue
                        path=Path(row[key]);files=[path] if path.is_file() else sorted(f for f in path.rglob('*') if f.is_file())
                        for f in files:
                            source_manifest['files'].append({'path':str(f),'sha256':hashlib.sha256(f.read_bytes()).hexdigest(),'bytes':f.stat().st_size})
        manifest.append(source_manifest)
(a.root/'retained-import.json').write_text(json.dumps({'scope':'Existing inert native writer captures; NOT inference; byte-unchanged read-only artifact roots','sources':manifest},indent=2))
print(json.dumps([{ 'source':x['source'],'tables':x['tables']} for x in manifest],indent=2))
