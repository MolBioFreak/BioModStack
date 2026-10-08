// Synthetic transport-only scientific producers for the real PPIFLOW DAG test.
// No fixture output represents native inference, energies, or scientific acceptance.
process IdentifyAnchorResidues {
    input:
    tuple val(meta), path(seed)
    output:
    tuple val(meta), path(seed), path('seed_enriched.pdb'), path('anchors.json'), path('positions.txt'), path('cdr.txt'), path('loops.json'), emit: anchor_inputs
    tuple val(meta), path('interface.json'), emit: interface_scores
    tuple val(meta), path('rotamer.json'), emit: rotamer_enrichment
    script:
    """
    test '${params.fixture_fail ?: ''}' != 'IdentifyAnchorResidues'
    cp '${seed}' seed_enriched.pdb
    printf '{"anchor_count":1}' > anchors.json
    printf '{}' > interface.json
    printf '{}' > rotamer.json
    printf '{}' > loops.json
    printf 'H1' > positions.txt
    printf 'H1' > cdr.txt
    """
}
process RunPartialFlow {
    input:
    tuple val(meta), path(seed), path(enriched), path(anchors), path(positions), path(cdr), path(loops)
    output:
    tuple val(meta), path('backbones'), path('manifest.json'), emit: backbones
    script:
    """
    test '${params.fixture_fail ?: ''}' != 'RunPartialFlow'
    mkdir backbones
    cp '${seed}' backbones/seed_sample0.pdb
    python3 -c 'import json; from pathlib import Path; Path("manifest.json").write_text(json.dumps([{"sample_index":0,"path":str(Path("backbones/seed_sample0.pdb").resolve())}]))'
    """
}
process ScorePartialFlowImprovement {
    input:
    tuple val(meta), path(seed), path(backbone), path(positions), path(loops)
    output:
    tuple val(meta), path('seed_sample0_score.json'), emit: scores
    script:
    """
    test '${params.fixture_fail ?: ''}' != 'ScorePartialFlowImprovement'
    printf '{"fixture":"not scientific energy"}' > seed_sample0_score.json
    """
}
process FilterByMaturation {
    input:
    tuple val(meta), path(pdbs), path(scores)
    output:
    tuple val(meta), path('filtered/*.pdb'), emit: pdbs
    path('filter.json'), emit: filter_reports
    script:
    """
    test '${params.fixture_fail ?: ''}' != 'FilterByMaturation'
    mkdir filtered
    cp ${pdbs} filtered/
    printf '{"fixture":"filter transport only"}' > filter.json
    """
}
