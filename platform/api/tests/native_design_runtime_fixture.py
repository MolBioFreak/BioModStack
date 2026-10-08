"""Explicit offline native CSV fixture; never a real model-acceptance result."""
import csv
from pathlib import Path
import subprocess
from Bio.SeqUtils import seq1
from test_frustrampnn_component_phase3 import _mock_v2_runtime, AA_ORDER


def offline_worker_env(root, env):
    """Deny worker Python network and host SQLAlchemy database connections."""
    guard = root/'offline_guard'
    guard.mkdir()
    (guard/'sitecustomize.py').write_text('''import socket

def denied(*args, **kwargs):
    raise RuntimeError("offline worker fixture denies host network/database access")
socket.socket.connect = denied
socket.socket.connect_ex = denied
socket.create_connection = denied
# Canonical native statistics imports ORM type definitions, but cannot connect
# to host persistence. The component's file-local sqlite ledger remains usable.
import sqlalchemy.engine, sqlalchemy.ext.asyncio
sqlalchemy.engine.Engine.connect = denied
sqlalchemy.ext.asyncio.AsyncEngine.connect = denied
''')
    return {**env, 'PYTHONPATH': str(guard)}


def patch_native_runtime(component, patch, root):
    _mock_v2_runtime(component, patch, root)
    from services.frustrampnn import runtime

    def execute(invocation, _pinned, **_kwargs):
        argv = list(invocation.argv)
        if '--manifest' in argv:
            # Execute the real batch adapter and terminal-evidence writer. Only
            # FrustraMPNN.from_pretrained/predict_batch are scientific fixtures.
            import pandas as pd
            from types import SimpleNamespace
            import run_frustrampnn_predict_batch as adapter
            class Model:
                @classmethod
                def from_pretrained(cls, checkpoint, *, device):
                    return cls()
                def predict_batch(self, paths, *, chains, show_progress):
                    rows = []
                    for path in paths:
                        residues = {}
                        for line in Path(path).read_text().splitlines():
                            if line.startswith('ATOM  '):
                                residues.setdefault(line[21], {}).setdefault(line[22:27], seq1(line[17:20]))
                        for chain, positions in residues.items():
                            for position, wt in enumerate(positions.values()):
                                for aa in AA_ORDER:
                                    rows.append(dict(frustration_pred=0.0, position=position, wildtype=wt, mutation=aa, chain=chain, pdb=Path(path).stem))
                    return pd.DataFrame(rows)
            adapter.run_batch(manifest_path=argv[argv.index('--manifest')+1],
                output_dir=argv[argv.index('--output-dir')+1], frustrampnn_module=SimpleNamespace(FrustraMPNN=Model))
            return subprocess.CompletedProcess(argv, 0)
        binds = [argv[i+1] for i, value in enumerate(argv) if value == '--bind']
        source = Path(next(b.split(':', 1)[0] for b in binds if b.endswith(':/bms/input/normalized.pdb:ro')))
        output = Path(next(b.split(':', 1)[0] for b in binds if b.endswith(':/bms/output:rw')))
        chains = argv[argv.index('--chains')+1].split(',') if '--chains' in argv else None
        positions = set(map(int, argv[argv.index('--positions')+1].split(','))) if '--positions' in argv else None
        residues = {}
        for line in source.read_text().splitlines():
            if line.startswith('ATOM  '):
                residues.setdefault(line[21], {}).setdefault(line[22:27], seq1(line[17:20]))
        with (output/Path(argv[argv.index('--output')+1]).name).open('w') as handle:
            writer = csv.DictWriter(handle, fieldnames=['frustration_pred','position','wildtype','mutation','chain','pdb'])
            writer.writeheader()
            for chain, rows in residues.items():
                if chains is not None and chain not in chains:
                    continue
                for position, wt in enumerate(rows.values()):
                    if positions is not None and position not in positions:
                        continue
                    for aa in AA_ORDER:
                        writer.writerow(dict(frustration_pred=0.0, position=position, wildtype=wt, mutation=aa, chain=chain, pdb='normalized'))
        return subprocess.CompletedProcess(argv, 0)

    patch.setattr(runtime, 'execute_frustrampnn', execute)
