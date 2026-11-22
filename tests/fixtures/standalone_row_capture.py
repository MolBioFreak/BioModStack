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
    output.with_name(f'{name}_summary_confidence_sample_0.json').write_text(json.dumps({'ptm': 0.5}))
    output.with_name(f'{name}_full_data_sample_0.json').write_text('{}')
output.parent.mkdir(parents=True, exist_ok=True)
if '--write_full_pae' in args:
    # Data-only pinned writer fixture: leave task staging, manifest generation,
    # per-task binding, publication and the API verifier untouched.
    import shutil
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from test_boltz_native_producer_identity import NativeIdentityTest
    fixture = NativeIdentityTest()
    fixture.setUp()
    try:
        for source in fixture.orig.iterdir():
            shutil.copyfile(source, output.parent / source.name.replace('input', name))
        ledger = Path('boltz_results_yamls/processed/structures') / (name + '.npz')
        ledger.parent.mkdir(parents=True)
        shutil.copyfile(fixture.ledger, ledger)
    finally:
        fixture.doCleanups()
else:
    output.write_text('INERT TRANSPORT FIXTURE ' + name + '\n')
Path('native.json').write_text(json.dumps(dict(name=name, sequence=sequence, argv=args, input=str(path))))
