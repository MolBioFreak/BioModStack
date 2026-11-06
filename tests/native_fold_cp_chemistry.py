"""Real RDKit file decoding -> installed Fold-CP schema; no model/checkpoint."""
import sys,tempfile,json
from pathlib import Path
from rdkit import Chem
from boltz.data.parse.schema import parse_boltz_schema
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from fold_cp_input import normalize_ligand_files
with tempfile.TemporaryDirectory() as td:
    root=Path(td)
    molecule=Chem.MolFromSmiles('C[C@H](O)C(=O)[O-]')
    path=root/'ligand.sdf'
    with Chem.SDWriter(str(path)) as writer:
        writer.write(molecule)
    document={'version':1,'sequences':[{'ligand':{'id':['L'],'path':'ligand.sdf'}}]}
    import yaml
    original = root / 'input.yaml'
    original.write_text(yaml.safe_dump(document))
    original_bytes = original.read_bytes()
    staged = root / 'staged.yaml'
    staged.write_bytes(original_bytes)
    repository = Path(__file__).resolve().parents[1]
    module = (repository / 'modules/boltz_cp_experimental.nf').read_text()
    marker = module.index('from lib.portable_inputs import bind_native_document')
    start = module.rfind('import sys\n', 0, marker)
    end = module.index('\nPY', marker)
    sys.argv = ['prepare', str(repository), str(staged), str(original)]
    exec(compile(module[start:end], 'RunBoltzCPExperimental-input-preparation', 'exec'), {})
    assert original.read_bytes() == original_bytes
    bound = yaml.safe_load(staged.read_text())
    smiles=bound['sequences'][0]['ligand']['smiles']
    assert Chem.MolToSmiles(Chem.MolFromSmiles(smiles))==Chem.MolToSmiles(molecule)
    result=parse_boltz_schema('probe',bound,ccd={},mol_dir=root,boltz_2=True)
    assert len(result.structure.chains)==1
    assert len(result.structure.atoms)==molecule.GetNumAtoms()
    print(json.dumps({'smiles':smiles,'chains':len(result.structure.chains),'atoms':len(result.structure.atoms)}))
print('PASS actual native ligand parser, charge/stereochemistry retained')
