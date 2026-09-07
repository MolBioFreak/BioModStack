"""Instrumented non-model executable: argv/staging evidence, NOT model output."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('runner', root / 'scripts/run_esmfold2_inference.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
args = runner.build_parser().parse_args(sys.argv[1:])
out = Path(args.output_dir)
out.mkdir(exist_ok=True)
components = json.loads(args.complex_components_json or '[]')
msa_path = args.msa_path or (components[0].get('msa_path') if components else None)
# Exercise the real component builder and MSA loader, substituting only the
# scientific input classes/parser endpoints. No model libraries are imported.
parser_calls = []
class InstrumentedMSA:
    @staticmethod
    def read(path, fmt, **kwargs):
        data = Path(path).read_bytes()
        lines = data.decode().splitlines()
        query = next(line for line in lines if line and not line.startswith(('>', '#', '//')))
        if fmt == 'stockholm':
            query = query.split()[1]
        call = {'format': fmt, 'sha256': hashlib.sha256(data).hexdigest(),
                'parser_path': str(path), 'kwargs': kwargs}
        parser_calls.append(call)
        return type('ParsedMSA', (), {'sequences': [query], 'depth': 1, 'capture': call})()

    @staticmethod
    def from_a3m(path, **kwargs):
        return InstrumentedMSA.read(path, 'a3m', **kwargs)

    @staticmethod
    def from_stockholm(path, **kwargs):
        return InstrumentedMSA.read(path, 'stockholm', **kwargs)

class Input:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

import os
parsed = dict(vars(args))
receipt_path = os.environ.get('BMS_ESMFOLD2_EFFECTIVE_SETTINGS')
if receipt_path:
    receipt = json.loads(Path(receipt_path).read_bytes())
    args._msa_expected_hashes = {source['used_path']: source['sha256'] for source in receipt['sources']}
built, manifest = runner.build_structure_prediction_input(args, ProteinInput=Input,
    DNAInput=Input, RNAInput=Input, LigandInput=Input, StructurePredictionInput=Input, MSA=InstrumentedMSA)
associations = [{'id': item.id, 'sequence': item.sequence,
                 'msa': item.msa.capture if item.msa else None} for item in built.sequences]
(out / 'capture.json').write_text(json.dumps({'argv': sys.argv[1:], 'parsed': parsed,
    'msa_sha256': hashlib.sha256(Path(msa_path).read_bytes()).hexdigest() if msa_path else None,
    'associations': associations, 'manifest_components': manifest, 'parser_calls': parser_calls}))
for name in ['fixture.cif', 'fixture.metrics.json', 'manifest.json', 'summary.tsv']:
    (out / name).write_text('NON_MODEL_FIXTURE_ONLY\n')
