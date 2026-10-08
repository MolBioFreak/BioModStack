// Offline scientific output fixtures only. Not model acceptance.
process IdentifyAnchorResidues {
 input:
 tuple val(meta), path(pdb)
 output:
 tuple val(meta), path('original.pdb'), path('enriched.pdb'), path('anchors.json'), path('positions.txt'), path('cdr.txt'), path('loops.json'), emit: anchor_inputs
 script:
 """
 cp ${pdb} original.pdb
 cp ${pdb} enriched.pdb
 printf '{"anchor_count":${params.offline_zero_anchors == true ? 0 : 1}}' > anchors.json
 printf '1' > positions.txt
 printf '1' > cdr.txt
 printf '{"H1":[1]}' > loops.json
 """
}
process RunPartialFlow {
 input:
 tuple val(meta), path(original), path(enriched), path(anchors), path(positions), path(cdr), path(loops)
 output:
 tuple val(meta), path('backbones'), path('manifest.json'), emit: backbones
 script:
 """
 mkdir backbones
 cp ${original} backbones/${meta.id}_ppiflow_0.pdb
 cp ${original} backbones/${meta.id}_ppiflow_1.pdb
 python3 - <<'PY'
import json
from pathlib import Path
Path('manifest.json').write_text(json.dumps([{'path':str(p.resolve()),'sample_index':i} for i,p in enumerate(sorted(Path('backbones').glob('*.pdb')))]))
PY
 """
}
process ScorePartialFlowImprovement {
 input:
 tuple val(meta), path(original), path(backbone), path(positions), path(loops)
 output:
 tuple val(meta), path("${meta.id}_partial_score.json"), emit: scores
 script:
 """
 printf '{"objective_score":-${meta.sample_index + 1}}' > ${meta.id}_partial_score.json
 """
}
process PrepMaturationRedesign {
 input:
 tuple val(meta), path(backbone), path(anchors), path(cdr), path(loops), path(comparisons)
 output:
 tuple val(meta), path('backbone.pdb'), path('positions.csv'), path('transport'), emit: prep
 script:
 """
 cp ${backbone} backbone.pdb
 touch positions.csv
 mkdir transport
 """
}
process RunMaturationFAMPNN {
 input:
 tuple val(meta), path(pdb), path(csv), path(transport)
 output:
 tuple val(meta), path("${meta.id}_matured.pdb"), path("${meta.id}_matured.json"), emit: redesigned
 script:
 """
 cp ${pdb} ${meta.id}_matured.pdb
 printf '{}' > ${meta.id}_matured.json
 """
}
process ScoreMaturationImprovement {
 input:
 tuple val(meta), path(original), path(matured), path(positions), path(loops)
 output:
 tuple val(meta), path("${meta.id}_score.json"), emit: scores
 script:
 """
 printf '{"objective_score":-2}' > ${meta.id}_score.json
 """
}
process FilterByMaturation {
 input:
 tuple val(meta), path(pdbs), path(scores)
 output:
 tuple val(meta), path('filtered/*.pdb'), emit: pdbs
 path 'filter.json', emit: filter_reports
 script:
 """
 mkdir filtered
 cp ${pdbs} filtered/
 printf '{"fixture":true}' > filter.json
 """
}
