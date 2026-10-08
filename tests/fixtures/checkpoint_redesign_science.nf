// Synthetic transport contracts only. These are NOT model-native inference.
process PrepProteinLocalMPNN {
 input:
 tuple path(pdb), path(metrics)
 path(manifest)
 output: path(pdb), emit: pdbs
 script: 'true'
}
process RunMPNN {
 input: path(pdbs)
 output: tuple path('sequence.pdb'), path('sequence.json'), emit: pdbs_jsons
 script:
 """
 test '${params.fixture_fail ?: ''}' != 'RunMPNN'
 cp ${pdbs} sequence.pdb
 printf '{}' > sequence.json
 """
}
process FilterMPNN {
 input: tuple path(pdb), path(metrics)
 output: path(pdb), emit: pdbs
 script:
 """
 test '${params.fixture_fail ?: ''}' != 'FilterMPNN'
 """
}
process ResolveProteinLocalRegion {
 input: path seed
 output:
 path('resolved_design_chain.pdb'), emit: seed_pdb
 path('region_manifest.json'), emit: manifest
 script:
 """
 test '${params.fixture_fail ?: ''}' != 'ResolveProteinLocalRegion'
 cp ${seed} resolved_design_chain.pdb
 printf '{}' > region_manifest.json
 """
}
process PrepProteinLocalRFD3Input {
 input:
 path seed
 path manifest
 output: tuple val('design0'), path(seed), emit: input_json
 script: 'true'
}
process RunRFD3 {
 input: tuple val(id), path(seed)
 output: tuple path('backbone.pdb'), path('backbone.json'), emit: structures_metadata
 script:
 """
 test '${params.fixture_fail ?: ''}' != 'RunRFD3'
 cp ${seed} backbone.pdb
 printf '{}' > backbone.json
 """
}
process FilterRFD3 {
 input: tuple path(pdb), path(metrics)
 output:
 tuple path(pdb), path(metrics), emit: structures_metadata
 path('filter.json'), emit: stage_receipt
 script:
 """
 test '${params.fixture_fail ?: ''}' != 'FilterRFD3'
 printf '{}' > filter.json
 """
}
process MergeProteinLocalComplexes {
 input:
 tuple path(pdb), path(metrics)
 path(seed)
 path(manifest)
 output: tuple path(pdb), path(metrics), emit: structures_metadata
 script: 'true'
}
process PrepProteinLocalFAMPNN {
 input:
 tuple path(pdb), path(metrics)
 path(manifest)
 output:
 path(pdb), emit: pdbs
 path('fampnn.csv'), emit: csv
 script: "printf 'name,value\\nfixture,1\\n' > fampnn.csv"
}
process RunFAMPNN {
 input:
 tuple val(id), path(pdbs), path(csv), val(gpu)
 val(chains)
 val(policy)
 output: tuple path('sequence.pdb'), path('sequence.json'), emit: pdbs_jsons
 script:
 """
 test '${params.fixture_fail ?: ''}' != 'RunFAMPNN'
 cp ${pdbs} sequence.pdb
 printf '{}' > sequence.json
 """
}
process FilterFAMPNN {
 input: tuple path(pdb), path(metrics)
 output: path(pdb), emit: pdbs
 script:
 """
 test '${params.fixture_fail ?: ''}' != 'FilterFAMPNN'
 """
}
process PrepareProteinLocalValidatorInput {
 input: tuple val(meta), path(pdb)
 output: tuple val(meta), path(pdb), path('contract.json'), path('input.json'), emit: prepared
 script:
 """
 printf '{}' > contract.json
 printf '{}' > input.json
 """
}
process ESMFold2FromPdb {
 input: tuple val(meta), path(pdb), val(candidate)
 output: tuple val(meta), val('esmfold2'), path('prediction.cif'), path('metrics.json'), emit: typed_results
 script:
 """
 test '${params.fixture_fail ?: ''}' != 'ESMFold2FromPdb'
 printf 'data_SYNTHETIC_NOT_INFERENCE' > prediction.cif
 printf '{}' > metrics.json
 """
}
