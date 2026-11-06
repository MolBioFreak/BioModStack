"""Installed Protenix chemistry deserialization and atom construction, no inference."""
from pathlib import Path
import json
import sys
from protenix.data.inference.json_to_feature import SampleDictToFeatures

root = Path(sys.argv[1])
results = []
for path in sorted(root.glob('protenix-*.json')):
    document = json.loads(path.read_text())['document']
    subject = SampleDictToFeatures(document)
    atoms = subject.get_atom_array()
    expected = document.get('covalent_bonds', [])
    assert subject.input_dict.get('covalent_bonds', []) == expected
    results.append({'file': path.name, 'atoms': len(atoms), 'bonds': expected})
assert len(results) == 18
(root / 'native-protenix-results.json').write_text(json.dumps(results, indent=2))
print('PASS 18 installed Protenix native chemistry/atom construction cases; no inference')
