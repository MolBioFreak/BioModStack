"""Bounded CPU definition differential, not native featurizer/inference acceptance."""
import hashlib
import argparse
import json
from pathlib import Path
import sys
import urllib.request

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('scratch', type=Path, help='task-owned disposable directory for immutable definition downloads')
root = parser.parse_args().scratch.resolve()
pin = '8f7f702e46f944713fbe8ff5c566221aaa54eb0b'
root.mkdir(exist_ok=True)
files = {
    'encoding.py': 'b09460edc31a90be47c49244785f76a66059a969520f055983ac2da4f3e13be1',
    'conditions.py': 'b05effbc8a437413301752f1ea99cb5c246fae2df3eefe034ceefc927951d03b',
    'resources/aa_rna_dna_glycan_ncaa.yaml': '0efd6c84938c2d944f24587769886d4cbd0e0a248106274bff12c64dede6d3a1',
}
hashes = {}
for relative in files:
    url = f'https://raw.githubusercontent.com/RosettaCommons/RFD4-Proteina/{pin}/src/rfproteina/{relative}'
    path = root / 'rfproteina' / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    content = urllib.request.urlopen(url, timeout=30).read()
    hashes[relative] = hashlib.sha256(content).hexdigest()
    if hashes[relative] != files[relative]:
        raise ValueError(f'immutable diagnostic source drift: {relative}')
    path.write_bytes(content)
# Empty package namespace deliberately avoids the unrelated native model initializer.
(root / 'rfproteina/__init__.py').write_text('"""Diagnostic namespace only; not an inference runtime."""\n')
sys.path.insert(0, str(root))
from rfproteina.encoding import UNIFIED_ATOM37_ENCODING as native
from atomworks.ml.encoding_definitions import UNIFIED_ATOM37_ENCODING as studio
import numpy as np
ns, ss = list(native.tokens), list(studio.tokens)
shared = sorted(set(ns) & set(ss))
result = {
    'native_pin': pin, 'fetched_source_sha256': hashes,
    'native_token_count': len(ns), 'studio_token_count': len(ss),
    'native_only': sorted(set(ns)-set(ss)), 'studio_only': sorted(set(ss)-set(ns)),
    'shared_token_index_differences': {t: {'native':ns.index(t),'studio':ss.index(t)} for t in shared if ns.index(t) != ss.index(t)},
    'shared_atom_layout_differences': {t: {'native':native.token_atoms[t].tolist(), 'studio':studio.token_atoms[t].tolist()} for t in shared if not np.array_equal(native.token_atoms[t], studio.token_atoms[t])},
    'scope': 'CPU TokenEncoding definitions; native model initializer/featurizer/inference not executed',
}
# Separate processes avoid duplicate annotation registration and native-module reuse.
import rfproteina.conditions as nc
import subprocess
probe = """
import hashlib,json
from atomworks_studio_rfd4 import model
print(json.dumps({'channels':model.conditions.SEQUENCE_PROBABILITY_CHANNEL_BY_TOKEN,
                 'indices':{k:v.token_index for k,v in model.conditions.SEQUENCE_PROBABILITY_CONDITION_BY_TOKEN.items()},
                 'conditions_sha256':hashlib.sha256((model.source_root()/'conditions.py').read_bytes()).hexdigest()}))
"""
sc = json.loads(subprocess.check_output([sys.executable, '-c', probe], text=True))
result['sequence_probability_channels_equal'] = nc.SEQUENCE_PROBABILITY_CHANNEL_BY_TOKEN == sc['channels']
result['sequence_probability_token_indices_equal'] = {k:v.token_index for k,v in nc.SEQUENCE_PROBABILITY_CONDITION_BY_TOKEN.items()} == sc['indices']
result['sequence_probability_channel_count'] = len(sc['channels'])
result['studio_conditions_sha256'] = sc['conditions_sha256']
print(json.dumps(result, indent=2))
