import yaml
from pathlib import Path

inputs_yaml = Path("platform/api/config/inputs.yaml")
ligands_dir = Path("platform/api/inputs/ligands")

with open(inputs_yaml, 'r') as f:
    config = yaml.safe_load(f)

# Ensure preset_pdbs exists
if 'preset_pdbs' not in config:
    config['preset_pdbs'] = []

# Get existing IDs
existing_ids = {item['id'] for item in config['preset_pdbs']}

# Add new oligos
categories = {
    'ssDNA': [],
    'dsDNA': [],
    'NTP': []
}

for pdb in sorted(ligands_dir.glob("*.pdb")):
    name = pdb.stem
    if name in existing_ids:
        continue
    
    entry = {
        'id': name,
        'name': name.replace('_', ' '),
        'path': str(pdb.absolute()),
        'description': f"Oligonucleotide {name}",
        'category': 'Oligonucleotides'
    }
    
    if 'dsDNA' in name:
        entry['category'] = 'dsDNA Oligos'
    elif 'ssDNA' in name:
        entry['category'] = 'ssDNA Oligos'
    elif 'TP' in name: # NTPs
        entry['category'] = 'Nucleotides'
        
    config['preset_pdbs'].append(entry)

# Write back
with open(inputs_yaml, 'w') as f:
    yaml.dump(config, f, sort_keys=False, default_flow_style=False)

print(f"Added {len(config['preset_pdbs']) - len(existing_ids)} new entries to inputs.yaml")
