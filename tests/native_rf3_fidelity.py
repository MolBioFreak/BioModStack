import json,sys,tempfile
from pathlib import Path
import numpy as np,torch
from omegaconf import OmegaConf
from biotite.structure import AtomArray
from rf3.utils.predicted_error import compile_af3_style_confidence_outputs,NHEAVY
from rf3.inference_engines.rf3 import RF3Output,dump_top_ranked_outputs,dump_ranking_scores
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from lib.filtering.rf3_association import export_pairs, archive_top_aliases
from lib.filtering.metrics import extract_confidence_metrics
from metadata_converter import RF3MetadataConverter
# Artificial logits/coordinates exercise real native confidence and serialization,
# not inference and not model-quality evidence.
a=AtomArray(2);a.chain_id=['Z','B'];a.res_id=[11,42];a.res_name=['ALA','GLY'];a.atom_name=['CA','CA'];a.element=['C','C'];a.coord=np.array([[0,0,0],[3,0,0]])
loss=OmegaConf.load('/usr/local/lib/python3.12/dist-packages/rf3/configs/trainer/loss/losses/confidence_loss.yaml')
real=torch.zeros((2,NHEAVY),dtype=torch.bool);real[:,0]=True
result=compile_af3_style_confidence_outputs(torch.zeros((1,2,NHEAVY*loss.plddt.n_bins)),torch.zeros((1,2,2,loss.pae.n_bins)),torch.zeros((1,2,2,loss.pde.n_bins)),np.array(['Z','B']),real,a,loss)
summary=result['summary_confidences'];full=result['confidences'];summary.update(ptm=.8,iptm=.6,ranking_score=.64)
metrics=extract_confidence_metrics(summary,1)
assert metrics['plddt']==50 and metrics['pae']==16 and metrics['plddt_units']=='percent',metrics
assert extract_confidence_metrics({'overall_plddt':0,'overall_pae':0},1)['plddt']==0
with tempfile.TemporaryDirectory() as td:
 root=Path(td)/'native';outs=[]
 for i in range(2):
  o=RF3Output('fold_1_seq_2',a,summary,full,i,0)
  o.dump(root/'fold_1_seq_2'/f'seed-0_sample-{i}');outs.append(o)
 dump_top_ranked_outputs(outs,root/'fold_1_seq_2','fold_1_seq_2')
 dump_ranking_scores(outs,root/'fold_1_seq_2','fold_1_seq_2')
 export_pairs(root,Path(td)/'bound')
 files=sorted(p.name for p in (Path(td)/'bound').glob('*.cif.gz'))
 assert len(files)==2,files
 assert len(list((Path(td)/'bound/native_artifacts').rglob('*ranking_scores.csv')))==1
 records=[list(RF3MetadataConverter()._parse_metadata(p))[0] for p in (Path(td)/'bound').glob('*summary_confidences.json')]
 assert {r['rf3_sample_idx'] for r in records}=={0,1}
 assert all(r['rf3_plddt']==50 and r['rf3_pae']==16 for r in records)
 assert all('not_ptm' in r['rf3_native_confidence_semantics']['chain_ptm'] for r in records)
 assert all('rf3_chain_ptm' not in r and 'rf3_chain_mean_plddt_fraction' in r for r in records)
 archive_top_aliases(root, Path(td)/'legacy_archive')
 assert len(list(root.rglob('*_model.cif'))) == 2
 assert len(list((Path(td)/'legacy_archive').rglob('*_model.cif'))) == 1
 print(json.dumps({'native_summary':summary,'filter_metrics':metrics,'sample_files':files,'records':records},indent=2))
print('PASS real RF3 native confidence -> dump/rank/alias -> export -> converter/filter; no model instantiated')
