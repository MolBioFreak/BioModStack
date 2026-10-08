// Public sequence design from a server-staged PDB. No backbone generation,
// antibody roles, structure prediction, or recursive component scheduling.
nextflow.enable.dsl=2

include { PrepFAMPNN; RunFAMPNN; FilterFAMPNN } from '../modules/fampnn.nf'
include { PrepMPNN; RunMPNN; FilterMPNN } from '../modules/proteinmpnn.nf'

// Join actual filter completion, including an observed empty selection. Maps
// keep each collected path list intact across Nextflow's combine flattening.
def selectedFiles(pdbs, jsons, logs) {
    pdbs.flatten().collect().ifEmpty([]).map { [pdbs: it] }
        .combine(jsons.flatten().collect().ifEmpty([]).map { [jsons: it] })
        .combine(logs.collect().map { [logs: it] })
        .map { p, j, l -> tuple(p.pdbs, j.jsons, l.logs) }
}

process PublishSequenceDesign {
    label 'pyrosetta_tools'
    publishDir "${params.out_dir}/results", mode: 'copy', pattern: 'sequence_design_results.json'

    input:
    path source_pdb, stageAs: 'source/*'
    path settings_json, stageAs: 'settings/*'
    tuple path(native_pdbs, stageAs: 'unfiltered/*'), path(native_jsons, stageAs: 'unfiltered/*')
    tuple path(selected_pdbs, stageAs: 'filtered/*'), path(selected_jsons, stageAs: 'filtered/*'), path(filter_logs, stageAs: 'filter_logs/*')

    output:
    path 'sequence_design_results.json', emit: index

    script:
    """
    mkdir -p unfiltered filtered
    python /scripts/sequence_design_results.py \\
        --engine '${params.sequence_design_engine}' --mode '${params.sequence_design_mode}' \\
        --source "${source_pdb}" --settings "${settings_json}" \\
        --unfiltered-dir unfiltered --filtered-dir filtered \\
        --output sequence_design_results.json
    """
}

workflow {
    def engine = params.get('sequence_design_engine')
    def mode = params.get('sequence_design_mode')
    if (!((engine == 'fampnn' && mode in ['design', 'fixed_backbone', 'binder_design']) ||
          (engine == 'proteinmpnn' && mode == 'design'))) {
        error 'Unsupported public sequence design engine/mode'
    }
    if (params.get('input_pdb') == null || params.input_pdb.toString().isEmpty()) {
        error 'Public sequence design requires input_pdb'
    }
    def source = file(params.input_pdb, checkIfExists: true)
    if (!(source instanceof java.nio.file.Path) || !source.fileName.toString().endsWith('.pdb')) {
        error 'Public sequence design requires one PDB file'
    }
    // No model metadata accompanies a public PDB. An empty path collection is
    // the native tuple's optional-JSON value (no fabricated RFD JSON/sentinel).
    def input = Channel.of(tuple(source, []))
    if (params.get('sequence_design_settings_path') == null) {
        error 'Public sequence design requires compiler-owned sequence_design_settings_path'
    }
    def settings = file(params.sequence_design_settings_path, checkIfExists: true)
    def canonical
    def selected
    if (engine == 'fampnn') {
        PrepFAMPNN(input)
        def batch = PrepFAMPNN.out.pdbs.combine(PrepFAMPNN.out.csv)
            .map { pdbs, csv -> tuple(0, FampnnAnalysisPolicy.stagePrepared(params, pdbs), csv, params.get('gpu_id') != null ? params.gpu_id : 0) }
        def analysis = FampnnAnalysisPolicy.forWorkflow(params, 'protein_design',
            mode == 'binder_design' ? 'binder_role_residues' : 'declared_protein_inputs')
        RunFAMPNN(batch, 'all_chains', analysis)
        FilterFAMPNN(RunFAMPNN.out.pdbs_jsons)
        canonical = RunFAMPNN.out.pdbs_jsons
        selected = selectedFiles(FilterFAMPNN.out.pdbs, FilterFAMPNN.out.jsons, FilterFAMPNN.out.logs)
    } else {
        PrepMPNN(input)
        RunMPNN(PrepMPNN.out.pdbs)
        FilterMPNN(RunMPNN.out.pdbs_jsons)
        canonical = RunMPNN.out.pdbs_jsons
        selected = selectedFiles(FilterMPNN.out.pdbs, FilterMPNN.out.jsons, FilterMPNN.out.logs)
    }
    PublishSequenceDesign(source, settings, canonical, selected)
    // Consume the native metadata topic unchanged. Native artifacts and filter
    // outputs are published by their owning modules; no synthetic success row.
    Channel.topic('metadata_ch_fold_seq').flatten()
        .collectFile(name: 'metadata_fold_seq.jsonl', storeDir: "${params.out_dir}/results", newLine: true)
}
