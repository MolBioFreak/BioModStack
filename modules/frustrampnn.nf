process FrustrampnnQC {
    tag "${meta.id}"
    label 'process_gpu'
    container "${params.container_dir}/frustrampnn.sif"
    containerOptions "--nv"
    
    publishDir "${params.out_dir}/frustration", mode: 'copy'
    
    input:
    tuple val(meta), path(pdb)
    
    output:
    tuple val(meta), path("${meta.id}_frustration.csv"), emit: frustration
    tuple val(meta), path("${meta.id}_summary.json"), emit: summary
    
    script:
    """
    frustrampnn predict --pdb ${pdb} --checkpoint /opt/frustrampnn_weights/megascale.ckpt --output ${meta.id}_frustration.csv
    python3 -c "
import pandas as pd, json
df = pd.read_csv('${meta.id}_frustration.csv')
pos = df.groupby(['position','chain'])['frustration_pred'].mean()
json.dump({
    'pdb': '${meta.id}',
    'n_high_frust': int((pos <= -1.0).sum()),
    'n_min_frust': int((pos >= 0.58).sum()),
    'total': len(pos),
    'pct_high_frust': round((pos <= -1.0).sum() / len(pos) * 100, 1)
}, open('${meta.id}_summary.json','w'))
"
    """
}

process AggregateFrustrationReports {
    publishDir "${params.out_dir}/frustration", mode: 'copy'
    
    input:
    path summaries
    
    output:
    path "batch_frustration_report.json"
    
    script:
    """
    python3 -c "
import json
from pathlib import Path
data = [json.load(open(f)) for f in Path('.').glob('*_summary.json')]
json.dump({
    'total_designs': len(data),
    'zero_high_frust': sum(1 for d in data if d['n_high_frust']==0),
    'avg_pct_high_frust': round(sum(d['pct_high_frust'] for d in data)/len(data), 1) if data else 0,
    'designs': data
}, open('batch_frustration_report.json','w'), indent=2)
"
    """
}
