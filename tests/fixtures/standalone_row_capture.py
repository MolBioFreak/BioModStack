"""Inert Boltz/Protenix executables; consume the actual task's native input."""
import json
from pathlib import Path
import sys
import yaml

runner, *args = sys.argv[1:]
if runner.endswith('run_boltz_wrapper.py'):
    path, = Path(args[args.index('predict')+1]).glob('*.yaml')
    name = path.stem
    sequence = yaml.safe_load(path.read_text())['sequences'][0]['protein']['sequence']
    output = Path('boltz_results_yamls/predictions') / name / f'{name}_model_0.pdb'
else:
    path = Path(args[args.index('--input') + 1])
    row, = json.loads(path.read_text())
    name = row['name']
    sequence = row['sequences'][0]['proteinChain']['sequence']
    output = Path(args[args.index('--out_dir') + 1]) / name / 'seed_42' / f'{name}_sample_0.cif'
    output.parent.mkdir(parents=True)
    output.with_name(f'{name}_full_data_sample_0.json').write_text('{}')
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text('INERT TRANSPORT FIXTURE ' + name + '\n')
Path('native.json').write_text(json.dumps(dict(name=name, sequence=sequence, argv=args, input=str(path))))
