"""Run with the installed Foundry CPU interpreter; never invokes a sampler.

BMS_INSERTION_EVIDENCE optionally retains the actual native arrays/commands.
"""
import hashlib
import inspect
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]


def main():
    prepare_only = '--prepare-only' in sys.argv
    consume_prepared = '--consume-prepared' in sys.argv
    if not prepare_only:
        from rfd3.inference.input_parsing import create_atom_array_from_design_specification
        from foundry.utils.components import split_contig
    socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('network prohibited'))
    evidence=os.environ.get('BMS_INSERTION_EVIDENCE')
    with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as temporary:
        work=Path(evidence) if evidence else Path(temporary)
        work.mkdir(exist_ok=True,parents=True)
        source=work/'source.pdb'
        text=''
        for model in (1,2):
            text+=f'MODEL     {model:4d}\n'
            for i,(number,ic) in enumerate([(10,''),(12,''),(12,'A'),(12,'B'),(20,'')]):
                for j,atom in enumerate(('N','CA','C','O')):
                    x=model*100+i*5+j
                    text+=f'ATOM  {i*4+j+1:5d} {atom:>4s} GLY A{number:4d}{ic or " "}   {x:8.3f}{0:8.3f}{0:8.3f}{1:6.2f}{20:6.2f}          {atom[0]:>2s}\n'
            text+='ENDMDL\n'
        source.write_text(text+'END\n')
        source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
        rejection = None
        if not prepare_only:
            try:
                split_contig('A12B')
            except Exception as exc:
                rejection={'type':type(exc).__name__,'message':str(exc)}
            else:
                raise AssertionError('native unexpectedly accepts unlowered insertion syntax')
        cases=[]
        for coordinate,sequence,expected_coordinate,expected_sequence in [
            ('A12','A12',[1],[1]),('A12A','A12A',[2],[2]),('A12B','A12B',[3],[3]),
            ('A12A-12B','A12B',[2,3],[3]),
        ]:
            folder=work/coordinate;folder.mkdir(exist_ok=True)
            seed,manifest,native=folder/'seed.pdb',folder/'region.json',folder/'rfd3.json'
            commands=[
                [sys.executable,str(ROOT/'scripts/resolve_redesign_regions.py'),'--input_pdb',str(source),
                 '--model_number','2','--design_chains','A','--redesign_ranges',coordinate,
                 '--sequence_redesign_ranges',sequence,'--output_seed_pdb',str(seed),'--output_manifest',str(manifest)],
                [sys.executable,str(ROOT/'scripts/prep_protein_local_redesign_rfd3_input.py'),
                 '--seed-pdb',str(seed),'--manifest',str(manifest),'--output',str(native)],
            ]
            if not consume_prepared:
                for command in commands:subprocess.run(command,check=True)
            if prepare_only:
                cases.append({'coordinate':coordinate,'commands':commands})
                continue
            payload=json.loads(native.read_text())['protein_local_redesign_0']
            # Same prepared bytes, mounted into the native image's namespace.
            payload['input']=str(seed)
            array,spec=create_atom_array_from_design_specification(**payload)
            ca=array[array.atom_name=='CA']
            actual_coordinate=ca.is_motif_atom_with_fixed_coord.tolist()
            actual_sequence=ca.is_motif_atom_with_fixed_seq.tolist()
            assert actual_coordinate==[i not in expected_coordinate for i in range(5)],(coordinate,actual_coordinate)
            assert actual_sequence==[i not in expected_sequence for i in range(5)],(sequence,actual_sequence)
            assert ca.res_id.tolist()==[1,2,3,4,5]
            assert ca.src_component.tolist()==['A1','A2','A3','A4','A5']
            m=json.loads(manifest.read_text());assert m['source_mapping']['model']=='2'
            assert m['source_mapping']['original_sha256']==source_hash
            # Source MODEL1 is >100 Angstrom away; fixed MODEL2 pair differences
            # survive native's documented common-origin translation.
            fixed=[i for i in range(5) if i not in expected_coordinate]
            for i in fixed:
                for j in fixed:assert abs(float(ca.coord[i,0]-ca.coord[j,0])-(i-j)*5)<1e-4
            cases.append({'coordinate':coordinate,'sequence':sequence,
                'commands_executed_here':commands if not consume_prepared else [],
                'preparation_commands_file':'preparation-commands.json' if consume_prepared else None,
                'native':payload,'coordinate_fixed':actual_coordinate,'sequence_fixed':actual_sequence,
                'source_model':m['source_mapping']['model'],'source_regions':m['source_regions']})
        assert hashlib.sha256(source.read_bytes()).hexdigest()==source_hash
        if prepare_only:
            (work/'preparation-commands.json').write_text(json.dumps(cases,indent=2))
            return
        report={'baseline_native_rejection':rejection,'consumer':inspect.getfile(create_atom_array_from_design_specification),
            'consumer_sha256':hashlib.sha256(Path(inspect.getfile(create_atom_array_from_design_specification)).read_bytes()).hexdigest(),
            'cases':cases,'count':len(cases),'scope':'installed native CPU preparation, no inference'}
        if evidence:(work/'results.json').write_text(json.dumps(report,indent=2))
        print(json.dumps(report,indent=2))


if __name__=='__main__':main()
