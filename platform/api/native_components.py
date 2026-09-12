"""Native annotations used by the existing selected_execution_metadata producer.

No command compilation, argv parsing, biological IO or execution occurs here.
Role declarations retain the native process contracts and optional output flags.
"""
from __future__ import annotations

# source:process -> labels, input declarations, output declarations, helpers, directives
PROCESS_CONTRACTS = {
    'modules/af2.nf:RunAF2': (('AF2', 'gpu'), ('tuple val(batch_id), path(pdbs)',), ('tuple path("outputs/*.pdb"), path("*.json"), emit: pdbs_jsons', 'path ("*.json"), emit: json, topic: metadata_ch_fold_seq', 'path "*.log"'), (), ()),
    'modules/af2.nf:AlignAF2': (('pyrosetta_tools',), ('path af2_pdbs', 'path reference_pdb'), ('path "aligned/*.pdb", emit: pdbs, optional: true', 'path "alignment_${task.index}.log", emit: logs, optional: true'), (), ()),
    'modules/af2.nf:FilterAF2': (('pyrosetta_tools',), ('tuple path(pdb_files), path(json_files)',), ('path ("output/*.pdb"), emit: pdbs, optional: true', 'path "filter_af2_${task.index}.log"', 'path ("filtered.jsonl"), emit: jsonl, optional: true'), (), ()),
    'modules/af2_backprop.nf:MergeComplex': (('process_low',), ('tuple val(meta), path(antibody_pdb), path(target_pdb)',), ('tuple val(meta), path("*_complex.pdb"), emit: complex',), ('scripts/merge_complex.py',), ()),
    'modules/af2_backprop.nf:AF2_BACKPROP': (('process_gpu', 'AF2_BACKPROP'), ('tuple val(meta), path(complex_pdb)',), ('tuple val(meta), path("*_refined.pdb"), emit: refined', 'tuple val(meta), path("*_refined.json"), emit: metrics', 'path "*.log", emit: log'), ('scripts/run_af2_backprop.py',), ()),
    'modules/analysis.nf:AnalyseBestDesigns': (('pyrosetta_tools',), ('path pdbs',), ('path "best_designs.jsonl", emit: jsonl, topic: metadata_ch_fold_seq', 'path "analysis.log", emit: log'), (), ()),
    'modules/antiberty.nf:ANTIBERTY_SCORE': (('process_medium',), ('tuple val(meta), path(input_file)  // Can be FASTA or PDB',), ('tuple val(meta), path("${meta.id}_pll_scores.csv"), emit: scores', 'tuple val(meta), path("${meta.id}_embeddings.pt"), emit: embeddings, optional: true', 'path "antiberty_${meta.id}.log"'), (), ('container "${params.container_dir}/antibody_tools.sif"',)),
    'modules/antiberty.nf:ANTIBERTY_FILTER': (('process_low',), ('tuple val(meta), path(scores_csv), path(fasta)',), ('tuple val(meta), path("${meta.id}_filtered.fasta"), emit: filtered_fasta', 'tuple val(meta), path("${meta.id}_filter_report.json"), emit: report'), (), ('container "${params.container_dir}/antibody_tools.sif"',)),
    'modules/antiberty.nf:ANTIBERTY_FILTER_STRUCTURES': (('process_low',), ('tuple val(meta), path(scores_csv), path(structure_pdb)',), ('tuple val(meta), path("${meta.id}_filtered.pdb"), emit: filtered_pdb, optional: true', 'tuple val(meta), path("${meta.id}_filter_report.json"), emit: report'), (), ('container "${params.container_dir}/antibody_tools.sif"',)),
    'modules/antibody_batch.nf:BatchBoltzValidation': (('Boltz', 'gpu'), ('path pdbs', 'path msa'), ('path "*_boltzpred.pdb", emit: raw_pdbs', 'path "*_boltzpred.json", emit: raw_scores', 'path "*_boltzpred.pae.npz", emit: raw_aligned_error', 'path "original_designs/*.pdb", emit: original_designs', 'path "boltz_batch.log"'), ('scripts/extract_target_templates.py', 'scripts/prep_boltz_batch.py'), ('container "${params.container_dir}/boltz2.sif"',)),
    'modules/antibody_batch.nf:AlignBoltzValidation': (('pyrosetta_tools',), ('path raw_pdbs', 'path raw_scores', 'path raw_aligned_error', 'path original_designs'), ('path "predictions/*.pdb", emit: pdbs', 'path "predictions/*.json", emit: scores', 'path "predictions/*.npz", emit: aligned_error', 'path "alignment_batch.log"'), ('scripts/align_boltz.py',), ()),
    'modules/antibody_batch.nf:BatchProtenixValidation': (('Protenix', 'gpu'), ('path pdbs', 'path msa'), ('path "predictions/*.pdb", emit: pdbs', 'path "predictions/*.json", emit: scores', 'path "predictions/*.cif", emit: cifs, optional: true', 'path "predictions/aligned_error/*.json", emit: aligned_error, optional: true', 'path "protenix_batch.log"'), ('scripts/align_protenix.py', 'scripts/extract_target_templates.py', 'scripts/prep_protenix_batch.py', 'scripts/prepare_protenix_msa.py', 'scripts/run_protenix_inference.py'), ('container { params.protenix_container_path ?: "${params.container_dir}/protenix.sif" }',)),
    'modules/antibody_batch.nf:BatchESMFold2Validation': (('ESMFold2',), ('path pdbs', 'path msa_files'), ('path "predictions/*_esmfold2.pdb", emit: pdbs', 'path "predictions/*_esmfold2.cif", emit: cifs', 'path "predictions/*_esmfold2.metrics.json", emit: metrics', 'path "predictions/esmfold2_validation_summary.json", emit: summary', 'path "esmfold2_batch.log", emit: log'), (), ('container "${params.container_dir}/esmfold2.sif"',)),
    'modules/antibody_batch.nf:BatchImmunogenicity': (('Antiberty',), ('path pdbs',), ('path "immunogenicity_scores.csv", emit: scores',), ('scripts/batch_antiberty.py',), ('container "${params.container_dir}/antibody_tools.sif"',)),
    'modules/antibody_batch.nf:BatchStability': (('ThermoMPNN',), ('path pdbs',), ('path "stability_scores.csv", emit: scores',), (), ('container "${params.container_dir}/stability_tools.sif"',)),
    'modules/antibody_frustrampnn_parent.nf:PrepareAntibodyFrustraMPNNCandidate': (('CPU',), ('tuple val(candidate_meta), path(terminal_structure), val(settings_base64), \\', 'val(settings_sha256), val(settings_value_origin)'), ("tuple path('workflow_component_request_v3.json'), path('canonical_source.pdb'), \\", "path('frustrampnn_structure_map_v1.json'), emit: prepared"), ('scripts/prepare_frustrampnn_candidate.py',), ()),
    'modules/antibody_frustrampnn_parent.nf:PublishAntibodyFrustraMPNNCandidate': (('CPU',), ('tuple val(result_meta), path(candidate_bundle), path(result_manifest)',), ("tuple val(result_meta), path(result_manifest), path('published_*.json'), emit: published",), ('scripts/publish_frustrampnn_bundle.py',), ()),
    'modules/antibody_frustrampnn_parent.nf:AggregateAndReportAntibodyFrustraMPNN': (('CPU',), ('path published_markers',), ("path 'antibody_frustrampnn_terminal_manifest.json', emit: terminal_manifest", "path 'frustrampnn_complete.reported', emit: reported"), ('scripts/stage_reporter.py',), ()),
    'modules/antibody_frustrampnn_parent.nf:ReportAntibodyFrustraMPNNNotRequested': (('CPU',), ('val trigger',), ("tuple val(parent_status), path('antibody_frustrampnn_terminal_manifest.json'), path('frustrampnn_not_requested.reported'), emit: result",), ('scripts/stage_reporter.py',), ()),
    'modules/antibody_output_finalization.nf:FinalizeSequentialValidationOutputs': (('process_low',), ('path artifact_manifest_file',), ('path "validated_designs/*.pdb", emit: pdbs, optional: true', 'path "validated_designs/*.json", emit: scores, optional: true', 'path "validated_designs/*.npz", emit: aligned_error, optional: true', 'path "validated_designs/aligned_error/*.json", emit: aligned_error_json, optional: true', 'path "aggregation_report.json", emit: report'), ('scripts/result_ingester.py', 'scripts/stage_reporter.py'), ()),
    'modules/antibody_output_finalization.nf:FinalizeTerminalAntibodyOutputs': (('process_low',), ('path terminal_pdb_list_file',), ('path "terminal_closeout_report.json", emit: report',), ('scripts/result_ingester.py',), ()),
    'modules/antifold.nf:ANTIFOLD': (('process_gpu',), ('tuple val(meta), path(pdb_imgt)',), ('tuple val(meta), path("*_probs.csv"), emit: probabilities', 'tuple val(meta), path("*_sampled.fasta"), emit: sequences', 'path "antifold.log"'), (), ('container "${params.container_dir}/antibody_tools.sif"',)),
    'modules/boltz.nf:PrepBoltz': (('pyrosetta_tools',), ('path pdb_files',), ('path ("yamls/*.yaml"), emit: yamls',), (), ("errorStrategy { params.containsKey('plr_validator_suite_active') && params.plr_validator_suite_active == true ? 'ignore' : 'terminate' }",)),
    'modules/boltz.nf:PrepBoltzWithMSA': (('pyrosetta_tools',), ('path pdb_files',), ('path ("yamls/*.yaml"), emit: yamls', 'path ("msa/*.a3m"), emit: msas, optional: true'), ('scripts/prep_boltz_with_msa.py', 'scripts/run_local_msa.py'), ()),
    'modules/boltz.nf:RunBoltz': (('Boltz', 'gpu'), ('tuple val(batch_id), path(yamls)',), ('tuple path("predictions/*.pdb"), path("predictions/*.json"), emit: pdbs_jsons', 'path "boltz_completion.json", emit: completion', 'path ("predictions/*.npz"), emit: pae_npz, optional: true', 'path ("*.log"), emit: logs'), (), ("errorStrategy { params.containsKey('plr_validator_suite_active') && params.plr_validator_suite_active == true ? 'ignore' : 'terminate' }",)),
    'modules/boltz.nf:AlignBoltz': (('pyrosetta_tools',), ('tuple path(pdb_files), path(json_files)', 'path designs', 'val design_type'), ('tuple path("aligned/*.pdb"), path("aligned/*.json"), emit: pdbs_jsons', 'path "alignment_*.log"', 'path ("boltz_metadata_*.jsonl"), topic: metadata_ch_fold_seq'), (), ()),
    'modules/boltz.nf:FilterBoltz': (('pyrosetta_tools',), ('tuple path(pdb_files), path(json_files)',), ('path ("output/*.pdb"), emit: pdbs, optional: true', 'path ("filter_boltz_${task.index}.log"), emit: log', 'path ("filtered.jsonl"), emit: jsonl, optional: true'), (), ()),
    'modules/boltz_cp_experimental.nf:RunBoltzCPExperimental': (('BoltzCP', 'gpu'), ('path input_config',), ("path 'cp_results', emit: results_dir, optional: true", "path 'cp_results/processed', emit: processed_dir, optional: true", "path '*.log'"), (), ()),
    'modules/boltz_cp_experimental.nf:FinalizeBoltzCPExperimental': (('process_low',), ('path results_dir',), ("path 'published/*.pdb', emit: pdbs, optional: true", "path 'published/*.cif', emit: cifs, optional: true", "path 'published/*.json', emit: jsons, optional: true", "path 'published/*.npz', emit: npzs, optional: true"), (), ()),
    'modules/boltzgen.nf:PrepBoltzGenInput': (('pyrosetta_tools',), ('val ligand_smiles', 'val ntp_type', 'val scaffold_length', 'val num_designs', 'val binding_site_residues', 'val catalytic_site', 'val protein_sequence', 'val dna_template_seq', 'val dna_primer_seq', 'val secondary_structure', 'val protocol', 'val covalent_bonds', 'val nanobody_framework', 'val cdr_h1_length', 'val cdr_h2_length', 'val cdr_h3_length', 'path input_pdb', 'path ligand_pdb', 'path dna_structure', 'path target_pdb'), ('path "boltzgen_prepared", emit: yaml',), ('scripts/prep_boltzgen.py', 'scripts/lib/boltzgen_inputs.py'), ()),
    'modules/boltzgen.nf:RunBoltzGen': (('BoltzGen', 'gpu'), ('path yaml_configs',), ('path "output/designs/*.pdb", emit: pdbs, optional: true', 'path "output/designs/*.{json,npz,csv}", emit: jsons, optional: true', 'path "*.log"'), ('scripts/lib/boltzgen_inputs.py',), ()),
    'modules/boltzgen.nf:FilterBoltzGen': (('pyrosetta_tools',), ('path pdbs', 'path jsons'), ('path "filtered/*.pdb", emit: pdbs, optional: params.get(\'core_protein_scientific_contract\') == 1', 'path "filtered/*.json", emit: jsons, optional: true', 'path "filtered/filter_summary.json", emit: summary, optional: true', 'path "filtered/*.{npz,csv}", emit: native_artifacts, optional: true', 'path "*.log"'), ('scripts/filter_boltzgen.py',), ()),
    'modules/boltzgen.nf:SpawnBoltzGenJobs': (('process_low',), ('val parent_job_id', 'val total_designs', 'val designs_per_job', 'path yaml_config', 'path target_pdb', 'val mode', 'val batch_name'), ('path "spawn_boltzgen_result.json", emit: result', 'path "spawn_boltzgen.log"'), ('scripts/spawn_boltzgen_children.py',), ()),
    'modules/boltzgen.nf:WaitForBoltzGenChildren': (('process_low',), ('val parent_job_id', 'path spawn_result', 'val batch_name'), ('path "boltzgen_child_outputs.json", emit: result', 'path "wait_boltzgen.log"'), ('scripts/wait_for_children.py',), ()),
    'modules/boltzgen.nf:CollectBoltzGenOutputs': (('process_low',), ('path child_outputs_json',), ('path "collected/*.pdb", emit: pdbs, optional: true', 'path "collected/*.{json,npz,csv}", emit: jsons, optional: true', 'path "collection_manifest.json", emit: manifest'), ('scripts/child_job_utils.py',), ()),
    'modules/boltzgen.nf:AggregateBoltzGenResults': (('process_low',), ('val parent_job_id', 'path collected_pdbs', 'path collected_jsons', 'path manifest'), ('path "aggregation_report.json", emit: report',), (), ()),
    'modules/caliby.nf:RunCaliby': (('Caliby', 'gpu'), ('tuple val(meta), path(pdb_files)',), ('tuple path("results/*.pdb"), path("results/generator_*.json"), emit: pdbs_jsons', 'path("caliby_metadata_${task.index}.jsonl"), emit: metadata', 'path "*.log"'), ('scripts/prep_caliby_antibody_constraints.py', 'scripts/run_caliby_sequence_design.py'), ()),
    'modules/caliby.nf:RunCalibyBinder': (('Caliby', 'gpu'), ('path(pdb_files)',), ('path("results/*.pdb"), emit: pdbs', 'path("results/generator_*.json"), emit: jsons', 'path("caliby_metadata.jsonl"), emit: metadata', 'path("caliby_binder.log"), emit: log'), ('scripts/prep_caliby_binder_constraints.py', 'scripts/run_caliby_sequence_design.py'), ()),
    'modules/caliby.nf:FilterCaliby': (('Caliby',), ('tuple path(pdb_files), path(json_files)',), ('path("filtered_output/*.pdb"), emit: pdbs, optional: true', 'path("filtered_output/generator_*.json"), emit: jsons, optional: true', 'path("filter_caliby_${task.index}.log"), emit: logs'), ('scripts/filter_caliby.py',), ()),
    'modules/combine_metadata.nf:CombineMetadata': (('pyrosetta_tools',), ('path metadata_fold', 'path metadata_fold_seq'), ('path ("combined_metadata.csv"), emit: csv', 'path "combined_metadata.log"'), (), ()),
    'modules/compress.nf:Compress': (('pyrosetta_tools',), ('val program', 'path files'), ('path "*.tar.gz"',), (), ()),
    'modules/conformational_mapping_confornets.nf:PrepCanonicalConforNetsRequest': (('ConforNetsCanonical',), ('path request_root',), ("path 'confornets_request.json', emit: request", "path 'confornets_assets', emit: assets_dir"), ('scripts/prep_canonical_confornets_request.py',), ()),
    'modules/conformational_mapping_confornets.nf:RunCanonicalConforNets': (('ConforNetsCanonical', 'gpu'), ('path request_json', 'path assets_dir'), ("path 'confornets_results', emit: results_dir", "path 'run_confornets.log', emit: log"), (), ()),
    'modules/conformational_mapping_confornets.nf:BindCanonicalConforNetsOutputLedger': (('local_cpu',), ('path request_root', 'path native_results'), ("path 'bound_confornets_results', emit: results_dir",), ('scripts/bind_confornets_output_ledger.py',), ()),
    'modules/conformational_mapping_confornets.nf:FinalizeCanonicalConforNets': (('local_cpu',), ('path request_root', 'path native_results'), ("path 'canonical_confornets', emit: canonical_dir", "path 'canonical_confornets/cm_native_artifacts_v1.json', emit: native_manifest", "path 'canonical_confornets/cm_ensemble_v1.json', emit: ensemble_manifest"), ('scripts/finalize_confornets_conformational_mapping.py',), ()),
    'modules/conformational_mapping_frustrampnn.nf:PrepareConformationalMappingFrustraMPNNV2': (('CPU',), ('tuple val(request_id), val(backend_dir), path(request_root), path(canonical_dir)',), ("tuple val(request_id), val(backend_dir), path('frustrampnn_prepared'), \\", "path('cm_frustrampnn_preparation_manifest_v1.json'), emit: prepared"), ('scripts/prepare_conformational_mapping_frustrampnn_v2.py',), ("errorStrategy 'terminate'", 'maxRetries 0')),
    'modules/conformational_mapping_frustrampnn.nf:CanonicalConformationalAnalysisPlaneV2': (('CPU',), ('tuple val(request_id), val(backend_dir), path(request_root), path(canonical_dir)', 'path(preparation_manifest)', 'path(result_bundles)', 'path(scheduler_terminal_receipt)'), ("tuple val(request_id), path('canonical_result'), emit: canonical",), ('scripts/postprocess_conformational_mapping_frustrampnn_v2.py',), ("errorStrategy 'terminate'", 'maxRetries 0')),
    'modules/conformational_mapping_frustrampnn.nf:StageConformationalMappingFrustraMPNNResult': (('CPU',), ('tuple val(component_result), path(candidate_bundle), path(result_manifest)',), ('tuple val(component_result), path("${component_result.candidate_id}"), emit: staged',), (), ("errorStrategy 'terminate'", 'maxRetries 0')),
    'modules/conformational_mapping_import.nf:CanonicalConformationalImport': (('local_cpu',), ('tuple val(request_id), path(request_root)',), ("tuple val(request_id), path('canonical_import'), emit: canonical", "path 'canonical_import/cm_native_artifacts_v1.json', emit: native_manifest", "path 'canonical_import/cm_ensemble_v1.json', emit: ensemble_manifest"), ('scripts/finalize_import_conformational_mapping.py',), ()),
    'modules/conformational_mapping_protenix.nf:PrepareProtenixExecution': ((), ('tuple val(request_id), path(request_root)',), ("tuple val(request_id), path('protenix_preflight'), emit: prepared",), ('scripts/prepare_protenix_execution_snapshot.py', 'scripts/prepare_runtime_image_attestation.py', 'scripts/run_protenix_inference.py'), ()),
    'modules/conformational_mapping_protenix.nf:CanonicalProtenixEnsemble': (('Protenix', 'gpu'), ('tuple val(request_id), path(preflight), val(runtime_image), val(preflight_source)',), ("tuple val(request_id), path('canonical_protenix'), emit: canonical", "path 'canonical_protenix/cm_native_artifacts_v1.json', emit: native_manifest", "path 'canonical_protenix/cm_ensemble_v1.json', emit: ensemble_manifest"), ('scripts/attest_protenix_runtime.py', 'scripts/finalize_protenix_conformational_mapping.py', 'scripts/prepare_protenix_conformational_mapping.py', 'scripts/prepare_runtime_image_attestation.py'), ('container { runtime_image }', 'beforeScript {\n        def store = params.runtime_image_store ?: System.getenv(\'BMS_RUNTIME_IMAGE_STORE\') ?: "${params.container_dir}/.image-store"\n        """\n        ${params.api_python} ${params.code_root}/scripts/prepare_runtime_image_attestation.py \\\n          --resolve-reference --store-root "${store}" \\\n          --registry "${preflight_source}/request/cm_runtime_registry_v1.json" \\\n          --reference "${preflight_source}/runtime-image-reference.json" \\\n          --receipt "${preflight_source}/runtime-image-receipt.json" \\\n          --executing-image "${runtime_image}" >/dev/null || exit 1\n\n        """\n    }')),
    'modules/confornets_experimental.nf:PrepConforNetsRequest': (('local_cpu',), (), ("path 'confornets_request.json', emit: request", "path 'confornets_assets', emit: assets_dir", "path '*.log'"), ('scripts/prep_confornets_request.py',), ()),
    'modules/confornets_experimental.nf:RunConforNets': (('ConforNets', 'gpu'), ('path request_json', 'path assets_dir'), ("path 'confornets_results', emit: results_dir", "path '*.log'"), (), ()),
    'modules/confornets_experimental.nf:FinalizeConforNetsOutputs': (('local_cpu',), ('path results_dir',), ("path 'final_confornets_results', emit: results_dir", "path 'final_confornets_results/conformers/*.cif', emit: cifs, optional: true", "path 'final_confornets_results/**/*.json', emit: jsons, optional: true", "path 'final_confornets_results/**/*.csv', emit: csvs, optional: true", "path 'final_confornets_results/**/*.pt', emit: states, optional: true", "path '*.log'"), (), ()),
    'modules/esmfold2_experimental.nf:ESMFold2MSAPredict': (('ESMFold2', 'gpu'), ("tuple val(producer_meta), val(request), val(source_paths), path(staged_files, stageAs: 'inputs/input??/*')",), ("tuple val(producer_meta), path('esmfold2_results/*.cif'), emit: typed_cifs", "path 'esmfold2_results/*.metrics.json', emit: metrics", "path 'esmfold2_results/*.telemetry.json', emit: telemetry", "path 'esmfold2_results/manifest.json', emit: manifest", "path 'esmfold2_results/summary.tsv', emit: summary", "path 'esmfold2_results/effective_settings.json', emit: effective_settings"), (), ()),
    'modules/esmfold2_experimental.nf:ESMFold2Predict': (('ESMFold2', 'gpu'), ('tuple val(producer_meta), val(sequence), val(sequence_name)',), ("tuple val(producer_meta), path('esmfold2_results/*.cif'), emit: typed_cifs", 'path "esmfold2_results/*.metrics.json", emit: metrics', "tuple val(sequence_name), path('esmfold2_results/*.cif'), path('esmfold2_results/*.metrics.json'), emit: shape_result", 'path "esmfold2_results/*.telemetry.json", emit: telemetry', 'path "esmfold2_results/manifest.json", emit: manifest', 'path "esmfold2_results/summary.tsv", emit: summary'), (), ()),
    'modules/esmfold2_experimental.nf:ESMFold2FromPdb': (('ESMFold2', 'gpu'), ('tuple val(producer_meta), path(source_pdb), val(candidate_name)',), ("tuple val(producer_meta), val('esmfold2'), path('esmfold2_results/*.cif'), path('esmfold2_results/*.metrics.json'), emit: typed_results",), (), ("errorStrategy 'ignore'",)),
    'modules/experimental/molecular_dynamics/analyze.nf:MD_ANALYZE_REPLICA': (('MolecularDynamicsAnalysis',), ('tuple val(replica_index), path(replica_dir), val(manifest_sha256)',), ('tuple val(replica_index), path("md_analysis_replica_${replica_index}.json"), emit: reports', 'tuple val(replica_index), path("md_analysis_replica_${replica_index}.artifacts.json"), emit: artifact_manifests', 'tuple val(replica_index), path("md_analysis_replica_${replica_index}.timeseries.parquet"), emit: timeseries', 'tuple val(replica_index), path("md_analysis_replica_${replica_index}.residue_metrics.parquet"), emit: residue_metrics'), (), ('container params.md_analysis_container ?: "${params.data_root}/apptainer/md-analysis-1.0.0.sif"', "errorStrategy 'retry'", 'maxRetries 2')),
    'modules/experimental/molecular_dynamics/gromacs_replica.nf:MD_GROMACS_REPLICA': (('MolecularDynamicsGromacs',), ('tuple val(replica_index), path(normalized_config), path(preparation_bundle)',), ('path "gromacs_replica_${replica_index}_manifest.json", emit: manifest', 'path "replica_${replica_index}", emit: artifacts'), (), ()),
    'modules/experimental/molecular_dynamics/openmm_replica.nf:MD_OPENMM_REPLICA': (('MolecularDynamicsOpenMM',), ('tuple val(replica_index), path(normalized_config), path(preparation_bundle)',), ('path "openmm_replica_${replica_index}_manifest.json", emit: manifest', 'path "replica_${replica_index}", emit: artifacts'), (), ()),
    'modules/experimental/molecular_dynamics/prepare.nf:MD_PREPARE_CONFIG': (('MolecularDynamicsPreparation',), ('path source_config', 'val config_base_dir'), ("path 'normalized_config.json', emit: normalized_config", "path 'md_metadata.json', emit: metadata", "path 'preparation_bundle', emit: preparation_bundle"), (), ()),
    'modules/fampnn.nf:PrepFAMPNN': (('pyrosetta_tools',), ('tuple path(pdb_files), path(json_files)',), ('path ("fampnn_input/*.pdb"), emit: pdbs', 'path ("fampnn_input/*.fampnn_prep.json"), emit: provenance, optional: true', 'path ("*.csv"), emit: csv'), (), ()),
    'modules/fampnn.nf:RunFAMPNN': (('FAMPNN', 'gpu_light'), ('tuple val(batch_id), path(pdbs), path(csv), val(gpu_id)', 'val analysis_chain_id', 'val analysis_contract // Trusted workflow-owned envelope, independent of pSCE.'), ('tuple path("results/*.pdb"), path("results/*.json"), emit: pdbs_jsons', 'path "fampnn_output", emit: raw', 'path ("fampnn_metadata_${batch_id}.jsonl"), topic: metadata_ch_fold_seq', 'path ("fampnn_seq_prob_metrics_${batch_id}.jsonl"), emit: seq_prob_metrics, optional: true', 'path "*.log"'), (), ()),
    'modules/fampnn.nf:FilterFAMPNN': (('pyrosetta_tools',), ('tuple path(pdb_files), path(json_files)',), ('path ("filtered_output/*.pdb"), emit: pdbs, optional: true', 'path ("filtered_output/*.json"), emit: jsons, optional: true', 'path ("filter_fampnn_${task.index}.log"), emit: logs'), (), ()),
    'modules/frustrampnn.nf:CanonicalFrustraMPNNTask': (('frustrampnn_gpu',), ('tuple val(component_request_meta), path(source_structure)',), ("tuple path('candidate_bundle/workflow_component_result_v1.json'), \\", "path('candidate_bundle'), \\", "path('candidate_bundle/frustrampnn_result_manifest_v1.json'), emit: result"), ('scripts/run_frustrampnn_component.py',), ("errorStrategy 'terminate'", 'maxRetries 0')),
    'modules/frustrampnn.nf:CanonicalFrustraMPNNV2Task': (('frustrampnn_gpu',), ('tuple path(component_request), path(source_structure), path(structure_map)',), ("tuple path('candidate_bundle/workflow_component_result_v3.json'), \\", "path('candidate_bundle'), \\", "path('candidate_bundle/frustrampnn_result_manifest_v3.json'), emit: result"), ('scripts/run_frustrampnn_component.py',), ("errorStrategy 'terminate'", 'maxRetries 0')),
    'modules/frustrampnn_parent_fanout.nf:StageFrustraMPNNParentCandidate': (('CPU',), ('tuple val(candidate_meta), path(terminal_structure)',), ("path 'candidate_*', emit: candidate",), ('scripts/stage_frustrampnn_parent_candidate.py',), ("errorStrategy 'terminate'", 'maxRetries 0')),
    'modules/frustrampnn_parent_fanout.nf:SpawnWaitFrustraMPNNParentChildren': (('CPU',), ('val parent_job_id', 'val parent_workflow_id', 'val settings_json', 'val settings_value_origin', 'path candidate_dirs'), ("path 'frustrampnn_parent_terminal_v1.json', emit: receipt", "path 'frustrampnn_child_bundles', emit: bundles"), ('scripts/run_frustrampnn_parent_fanout.py',), ("errorStrategy 'terminate'", 'maxRetries 0')),
    'modules/frustrampnn_parent_fanout.nf:ReportFrustraMPNNParentChildrenComplete': (('CPU',), ('val parent_job_id', 'val parent_workflow_id', 'path terminal_receipt'), ("path 'frustrampnn_children_complete.reported', emit: marker",), ('scripts/stage_reporter.py',), ("errorStrategy 'terminate'", 'maxRetries 0')),
    'modules/iggm.nf:IGGM_DENOVO': (('process_gpu',), ('tuple val(meta), path(antigen_pdb)', 'val epitope_residues'), ('tuple val(meta), path("output/*.pdb"), emit: designs', 'tuple val(meta), path("output/*.json"), emit: scores, optional: true', 'path "iggm_denovo_${meta.id}.log"'), (), ('container "${params.container_dir}/iggm.sif"',)),
    'modules/iggm.nf:IGGM_AFFINITY_MATURATION': (('process_gpu',), ('tuple val(meta), path(antibody_pdb), path(antigen_pdb)',), ('tuple val(meta), path("output/*_matured.pdb"), emit: matured_designs', 'tuple val(meta), path("output/*_mutations.json"), emit: mutations', 'path "iggm_maturation_${meta.id}.log"'), (), ('container "${params.container_dir}/iggm.sif"',)),
    'modules/iggm.nf:IGGM_STRUCTURE_PREDICTION': (('process_gpu',), ('tuple val(meta), path(antibody_fasta), path(antigen_pdb)',), ('tuple val(meta), path("output/*.pdb"), emit: predicted_structure', 'tuple val(meta), path("output/*_confidence.json"), emit: confidence', 'path "iggm_structure_${meta.id}.log"'), (), ('container "${params.container_dir}/iggm.sif"',)),
    'modules/iggm.nf:IGGM_HUMANIZATION': (('process_gpu',), ('tuple val(meta), path(antibody_pdb)',), ('tuple val(meta), path("output/*_humanized.pdb"), emit: humanized', 'tuple val(meta), path("output/*_humanization_report.json"), emit: report', 'path "iggm_humanization_${meta.id}.log"'), (), ('container "${params.container_dir}/iggm.sif"',)),
    'modules/merge_uncropped_target.nf:MergeUncroppedTarget': (('pyrosetta_tools',), ('path pdb_files', 'path uncropped_target_pdb'), ('path ("merged_pdbs/*.pdb"), emit: pdbs',), (), ()),
    'modules/ngs/bam_prepare.nf:PrepareBamForAnalysis': (('dorado_cpu',), ("path bam, stageAs: 'source.bam'",), ('tuple path("aligned.bam"), path("aligned.bam.bai"), emit: aligned', 'path "bam_prepare.log", emit: log'), (), ()),
    'modules/ngs/bam_prepare.nf:ValidateMappedBam': (('dorado_cpu',), ("tuple path(bam, stageAs: 'validated-source.bam'), path(bai, stageAs: 'validated-source.bam.bai')", "path reference, stageAs: 'expected-reference.fasta'"), ('tuple path("aligned.bam"), path("aligned.bam.bai"), emit: aligned', 'path "bam_mapped_check.log", emit: log'), (), ()),
    'modules/ngs/bam_prepare.nf:BamToFastqForQC': (('dorado_cpu',), ('tuple path(bam), path(bai)',), ('path "reads_for_qc.fastq", emit: fastq', 'path "bam_to_fastq_for_qc.log", emit: log'), (), ()),
    'modules/ngs/bam_prepare.nf:PrepareReferenceForIGV': (('dorado_cpu',), ("path reference, stageAs: 'expected-reference-source.fasta'",), ('path "reference.fasta", emit: reference_copy', 'path "reference.fasta.fai", emit: reference_index', 'path "reference_prepare.log", emit: log'), (), ()),
    'modules/ngs/clone_validation.nf:RunCloneValidation': (('wf_clone',), ('tuple path(bam), val(reference_fasta)',), ('path "wf_clone_out", emit: out', 'path "wf_clone.log", emit: log', 'path "runtime_provenance.json", emit: runtime_provenance', 'path "wf_clone_out/wf-clone-validation-report.html", emit: report', 'path "wf_clone_out/sample_status.txt", emit: sample_status'), (), ()),
    'modules/ngs/clone_validation.nf:CloneValidationAdapter': (('fastq_qc_cpu',), ('path result_root', 'path runtime_provenance', 'tuple path(aligned_bam), path(aligned_bai)', "path reference, stageAs: 'authoritative_reference.fasta'"), ('path "adapter_manifest.json", emit: manifest', 'path "verification_input", emit: verification_input', 'path "per_base_support.tsv", emit: per_base_support', 'path "alignment_stats.tsv", emit: alignment_stats', 'path "dimer_breakpoint_call.tsv", emit: breakpoint_call', 'path "dimer_secondary_summary.tsv", emit: secondary_summary'), (), ()),
    'modules/ngs/comparison_panel_attribution.nf:ComparisonPanelAttribution': (('local_cpu',), ('path fastq', 'path expected_reference', 'path snapshot'), ("path 'comparison_panel.fasta', emit: reference", "path 'comparison_panel_expected_reference.fasta', emit: expected_reference", "path 'comparison_panel_source.fastq', emit: source_fastq", "path 'comparison_panel_normalized.fastq', emit: normalized_fastq", "path 'comparison_panel_occurrence_map.json', emit: occurrence_map", "path 'comparison_panel.bam', emit: bam", "path 'comparison_panel.bam.bai', emit: bai", "path 'comparison_panel_summary.json', emit: summary"), (), ()),
    'modules/ngs/construct_verify.nf:ConstructVerify': (('fastq_qc_cpu',), ('path reference', 'path verification_input', 'path per_base_support', 'tuple path(aligned_bam), path(aligned_bai)', 'path alignment_stats', 'path dimer_breakpoint_call', 'path dimer_secondary_summary'), ('path "verification", emit: verification_dir', 'path "verification/qc_manifest.json", emit: manifest', 'path "verification/verification_summary.tsv", emit: summary', 'path "verification/variants.vcf", emit: variants', 'path "verification/per_base_metrics.tsv", emit: per_base_metrics', 'path "verification/evidence.html", emit: evidence_html'), (), ()),
    'modules/ngs/dorado_align.nf:DoradoAlign': (('dorado_cpu',), ('path bam', 'path reference'), ('tuple path("aligned.bam"), path("aligned.bam.bai"), emit: aligned', 'path "reference.fasta", emit: reference_copy', 'path "reference.fasta.fai", emit: reference_index', 'path "align.log", emit: log', 'path "qc_manifest.json", emit: primary_manifest, optional: true'), (), ()),
    'modules/ngs/dorado_basecall.nf:DoradoPreflight': (('local_cpu',), ('path pod5_dir',), ('path "dorado_preflight.json", emit: manifest',), ('scripts/dorado_p4_preflight.py',), ()),
    'modules/ngs/dorado_basecall.nf:DoradoBasecall': (('dorado_gpu', 'gpu'), ('path pod5_dir', 'path preflight_json'), ('path "calls.bam", emit: bam', 'path "basecall.log", emit: log', 'path "dorado_preflight.json", emit: preflight', 'path "dorado_runtime_provenance.json", emit: provenance', 'path "sequencing_summary.tsv", emit: summary, optional: true'), (), ()),
    'modules/ngs/dorado_basecall.nf:DoradoDemux': (('dorado_cpu',), ('path bam', 'path preflight_json'), ('path "demux", emit: directory', 'path "demux_manifest.json", emit: manifest', 'path "per_barcode_units.json", emit: units', 'path "demux.log", emit: log'), (), ()),
    'modules/ngs/fastq_align.nf:FastqAlign': (('dorado_cpu',), ('path fastq', 'path reference'), ('tuple path("aligned.bam"), path("aligned.bam.bai"), emit: aligned', 'path "reference.fasta", emit: reference_copy', 'path "reference.fasta.fai", emit: reference_index', 'path "fastq_align.log", emit: log'), (), ()),
    'modules/ngs/fastq_dimer_qc.nf:FastqMultimerQC': (('local_cpu',), ('path fastq',), ('path "read_lengths.tsv", emit: lengths', 'path "multimer_summary.tsv", emit: summary', 'path "multimer_candidates.tsv", emit: candidates', 'path "multimer_qc.log", emit: log'), (), ()),
    'modules/ngs/fastq_dimer_qc.nf:FastqDimerAnalysis': (('dorado_cpu',), ('path fastq', 'path reference'), ('path "dimer_candidates.fastq", emit: dimer_fastq', 'path "dimer_candidates.fasta", emit: dimer_fasta', 'path "dimer_read_lengths.tsv", emit: dimer_lengths', 'path "dimer_reference.fasta", emit: dimer_reference', 'path "dimer_reference.fasta.fai", emit: dimer_reference_index', 'path "dimer_analysis_summary.tsv", emit: summary', 'path "dimer_analysis.log", emit: log', 'path "qc_manifest.json", emit: qc_manifest', 'path "dimer_candidates.aligned.bam", emit: dimer_bam, optional: true', 'path "dimer_candidates.aligned.bam.bai", emit: dimer_bai, optional: true', 'path "dimer_consensus.fasta", emit: consensus, optional: true', 'path "dimer_consensus.log", emit: consensus_log, optional: true', 'path "dominant_dimer_consensus.fasta", emit: dominant_consensus, optional: true', 'path "dominant_dimer_consensus.log", emit: dominant_consensus_log, optional: true', 'path "dominant_dimer_consensus_metadata.tsv", emit: dominant_consensus_metadata, optional: true', 'path "dominant_dimer_consensus.read_ids.txt", emit: dominant_consensus_reads, optional: true', 'path "dimer_junction_profile.tsv", emit: junction_profile, optional: true', 'path "dimer_read_junctions.tsv", emit: junction_reads, optional: true', 'path "dimer_junction_events.tsv", emit: junction_events, optional: true', 'path "dimer_junction_clusters.tsv", emit: junction_clusters, optional: true', 'path "dimer_junction_hotspots.tsv", emit: junction_hotspots, optional: true', 'path "dimer_junction_rotated_profile.tsv", emit: junction_rotated_profile, optional: true', 'path "dimer_junction_rotation_summary.tsv", emit: junction_rotation_summary, optional: true', 'path "dimer_breakpoint_screen.tsv", emit: breakpoint_screen, optional: true', 'path "dimer_breakpoint_start_counts.tsv", emit: breakpoint_start_counts, optional: true', 'path "dimer_read_ledger.tsv", emit: read_ledger, optional: true', 'path "dimer_breakpoint_reads.tsv", emit: breakpoint_reads, optional: true', 'path "dimer_rotated_remap_summary.tsv", emit: rotated_remap_summary, optional: true', 'path "dimer_rotated_remap_breakpoints.tsv", emit: rotated_remap_breakpoints, optional: true', 'path "dimer_single_ref_split_events.tsv", emit: single_ref_split_events, optional: true', 'path "dimer_single_ref_split_profile.tsv", emit: single_ref_split_profile, optional: true', 'path "dimer_candidates.single_ref.aligned.bam", emit: single_ref_bam, optional: true', 'path "dimer_candidates.single_ref.aligned.bam.bai", emit: single_ref_bai, optional: true', 'path "dimer_single_ref_alignment.log", emit: single_ref_align_log, optional: true', 'path "dimer_alignment.log", emit: align_log, optional: true'), (), ()),
    'modules/ngs/fastq_dimer_qc.nf:BuildDimerCanonicalOutputs': (('local_cpu',), ('path summary', 'path events', 'path single_ref_events', 'path single_ref_profile', 'path breakpoint_screen', 'path reference_fasta'), ('path "dimer_breakpoint_call.tsv", emit: breakpoint_call', 'path "dimer_evidence_by_position.tsv", emit: evidence_by_position', 'path "dimer_read_events.tsv", emit: read_events', 'path "dimer_breakpoint_sequences.tsv", emit: breakpoint_sequences', 'path "dimer_secondary_anomalies.tsv", emit: secondary_anomalies', 'path "dimer_secondary_summary.tsv", emit: secondary_summary', 'path "dimer_diagnostics.tar.gz", emit: diagnostics, optional: true'), (), ()),
    'modules/ngs/fastq_plasmid_qc.nf:FastqPlasmidQC': (('fastq_qc_cpu',), ("tuple path(bam, stageAs: 'source-aligned.bam'), path(bai, stageAs: 'source-aligned.bam.bai')", "path reference, stageAs: 'expected-reference-source.fasta'", 'path fastq'), ('path "aligned.bam", emit: alignment_bam', 'path "aligned.bam.bai", emit: alignment_bai', 'path "read_lengths.tsv", emit: lengths', 'path "fastq_qc_summary.tsv", emit: summary', 'path "fastq_alignment_stats.tsv", emit: alignment_stats', 'path "fastq_coverage.tsv", emit: coverage', 'path "per_base_support.tsv", emit: per_base_support', 'path "qc_manifest.json", emit: qc_manifest', 'path "construct_verification_input", emit: verification_input', 'path "reference_qc.fasta", emit: reference', 'path "reference_qc.fasta.fai", emit: reference_index', 'path "igv_coverage_depth.bedgraph", emit: igv_coverage_depth', 'path "igv_position_gradient.bedgraph", emit: igv_position_gradient', 'path "igv_gc_content.bedgraph", emit: igv_gc_content', 'path "igv_gc_zscore.bedgraph", emit: igv_gc_zscore', 'path "igv_split_read_density.bedgraph", emit: igv_split_density', 'path "igv_softclip_density.bedgraph", emit: igv_softclip_density', 'path "igv_junction_hotspots.bed", emit: igv_hotspots', 'path "igv_report_sites.bed", emit: igv_report_sites_bed', 'path "igv_report_sites.tsv", emit: igv_report_sites_tsv', 'path "igv_track_config.json", emit: igv_track_config', 'path "igv_report.html", emit: igv_report', 'path "igv_report.log", emit: igv_report_log', 'path "fastq_consensus.fasta", emit: consensus', 'path "fastq_consensus.fasta.fai", emit: consensus_index', 'path "fastq_consensus.log", emit: consensus_log', 'path "fastq_qc.log", emit: log'), (), ()),
    'modules/ngs/modkit_pileup.nf:ValidateModifiedBaseBam': (('dorado_cpu',), ("tuple path(bam, stageAs: 'modified-base-source.bam'), path(bai, stageAs: 'modified-base-source.bam.bai')",), ('tuple path("modified_base_input.bam"), path("modified_base_input.bam.bai"), emit: bam', 'path "modified_base_tag_check.log", emit: log'), (), ()),
    'modules/ngs/modkit_pileup.nf:ModkitPileup': (('dorado_cpu',), ("tuple path(bam, stageAs: 'modkit-input.bam'), path(bai, stageAs: 'modkit-input.bam.bai')", "path reference, stageAs: 'modkit-reference.fasta'"), ('path "methylation.bed", emit: bed', 'path "pileup.log", emit: log'), (), ()),
    'modules/ngs/modkit_summary.nf:ModkitSummary': (('dorado_cpu',), ('tuple path(bam), path(bai)',), ('path "modkit_summary.tsv", emit: summary', 'path "summary.log", emit: log'), (), ()),
    'modules/openmm.nf:OpenMMRelaxation': (('OpenMM', 'gpu'), ('tuple val(batch_id), path(pdbs)', 'val compute_tier', 'val cdr_only', 'val restraint_mode', 'val antibody_chain', 'val force_field'), ('path "relaxed/*.pdb", emit: relaxed_pdbs, optional: true', 'tuple val(batch_id), path("relaxed/*.pdb"), emit: relaxed_with_batch, optional: true', 'path "relaxed/*.json", emit: metrics_json, optional: true', 'path "*.log", emit: logs', 'path "openmm_metadata_${batch_id}.jsonl", emit: metadata, optional: true, topic: metadata_ch_openmm'), (), ()),
    'modules/openmm.nf:OpenMMScore': (('OpenMM', 'gpu'), ('tuple val(batch_id), path(pdbs)', 'val scoring_mode', 'val binder_chains', 'val target_chains', 'val force_field'), ('path "scores/*.json", emit: scores_json, optional: true', 'path "*.log", emit: logs', 'path "mmgbsa_metadata_${batch_id}.jsonl", emit: metadata, optional: true, topic: metadata_ch_mmgbsa'), (), ()),
    'modules/openmm.nf:OpenMMDeltaDeltaG': (('OpenMM', 'gpu'), ('tuple val(design_name), path(mutant_pdb), path(wildtype_pdb)', 'val binder_chains', 'val target_chains', 'val force_field'), ('path "${design_name}_ddg.json", emit: ddg_json', 'path "*.log", emit: logs'), (), ()),
    'modules/ppiflow.nf:IdentifyAnchorResidues': (('pyrosetta_tools',), ('tuple val(meta), path(complex_pdb)',), ('tuple val(meta), path(complex_pdb), path("${meta.id}_enriched_complex.pdb"), path("${meta.id}_anchors.json"), path("${meta.id}_ppiflow_positions.txt"), path("${meta.id}_cdr_positions.txt"), path("${meta.id}_cdr_positions_by_loop.json"), emit: anchor_inputs', 'tuple val(meta), path("${meta.id}_interface_score.json"), emit: interface_scores', 'tuple val(meta), path("${meta.id}_rotamer_enrichment.json"), emit: rotamer_enrichment'), ('scripts/prepare_ppiflow_maturation.py',), ()),
    'modules/ppiflow.nf:RunPartialFlow': (('gpu', 'PPIFlow'), ('tuple val(meta), path(original_complex_pdb), path(complex_pdb), path(anchors_json), path(ppiflow_positions), path(cdr_positions), path(cdr_positions_by_loop_json)',), ('tuple val(meta), path("ppiflow_backbones"), path("ppiflow_backbones_manifest.json"), emit: backbones',), ('scripts/anchors_to_ppiflow_positions.py', 'scripts/maturation_native_adapter.py', 'scripts/validate_ppiflow_masks.py'), ()),
    'modules/ppiflow.nf:PrepMaturationRedesign': (('pyrosetta_tools',), ('tuple val(meta), path(backbone_pdbs), path(anchors_json), path(cdr_positions), path(cdr_positions_by_loop), path(comparison_requests)',), ('tuple val(meta), path("fampnn_input/*.pdb"), path("fampnn.csv"), path("fampnn_transport"), emit: prep',), ('scripts/anchors_to_ppiflow_positions.py', 'scripts/prep_antibody_constraints.py', 'scripts/prep_fampnn_designs.py'), ()),
    'modules/ppiflow.nf:RunMaturationFAMPNN': (('FAMPNN', 'gpu_light'), ('tuple val(meta), path(pdbs), path(csv), path(transport_dir)',), ('tuple val(meta), path("matured_pdbs/*.pdb"), path("matured_jsons/*.json"), emit: redesigned',), ('scripts/analyse_fampnn.py', 'scripts/maturation_native_adapter.py'), ()),
    'modules/ppiflow.nf:ScoreMaturationImprovement': (('pyrosetta_tools',), ('tuple val(meta), path(original_pdb), path(matured_pdbs), path(ppiflow_positions), path(cdr_positions_by_loop_json)',), ('tuple val(meta), path("scores/*_maturation_score.json"), emit: scores',), ('scripts/score_maturation.py',), ()),
    'modules/ppiflow.nf:ScorePartialFlowImprovement': (('pyrosetta_tools',), ('tuple val(meta), path(original_pdb), path(matured_pdbs), path(ppiflow_positions), path(cdr_positions_by_loop_json)',), ('tuple val(meta), path("scores/*_partial_flow_score.json"), emit: scores',), ('scripts/score_maturation.py',), ()),
    'modules/ppiflow.nf:FilterByMaturation': (('process_low',), ('tuple val(meta), path(matured_pdbs), path(score_jsons)',), ('tuple val(meta), path("filtered_output/*.pdb"), emit: pdbs, optional: true', 'path ("filter_reports/*_maturation_filter.json"), emit: filter_reports'), ('scripts/filter_maturation.py',), ()),
    'modules/predict_target_complex.nf:PredictTargetComplex': (('Boltz', 'gpu'), ('tuple val(meta), val(protein_seq), val(dna_seq)',), ('tuple val(meta), path("target_complex.pdb"), emit: complex', 'path "confidence.json", emit: confidence', 'path "predict_complex.log", emit: log'), ('scripts/prep_complex_yaml.py',), ()),
    'modules/protein_cad_experimental.nf:PrepProteinCadRequest': (('process_low',), (), ("path 'protein_cad_request.json', emit: request", "path 'protein_cad_inputs', emit: input_dir"), ('scripts/prep_protein_cad_request.py',), ()),
    'modules/protein_cad_experimental.nf:RunLaProteina': (('LaProteina', 'gpu'), ('path request_json', 'path input_dir'), ("path 'raw/pdbs/*.pdb', emit: pdbs", "path 'raw/metadata/*.json', emit: jsons", "path 'design_manifest.json', emit: manifest", "path '*.log'"), ('scripts/run_laproteina_inference.py',), ()),
    'modules/protein_cad_experimental.nf:RunDISCO': (('DISCO', 'gpu'), ('path request_json', 'path input_dir'), ("path 'raw/pdbs/*.pdb', emit: pdbs", "path 'raw/metadata/*.json', emit: jsons", "path 'design_manifest.json', emit: manifest", "path '*.log'"), ('scripts/run_disco_inference.py',), ()),
    'modules/protein_cad_experimental.nf:FinalizeProteinCadOutputs': (('process_low',), ('path pdb_files', 'path metadata_jsons', 'path design_manifest'), ("path 'published/*.pdb', emit: pdbs", "path 'published/confidence_*.json', emit: jsons", "path 'published/design_manifest.json', emit: manifest"), (), ()),
    'modules/proteinmpnn.nf:PrepMPNN': (('pyrosetta_tools',), ('tuple path(pdb_files), path(json_files)',), ('path ("mpnn_fixed/*.pdb"), emit: pdbs', 'path ("mpnn_prep_*.log")'), (), ()),
    'modules/proteinmpnn.nf:RunMPNN': (('MPNN', 'gpu_light'), ('path pdbs',), ('tuple path("${params.get(\'sequence_design_engine\') == \'proteinmpnn\' ? \'mpnn_canonical\' : \'results\'}/*.pdb"), path("${params.get(\'sequence_design_engine\') == \'proteinmpnn\' ? \'mpnn_canonical\' : \'results\'}/*.json"), emit: pdbs_jsons', 'path "mpnn_native_output", emit: raw', 'path ("mpnn_metadata_${task.index}.jsonl"), topic: metadata_ch_fold_seq', 'path "*.log"'), (), ("errorStrategy { params.allow_retries && task.exitStatus in [137, 139] ? 'retry' : 'terminate' }", 'maxRetries { params.allow_retries ? 2 : 0 }')),
    'modules/proteinmpnn.nf:FilterMPNN': (('pyrosetta_tools',), ('tuple path(pdb_files), path(json_files)',), ('path ("filtered_output/*.pdb"), emit: pdbs, optional: true', 'path ("filtered_output/*.json"), emit: jsons, optional: true', 'path ("filter_mpnn_${task.index}.log"), emit: logs'), (), ()),
    'workflows/protein_sequence_design.nf:PublishSequenceDesign': (('pyrosetta_tools',), ("path source_pdb, stageAs: 'source/*'", "path settings_json, stageAs: 'settings/*'", "tuple path(native_pdbs, stageAs: 'unfiltered/*'), path(native_jsons, stageAs: 'unfiltered/*')", "tuple path(selected_pdbs, stageAs: 'filtered/*'), path(selected_jsons, stageAs: 'filtered/*'), path(filter_logs, stageAs: 'filter_logs/*')"), ("path 'sequence_design_results.json', emit: index",), ('scripts/sequence_design_results.py',), ()),
    'modules/protenix.nf:ProtenixPredict': (('Protenix', 'gpu'), ('tuple val(producer_meta), val(sequence), val(sequence_name), path(prepared_msa)',), ('tuple val(producer_meta), path("predictions/**/*.cif"), emit: typed_cifs, optional: true', 'path "producer_publication/**/*.json", emit: producer_publication', 'path "predictions/**/*confidence*.json", emit: confidence, optional: true', 'path "predictions/**/*full_data*.json", emit: full_confidence, optional: true', 'path "msa_prepared/msa_report.json", emit: msa_report, optional: true', 'path "*.log", emit: logs, optional: true'), ('scripts/prepare_protenix_msa.py', 'scripts/run_protenix_inference.py', 'scripts/write_structure_producer_manifest.py'), ()),
    'modules/protenix.nf:PrepProtenixComplex': (('CPU',), ('tuple val(name), path(complex_json), path(msa_file), path(prepared_msa)',), ('tuple val(name), path("protenix_input.json"), path(prepared_msa), emit: protenix_json',), (), ()),
    'modules/protenix.nf:ProtenixFromComplex': (('Protenix', 'gpu'), ('tuple val(input_sample), path(complex_json), path(prepared_msa)',), ('tuple val(input_sample), path("producer_candidates.json"), path("predictions/**/*.${protenixComplexFinalizesGeometry(params) ? \'pdb\' : \'cif\'}"), emit: canonical_structures, optional: true', 'path "producer_publication/**/*.json", emit: producer_publication', 'path "predictions/**/*.cif", emit: raw_structures', 'path "predictions/**/*confidence*.json", emit: confidence, optional: true', 'path "predictions/**/*full_data*.json", emit: full_confidence, optional: true', 'path "msa_prepared/msa_report.json", emit: msa_report, optional: true', 'path "*.log", emit: logs, optional: true'), ('scripts/extract_target_templates.py', 'scripts/finalize_target_geometry.py', 'scripts/prepare_protenix_constraints.py', 'scripts/prepare_protenix_exact_templates.py', 'scripts/prepare_protenix_msa.py', 'scripts/run_protenix_inference.py', 'scripts/write_structure_producer_manifest.py'), ("errorStrategy { params.containsKey('plr_validator_suite_active') && params.plr_validator_suite_active == true ? 'ignore' : 'terminate' }",)),
    'modules/publish.nf:PublishResults': (('pyrosetta_tools',), ('path final_pdbs', 'path csv_scores', 'val rfd_count', 'val filter_rfd_count', 'val seq_count', 'val filter_seq_count', 'val filter_pred_count'), ('path "all_designs.csv"', 'path ("best_designs*"), optional: true', 'path "filter_best_designs.log"', 'path "success_metrics.json"'), (), ()),
    'modules/rf3.nf:RunRF3': (('Foundry', 'gpu'), ('tuple val(batch_id), path(pdbs)',), ('tuple path("rf3_results/*.cif.gz"), path("rf3_results/*.json"), emit: structures_metadata', 'path ("rf3_metadata_${batch_id}.jsonl"), emit: jsonl, topic: metadata_ch_fold_seq', 'path "rf3_${batch_id}.log"'), (), ('beforeScript """\n        mkdir -p rf3_results\n    """',)),
    'modules/rf3.nf:FilterRF3': (('Foundry',), ('tuple path(cif_files), path(json_files)',), ('path ("output/*.pdb"), emit: structures, optional: true', 'path "rf3_filter_*", emit: stage_receipt, optional: true', 'path "filter_rf3_${task.index}.log"', 'path (params.get(\'core_protein_scientific_contract\') == 1 ? "rf3_data_*.jsonl" : "output/filtered.jsonl"), emit: jsonl, optional: true'), (), ()),
    'modules/rf_filter_stage.nf:PublishRFFilterStage': (('process_low',), ('val stage_owner', 'val stage_id', 'val role', 'val expected_tasks', 'path task_receipts', 'path terminal_manifests'), ('path "${stage_id}.json", emit: stage',), ('scripts/collect_rf_filter_stage.py',), ()),
    'modules/rfantibody.nf:RFANTIBODY': (('process_gpu',), ('tuple val(meta), path(target_pdb), val(hotspot_residues), val(gpu_id), val(num_designs_for_this_gpu)', 'path framework_pdb'), ('tuple val(meta), path("output/*.pdb"), emit: designs', 'path "output/*.trb", emit: metadata, optional: true', 'path "output/traj/*.pdb", emit: trajectories, optional: true', 'path "rfantibody_${meta.id}.log", emit: log'), ('scripts/check_rfantibody_runtime.py', 'scripts/rfantibody_inference_wrapper.py'), ('container "${params.container_dir}/rfantibody.sif"', "errorStrategy 'retry'", 'maxRetries 2')),
    'modules/rfd3.nf:RunRFD3': (('Foundry', 'gpu'), ('tuple val(batch_id), path(input_json), path(runtime_source)',), ('path ("rfd3_results/*.cif.gz"), emit: structures', 'path ("rfd3_results/*.json"), emit: metadata', 'tuple path("rfd3_results/*.cif.gz"), path("rfd3_results/*.json"), emit: structures_metadata', 'path "rfd3_trajectories", emit: trajectories', 'path ("rfd3_metadata_${batch_id}.jsonl"), emit: producer_metadata_index, topic: metadata_ch_fold', 'path "rfd3_${batch_id}.log", emit: producer_log'), (), ('beforeScript """\n        mkdir -p rfd3_results rfd3_trajectories\n    """',)),
    'modules/rfd3.nf:PrepRFD3Input': (('pyrosetta_tools',), ('tuple val(mode), val(contigs), path(input_pdb), val(hotspots), val(num_designs), val(design_startnum)',), ('tuple val("${mode}_${design_startnum}"), path("rfd3_input_*.json"), path(input_pdb), emit: input_json',), (), ()),
    'modules/rfd3.nf:FilterRFD3': (('Foundry',), ('tuple path(cif_files), path(json_files)',), ('tuple path("filtered_output/*.pdb"), path("filtered_output/*.json"), emit: structures_metadata, optional: true', 'path "rfd3_filter_*", emit: stage_receipt, optional: true', 'path ("rfd3_data_*.jsonl"), topic: metadata_ch_fold', 'path "filter_rfd3_*.log"'), (), ()),
    'modules/rfdiffusion.nf:RunRFDiffusion': (('RFDiffusion', 'gpu'), ('tuple val(rfd_command), val(batch_id), val(batch_size), val(design_startnum), val(mode), path(input_files)',), ('path ("rfd_results/*.pdb"), emit: pdbs', 'tuple path("rfd_results/*.pdb"), path("rfd_results/*.json"), emit: pdbs_jsons', 'path "*.log"', 'path ("rfd_metadata_${batch_id}.jsonl"), topic: metadata_ch_fold'), (), ('beforeScript """\n        mkdir -p outputs schedules .dgl\n    """',)),
    'modules/rfdiffusion.nf:FilterRFD': (('pyrosetta_tools',), ('tuple path(pdb_files), path(json_files)',), ('tuple path("filtered_output/*.pdb"), path("filtered_output/*.json"), emit: pdbs_jsons, optional: true', 'path ("rfd_data_*.jsonl"), topic: metadata_ch_fold', 'path "filter_rfd_*.log"'), (), ()),
    'modules/rfdpoly.nf:RFDPolyDesign': (('RFDpoly', 'gpu'), ('val design_id', 'val contigs', 'val polymer_chains', 'val use_input_pdb', 'path input_pdb', 'val use_target_pdb', 'path target_pdb'), ('path "*.pdb", emit: pdbs', 'path "rfdpoly_metrics.json", emit: metrics'), (), ()),
    'modules/rfdpoly.nf:PrepBoltzOligo': (('pyrosetta_tools',), ('path pdbs', 'path prep_script'), ('path "boltz_inputs/*.yaml", emit: yamls',), ('scripts/prep_boltz_oligo.py', 'scripts/rebuild_sidechains.py'), ()),
    'modules/rfdpoly.nf:NAMPNNDesign': (('nampnn', 'gpu'), ('path pdbs', 'val design_id'), ('path "designed/*.pdb", emit: pdbs', 'path "designed/*.fa", emit: fastas', 'path "nampnn_metrics.json", emit: metrics'), (), ()),
    'modules/rfdpoly.nf:PyRosettaRebuild': (('pyrosetta_tools',), ('path pdbs', 'path fastas', 'path rebuild_script', 'val polymer_chains', 'path convert_script'), ('path "out_*.pdb", emit: pdbs', 'path "rebuild_metrics.json", emit: metrics'), (), ()),
    'modules/shape_blueprint.nf:ValidateShapeBundle': (('ShapeEvaluate',), ('path request_json', 'path geometry_manifest', 'path vertices_f64', 'path faces_u32', 'path points_f32le', 'path sdf_f32le'), ("path 'shape_input_receipt.json', emit: receipt",), ('scripts/shape_blueprint/validate_bundle.py',), ()),
    'modules/shape_blueprint.nf:PlanRFD3Batches': (('ShapeEvaluate',), ('path request_json',), ("path 'rfd3_batch_plan.json', emit: plan", "path 'batch_requests/*.json', emit: batch_requests"), ('scripts/shape_blueprint/plan_rfd3_batches.py',), ()),
    'modules/shape_blueprint.nf:RunShapeRFD3': (('ShapeRFD3', 'gpu'), ('path request_json', 'path geometry_manifest', 'path points_f32le', 'path sdf_f32le', 'path validation_receipt'), ("path 'rfd3_results/*.cif.gz', emit: structures", "tuple path('rfd3_results/*.cif.gz'), path('rfd3_results/*.json'), emit: structures_metadata", "path 'shape_rfd3_runtime_receipt.json', emit: runtime_receipt", "path 'rfd3_results/shape_guidance_steps.jsonl', emit: guidance_receipt", "path 'shape_rfd3_input.json', emit: input_spec", "path 'shape_rfd3.log', emit: log"), ('scripts/shape_blueprint/run_shape_rfd3.py',), ()),
    'modules/shape_blueprint.nf:AdmitRFD3InitialCandidate': (('ShapeEvaluate',), ('path candidate', 'path request_json', 'path geometry_manifest', 'path points_f32le', 'path sdf_f32le'), ('tuple path(candidate), path("${candidate.simpleName}.initial_admission.json"), emit: admitted',), ('scripts/shape_blueprint/evaluate_rfd3_initial_candidate.py',), ()),
    'modules/shape_blueprint.nf:PrepareShapeBackbone': (('ShapeEvaluate',), ('tuple path(candidate), path(admission)',), ("tuple val(candidate_id), path('shape_backbone_bundle'), emit: backbone",), ('scripts/shape_blueprint/prepare_shape_backbone.py',), ()),
    'modules/shape_blueprint.nf:BuildRFD3Aggregate': (('ShapeEvaluate',), ('path batch_plan', "path admission_files, arity: '0..*'"), ("path 'rfd3_aggregate_manifest.json', emit: aggregate",), ('scripts/shape_blueprint/build_rfd3_aggregate.py',), ()),
    'modules/shape_blueprint.nf:RunShapeProteinMPNN': (('MPNN', 'gpu_light'), ('tuple val(candidate_id), path(backbone)', 'val sequence_count', 'val seed', 'path request_json'), ('path "${candidate_id}_proteinmpnn", emit: bundle',), ('scripts/shape_blueprint/run_shape_sequence.py',), ()),
    'modules/shape_blueprint.nf:RunShapeFAMPNN': (('FAMPNN', 'gpu_light'), ('tuple val(candidate_id), path(backbone)', 'val sequence_count', 'val seed', 'path request_json'), ('path "${candidate_id}_fampnn", emit: bundle',), ('scripts/shape_blueprint/run_shape_sequence.py',), ()),
    'modules/shape_blueprint.nf:EvaluateShapeCandidate': (('ShapeEvaluate',), ('tuple val(sequence_name), path(structure), path(esm_metrics), path(source_backbone)', 'path request_json', 'path geometry_manifest', 'path point_pool', 'path sdf_grid'), ('tuple val(sequence_name), path("shape_candidate_bundle_${task.index}"), emit: bundle',), ('scripts/shape_blueprint/evaluate_shape_candidate.py',), ()),
    'modules/shape_blueprint.nf:RunShapeBoltzValidator': (('Boltz', 'gpu'), ('tuple val(sequence_name), val(sequence)', 'val seed'), ('tuple val(sequence_name), path("boltz_validator_evidence_${sequence_name}"), emit: evidence',), ('scripts/shape_blueprint/run_shape_validator_suite.py',), ()),
    'modules/shape_blueprint.nf:RunShapeProtenixValidator': (('Protenix', 'gpu'), ('tuple val(sequence_name), val(sequence)', 'val seed'), ('tuple val(sequence_name), path("protenix_validator_evidence_${sequence_name}"), emit: evidence',), ('scripts/run_protenix_inference.py', 'scripts/shape_blueprint/run_shape_validator_suite.py'), ()),
    'modules/shape_blueprint.nf:AggregateShapeValidatorEvidence': (('ShapeEvaluate',), ('tuple val(sequence_name), path(esm_structure), path(esm_metrics), val(sequence), path(peer_evidence)', 'val validator_suite', 'val seed'), ('tuple val(sequence_name), path("validator_evidence_${sequence_name}"), emit: evidence',), ('scripts/shape_blueprint/run_shape_validator_suite.py',), ()),
    'modules/shape_blueprint.nf:AttachShapePostRefold': (('ShapeEvaluate',), ('tuple val(sequence_name), path(candidate_bundle), path(validator_records)', 'path request_json', 'path geometry_manifest', 'path point_pool', 'path sdf_grid'), ('tuple val(sequence_name), path("attached_shape_candidate_bundle_${sequence_name}"), emit: bundle',), ('scripts/shape_blueprint/attach_shape_post_refold.py',), ()),
    'modules/shape_blueprint.nf:BuildShapeSkipBundle': (('ShapeEvaluate',), ('tuple val(candidate_id), path(backbone_bundle)', 'path request_json'), ('path "shape_skip_bundle_${candidate_id}", emit: bundle',), ('scripts/shape_blueprint/build_shape_skip_bundle.py',), ()),
    'modules/shape_blueprint.nf:BuildShapeResult': (('ShapeEvaluate',), ("path candidate_bundles, arity: '0..*'", 'path request_json', 'path aggregate_manifest', 'val job_id'), ("path 'final_shape_result/results', emit: results",), ('scripts/shape_blueprint/build_shape_result.py',), ()),
    'modules/structure_prediction.nf:GenerateLocalMSA': (('CPU',), ('tuple val(sequence), val(sequence_name)',), ('tuple val(sequence), val(sequence_name), path("${sequence_name}.a3m"), emit: msa', 'path "*_msa_quality.json", emit: quality_report, optional: true', 'path "*.log"'), ('scripts/run_local_msa.py',), ()),
    'modules/structure_prediction.nf:BatchMSAGeneration': (('GPU',), ('val sequences_json', 'val reference_sequence'), ('path ("msa_manifest.json"), emit: manifest', 'path ("*.a3m"), emit: msas, optional: true', 'path "*.log"'), ('scripts/batch_msa.py',), ()),
    'modules/structure_prediction.nf:BoltzFromSequenceTask': (('Boltz', 'gpu'), ('tuple val(producer_meta), val(sequence), val(sequence_name)',), ("path 'boltz_task_binding.json'", 'tuple val(producer_meta), path("producer_candidates.json"), path("predictions/*.{pdb,cif}"), emit: canonical_structures', 'path "predictions/*.json", emit: jsons, optional: true', 'path "predictions/*.npz", emit: native_identity_artifacts, optional: true', 'path "msa/*.a3m", emit: msa, optional: true', 'path "*.log"'), (), ()),
    'modules/structure_prediction.nf:BoltzFromSequenceWithMSATask': (('Boltz', 'gpu'), ('tuple val(producer_meta), val(sequence), val(sequence_name), path(msa_file)',), ("path 'boltz_task_binding.json'", 'tuple val(producer_meta), path("producer_candidates.json"), path("predictions/*.{pdb,cif}"), emit: canonical_structures', 'path "predictions/*.json", emit: jsons, optional: true', 'path "predictions/*.npz", emit: native_identity_artifacts, optional: true', 'path "*.log"'), (), ()),
    'modules/structure_prediction.nf:PrepareComplexWithMSA': (('CPU',), ('tuple val(complex_name), path(complex_json), path(msa_files)',), ('tuple val(complex_name), path("yamls/${complex_name}.yaml"), path("msa"), emit: prepared', 'path "msa/*.a3m", emit: msa, optional: true', 'path "msa/*_msa_quality.json", emit: quality_report, optional: true', 'path "msa/complex_msa_manifest.json", emit: msa_manifest, optional: true', 'path "*.log"'), ('scripts/extract_target_templates.py', 'scripts/run_local_msa.py'), ()),
    'modules/structure_prediction.nf:BoltzFromComplex': (('Boltz', 'gpu'), ('tuple val(complex_name), path(complex_yaml), path(msa_dir)',), ("path 'boltz_task_binding.json'", 'tuple val(complex_name), path("producer_candidates.json"), path("predictions/*.pdb"), emit: canonical_pdbs, optional: true', 'path "predictions/*.cif", emit: cifs, optional: true', 'path "predictions/*.json", emit: jsons, optional: true', 'path "predictions/*.npz", emit: npz, optional: true', 'path "*.log"'), ('scripts/finalize_target_geometry.py', 'scripts/write_structure_producer_manifest.py'), ()),
    'modules/thermompnn.nf:THERMOMPNN': (('process_gpu',), ('tuple val(meta), path(pdb)',), ('tuple val(meta), path("*_stability.csv"), emit: stability', 'path "thermompnn.log"'), (), ('container "${params.container_dir}/stability_tools.sif"',)),
    'modules/utils/anarci.nf:ANARCII': (('process_low',), ('tuple val(meta), path(pdb)',), ('tuple val(meta), path("*_imgt.pdb"), emit: pdb_imgt', 'tuple val(meta), path("*_cdrs.json"), emit: cdrs', 'tuple val(meta), path("*_cdr_positions.json"), emit: cdr_positions', 'path "anarci.log"'), (), ('container "${params.container_dir}/antibody_tools.sif"',)),
    'workflows/antibody_denovo.nf:SpawnRFantibodyJobs': (('process_low',), ('path target_pdb', 'val epitope_residues', 'val framework_type', 'val total_designs', 'val designs_per_job', 'val parent_job_id', 'val batch_name'), ('path "spawn_rfa_result.json", emit: result',), ('scripts/spawn_rfantibody_children.py',), ()),
    'workflows/antibody_denovo.nf:NormalizeTargetPDB': (('process_low',), ('tuple val(meta), path(target_pdb)',), ('tuple val(meta), path("normalized_target.pdb"), emit: normalized',), ('scripts/normalize_target_pdb.py',), ()),
    'workflows/antibody_denovo.nf:StageRFantibodyBackbones': (('process_low',), ('path pdb_files',), ('path "staged_output", emit: dir', 'path "staged_output/*.pdb", emit: pdbs, optional: true', 'path "staged_output/*.trb", emit: trbs, optional: true', 'path "rfantibody_stage_summary.json", emit: summary'), (), ()),
    'workflows/antibody_denovo.nf:ScreenRFantibodyBackbones': (('process_low',), ('path staged_dir', 'val epitope_residues', 'val antibody_chains', 'val target_chain', 'path reference_target_pdb'), ('path "screened_output", emit: dir', 'path "screened_output/*.pdb", emit: pdbs, optional: true', 'path "screened_output/*.trb", emit: trbs, optional: true', 'path "screened_output/*.json", emit: jsons, optional: true', 'path "screened_output/*.csv", emit: csvs, optional: true', 'path "screening_summary.json", emit: summary', 'path "screen_rfantibody_${task.index}.log", emit: log'), ('scripts/screen_rfantibody_backbones.py',), ()),
    'workflows/antibody_denovo.nf:CheckRFantibodyYield': (('process_low',), ('val candidate_count',), ('path "rfantibody_yield_guard.ok", emit: ok',), (), ()),
    'workflows/antibody_denovo.nf:CheckZeroYield': (('process_low',), ('val candidate_count',), ('path "zero_yield_guard.ok", emit: ok',), (), ()),
    'workflows/antibody_denovo.nf:CheckFrustraYield': (('process_low',), ('val candidate_count',), ('path "frustrampnn_yield_guard.ok", emit: ok',), (), ()),
    'workflows/antibody_denovo.nf:CheckPPIFlowYield': (('process_low',), ('val candidate_count', 'val stage_name'), ('path "ppiflow_yield_guard.ok", emit: ok',), (), ()),
    'workflows/antibody_denovo.nf:SpawnFAMPNNJobs': (('process_low',), ('path pdb_dir', 'val seqs_per_design', 'val pdbs_per_job', 'val parent_job_id', 'val batch_name'), ('path "spawn_fampnn_result.json", emit: result',), ('scripts/spawn_fampnn_children.py',), ()),
    'workflows/antibody_denovo.nf:WaitForChildren': (('process_low',), ('val parent_job_id', 'val stage_name', 'val poll_interval_seconds', 'val batch_name'), ('path "child_outputs.json", emit: child_outputs',), ('scripts/wait_for_children.py',), ()),
    'workflows/antibody_denovo.nf:CollectChildOutputs': (('process_low',), ('path child_outputs_json', 'val stage_name'), ('path "*.pdb", emit: pdbs, optional: true', 'path "*.trb", emit: trbs, optional: true', 'path "traj/*.pdb", emit: trajs, optional: true', 'path "collection_manifest.json", emit: manifest'), (), ()),
    'workflows/antibody_denovo.nf:WaitForFAMPNNChildren': (('process_low',), ('val parent_job_id', 'val stage_name', 'val poll_interval_seconds', 'val batch_name'), ('path "child_outputs.json", emit: child_outputs',), ('scripts/wait_for_children.py',), ()),
    'workflows/antibody_denovo.nf:CollectFAMPNNOutputs': (('process_low',), ('path child_outputs_json', 'val stage_name'), ('tuple path("job*.pdb"), path("job*.json"), emit: outputs', 'path "collection_manifest.json", emit: manifest'), (), ()),
    'workflows/antibody_denovo.nf:StageMaturationInputs': (('process_low',), ('path pdbs',), ('path "input_pdbs", emit: pdb_dir',), (), ()),
    'workflows/antibody_denovo.nf:SpawnMaturationJobs': (('process_low',), ('path pdb_dir', 'val designs_per_job', 'val parent_job_id', 'val batch_name', 'val stage_name', 'val ppiflow_region_mode_input', 'val ppiflow_selected_loops_input'), ('path "spawn_maturation_result.json", emit: result',), ('scripts/spawn_maturation_children.py',), ()),
    'workflows/antibody_denovo.nf:WaitForMaturationChildren': (('process_low',), ('val parent_job_id', 'val stage_name', 'val poll_interval_seconds', 'val batch_name'), ('path "child_outputs.json", emit: child_outputs',), ('scripts/wait_for_children.py',), ()),
    'workflows/antibody_denovo.nf:CollectMaturationOutputs': (('process_low',), ('path child_outputs_json', 'val stage_name'), ('path "*.pdb", emit: pdbs, optional: true', 'path "*.json", emit: jsons, optional: true', 'path "*.txt", emit: txts, optional: true', 'path "*.csv", emit: csvs, optional: true', 'path "collection_manifest.json", emit: manifest'), ('scripts/collect_maturation_outputs.py',), ()),
    'workflows/antibody_denovo.nf:StageValidatedMaturationInputs': (('process_low',), ('path pdbs',), ('path "input_pdbs", emit: pdb_dir',), (), ()),
    'workflows/antibody_denovo.nf:SpawnValidatedMaturationJobs': (('process_low',), ('path pdb_dir', 'val designs_per_job', 'val parent_job_id', 'val batch_name', 'val stage_name', 'val ppiflow_region_mode_input', 'val ppiflow_selected_loops_input'), ('path "spawn_validated_maturation_result.json", emit: result',), ('scripts/spawn_maturation_children.py',), ()),
    'workflows/antibody_denovo.nf:WaitForValidatedMaturationChildren': (('process_low',), ('val parent_job_id', 'val stage_name', 'val poll_interval_seconds', 'val batch_name'), ('path "child_outputs.json", emit: child_outputs',), ('scripts/wait_for_children.py',), ()),
    'workflows/antibody_denovo.nf:CollectValidatedMaturationOutputs': (('process_low',), ('path child_outputs_json', 'val stage_name'), ('path "*.pdb", emit: pdbs, optional: true', 'path "*.json", emit: jsons, optional: true', 'path "*.txt", emit: txts, optional: true', 'path "*.csv", emit: csvs, optional: true', 'path "collection_manifest.json", emit: manifest'), ('scripts/collect_maturation_outputs.py',), ()),
    'workflows/antibody_denovo.nf:StageStructureValidationArtifacts': (('process_low',), ('path pdb_list_file',), ('path "validation_artifacts", emit: dir',), (), ()),
    'workflows/antibody_denovo.nf:OpenInteractiveGate': (('process_low',), ('val job_id', 'val stage_name', 'val gate_trigger', 'val candidate_dir', 'val raw_dir', 'val filtered_dir', 'val framework_type', 'val antibody_chains', 'val structure_validator'), ('path "gate_${stage_name}.json", emit: report',), ('scripts/open_stage_gate.py',), ()),
    'workflows/antibody_denovo.nf:OpenInteractivePayloadGate': (('process_low',), ('val job_id', 'val stage_name', 'path payload_json'), ('path "gate_${stage_name}.json", emit: report',), ('scripts/open_stage_gate.py',), ()),
    'workflows/antibody_denovo.nf:CheckProtenixMsaPreflight': (('process_low',), ('path pdbs',), ('path "protenix_msa_preflight.json", emit: report',), ('scripts/check_protenix_msa_preflight.py', 'scripts/prep_protenix_batch.py'), ()),
    'workflows/antibody_denovo.nf:TriggerANARCIIAnnotationPostFAMPNNGate': (('process_low',), ('val job_id', 'val include_children'), ('path "anarcii_trigger.log", emit: log',), ('scripts/trigger_anarcii_annotation.py',), ()),
    'workflows/antibody_denovo.nf:TriggerANARCIIAnnotationPostValidationGate': (('process_low',), ('val job_id', 'val include_children'), ('path "anarcii_trigger.log", emit: log',), ('scripts/trigger_anarcii_annotation.py',), ()),
    'workflows/antibody_denovo.nf:TriggerANARCIIAnnotationFinal': (('process_low',), ('val job_id', 'val include_children'), ('path "anarcii_trigger.log", emit: log',), ('scripts/trigger_anarcii_annotation.py',), ()),
    'workflows/antibody_denovo.nf:SpawnChildJobs': (('process_low',), ('path pdbs', 'path msa_file', 'val parent_job_id', 'val batch_name', 'val child_params_json', 'val seqs_per_validation_job'), ('path "spawn_result.json", emit: result', 'path "spawn.log", emit: log'), ('scripts/spawn_antibody_children.py',), ()),
    'workflows/antibody_denovo.nf:WaitAndAggregateChildResults': (('process_low',), ('val parent_job_id', 'val batch_name', 'val expected_child_count', 'val child_stage'), ('path "validated_designs/*.pdb", emit: pdbs, optional: true', 'path "validated_designs/*.json", emit: scores, optional: true', 'path "validated_designs/*.npz", emit: aligned_error, optional: true', 'path "validated_designs/aligned_error/*.json", emit: aligned_error_json, optional: true', 'path "aggregation_report.json", emit: report'), ('scripts/result_ingester.py', 'scripts/stage_reporter.py', 'scripts/wait_for_children.py'), ()),
    'workflows/experimental/molecular_dynamics/orchestrator.nf:MD_SPAWN_REPLICAS': (('MolecularDynamicsCoordinator',), ('path normalized_config', 'path metadata', 'path preparation_bundle', 'val parent_job_id', 'val parent_name', 'val api_url'), ("path 'spawn_md_replicas.json', emit: spawn_result",), ('scripts/bms_md/spawn_replicas.py',), ()),
    'workflows/experimental/molecular_dynamics/orchestrator.nf:MD_WAIT_FOR_REPLICAS': (('MolecularDynamicsCoordinator',), ('path spawn_result', 'val parent_job_id', 'val parent_name', 'val api_url', 'val poll_seconds'), ("path 'replica_child_outputs.json', emit: child_status",), ('scripts/wait_for_children.py',), ()),
    'workflows/experimental/molecular_dynamics/orchestrator.nf:MD_COLLECT_REPLICAS': (('MolecularDynamicsCoordinator',), ('path child_status', 'path spawn_receipt'), ("path 'replica_collection.done', emit: collection_marker",), ('scripts/bms_md/aggregate_children.py',), ()),
    'workflows/experimental/molecular_dynamics/orchestrator.nf:MD_ASSERT_REPLICA_OUTCOME': (('MolecularDynamicsCoordinator',), ('path collection_marker', 'val aggregate_manifest'), ("path 'md_replica_outcome_verified.txt', emit: verified",), (), ()),
    'workflows/experimental/molecular_dynamics/orchestrator.nf:MD_SPAWN_ANALYSIS': (('MolecularDynamicsCoordinator',), ('path replica_outcome', 'val aggregate_manifest', 'val parent_job_id', 'val parent_name', 'val api_url', 'val runtime_sha256'), ("path 'spawn_md_analysis.json', emit: spawn_result",), ('scripts/bms_md/spawn_analysis.py',), ()),
    'workflows/experimental/molecular_dynamics/orchestrator.nf:MD_WAIT_FOR_ANALYSIS': (('MolecularDynamicsCoordinator',), ('path spawn_result', 'val parent_job_id', 'val parent_name', 'val api_url', 'val poll_seconds'), ("path 'analysis_child_outputs.json', emit: child_status",), ('scripts/wait_for_children.py',), ()),
    'workflows/experimental/molecular_dynamics/orchestrator.nf:MD_COLLECT_ANALYSIS': (('MolecularDynamicsCoordinator',), ('path child_status', 'val aggregate_manifest', 'path spawn_receipt'), ("path 'analysis_collection.done', emit: collection_marker",), ('scripts/bms_md/collect_analysis.py',), ()),
    'workflows/experimental/molecular_dynamics/orchestrator.nf:MD_ASSERT_ANALYSIS_OUTCOME': (('MolecularDynamicsCoordinator',), ('path collection_marker', 'val analysis_manifest'), ("path 'md_analysis_outcome_verified.txt', emit: verified",), (), ()),
    'workflows/experimental/molecular_dynamics/orchestrator.nf:MD_COMPLETION_BARRIER': (('MolecularDynamicsCoordinator',), ('path replica_outcome', 'path analysis_outcome', 'val aggregate_manifest', 'val analysis_manifest'), ("path 'md_completion_barrier.json', emit: completion",), (), ()),
    'workflows/frustrampnn_analysis.nf:PreparePersistedFrustraMPNNCandidate': (('CPU',), ('tuple val(record_base64), path(request_snapshot), path(source_snapshot), path(structure_map_snapshot)',), ("tuple path('workflow_component_request_v3.json'), path('canonical_source.pdb'), \\", "path('frustrampnn_structure_map_v1.json'), emit: prepared"), ('scripts/prepare_persisted_frustrampnn_candidate.py',), ("errorStrategy 'terminate'", 'maxRetries 0')),
    'workflows/frustrampnn_analysis.nf:PublishPersistedFrustraMPNNChildBundle': (('CPU',), ('tuple val(result_meta), path(candidate_bundle), path(result_manifest)',), ("path 'published_*.json', emit: marker",), ('scripts/publish_frustrampnn_bundle.py',), ("errorStrategy 'terminate'", 'maxRetries 0')),
    'workflows/frustrampnn_analysis.nf:RunPersistedFrustraMPNNGroupedBatch': (('frustrampnn_gpu',), ('val batch_manifest_path',), ("path 'grouped_results', emit: results",), ('scripts/run_frustrampnn_grouped_batch.py',), ("errorStrategy 'terminate'", 'maxRetries 0')),
    'workflows/frustrampnn_analysis.nf:PublishPersistedFrustraMPNNGroupedBundles': (('CPU',), ('path grouped_results',), ("path 'published_*.json', emit: marker",), ('scripts/publish_frustrampnn_grouped_results.py',), ("errorStrategy 'terminate'", 'maxRetries 0')),
    'workflows/frustrampnn_analysis.nf:ReportPersistedFrustraMPNNComplete': (('CPU',), ('path published_markers',), ("path 'frustrampnn_complete.reported'",), ('scripts/stage_reporter.py', 'scripts/validate_frustrampnn_publication_markers.py'), ("errorStrategy 'terminate'", 'maxRetries 0')),
    'workflows/ngs/ont_pooled_reference_assignment.nf:ONTPooledReferenceAssignment': (('pooled_assignment_cpu',), ('path fastq', 'path snapshot_root', 'val manifest_name'), ('path "assignment_summary.json"', 'path "per_read_assignment.tsv"', 'path "fastq_preflight.json"', 'path "occurrence_map.json"', 'path "combined_intended_reference.fasta"', 'path "combined_intended_reference.fasta.fai"', 'path "pooled_assignment.bam"', 'path "pooled_assignment.bam.bai"', 'path "pooled_reference_assignment.minimap2.log"', 'path "target_*.read_ids.txt"', 'path "target_*.fastq"', 'path "ambiguous.read_ids.txt"', 'path "ambiguous.fastq"', 'path "unclassified.read_ids.txt"', 'path "unclassified.fastq"', 'path "intended_pool.igv_session.json"'), (), ()),
    'workflows/ppiflow_generator_design.nf:OpenInteractiveGate': (('process_low',), ('val job_id', 'val stage_name', 'val gate_trigger', 'val candidate_dir', 'val raw_dir', 'val filtered_dir', 'val framework_type', 'val antibody_chains', 'val structure_validator'), ('path "gate_${stage_name}.json", emit: report',), ('scripts/open_stage_gate.py',), ()),
    'workflows/ppiflow_generator_design.nf:CollectPPIFlowGeneratorRaw': (('process_low',), ('path raw_pdbs', 'path score_jsons', 'path anchor_jsons', 'path interface_jsons', 'path rotamer_jsons', 'path enriched_pdbs', 'path ppiflow_positions_files', 'path cdr_positions_files', 'path cdr_positions_by_loop_jsons'), ('path "raw_output/*.pdb", emit: pdbs, optional: true', 'path "raw_output/*.json", emit: jsons, optional: true'), (), ()),
    'workflows/ppiflow_generator_design.nf:CollectPPIFlowGeneratorFiltered': (('process_low',), ('path filtered_pdbs', 'path filter_reports', 'path score_jsons', 'path anchor_jsons', 'path interface_jsons', 'path rotamer_jsons', 'path enriched_pdbs', 'path ppiflow_positions_files', 'path cdr_positions_files', 'path cdr_positions_by_loop_jsons'), ('path "filtered_output/*.pdb", emit: pdbs, optional: true', 'path "filtered_output/*.json", emit: jsons, optional: true'), (), ()),
    'workflows/protein_design.nf:BindProteinDesignTerminalMetadata': (('pyrosetta_tools',), ('tuple val(candidate_meta), path(terminal_structure)',), ('path "bound_${candidate_meta.candidate_id}.jsonl", topic: metadata_ch_fold_seq', 'path "terminal_${candidate_meta.candidate_id}.json", emit: manifest', 'path "analysis_${candidate_meta.candidate_id}.log", emit: log'), ('scripts/project_protein_design_metadata.py',), ()),
    'workflows/protein_design.nf:ProjectProteinDesignMetadata': (('CPU',), ('path combined_metadata', 'path terminal_manifests'), ("path 'all_designs.csv', emit: csv",), ('scripts/project_protein_design_metadata.py',), ()),
    'workflows/protein_design.nf:StageProteinDesignPublishStructure': (('CPU',), ('tuple val(candidate_meta), path(terminal_structure)',), ('path "candidate_${candidate_meta.candidate_id}.*", emit: structure',), (), ()),
    'workflows/protein_design.nf:PrepareProteinDesignFrustraMPNNCandidate': ((), ('tuple val(candidate_meta), path(terminal_structure), val(settings_base64), \\', 'val(settings_sha256), val(settings_value_origin)'), ("tuple path('workflow_component_request_v3.json'), path('canonical_source.pdb'), \\", "path('frustrampnn_structure_map_v1.json'), emit: prepared"), ('scripts/prepare_frustrampnn_candidate.py',), ()),
    'workflows/protein_design.nf:ReportProteinDesignFrustraMPNNNotRequested': (('CPU',), ('val trigger',), ("path 'protein_design_frustrampnn_terminal_manifest.json'", "path 'frustrampnn_not_requested.reported'"), ('scripts/stage_reporter.py',), ()),
    'workflows/protein_design.nf:PublishProteinDesignFrustraMPNNCandidate': (('CPU',), ('tuple val(result_meta), path(candidate_bundle), path(result_manifest)',), ("path 'published_*.json', emit: marker",), ('scripts/publish_frustrampnn_bundle.py',), ()),
    'workflows/protein_design.nf:ReportProteinDesignFrustraMPNNComplete': (('CPU',), ('path published_markers',), ("path 'frustrampnn_complete.reported'",), ('scripts/stage_reporter.py',), ()),
    'workflows/protein_design.nf:PrepareGeneralRFD3Input': (('pyrosetta_tools',), ('path request_json',), ("tuple val('generation_0'), path('rfd3_generation_native_input.json'), path(request_json), emit: input_json", "path 'rfd3_generation_preparation_receipt.json', emit: receipt"), ('scripts/rfd3_generation/prepare_native_input.py',), ()),
    'workflows/protein_design.nf:BuildGeneralRFD3ResultManifest': (('process_low',), ('tuple path(cif_files), path(json_files)', 'path request_json'), ("path 'rfd3_generation_result_manifest.json', emit: manifest",), ('scripts/rfd3_generation/build_result_manifest.py',), ()),
    'workflows/protein_local_redesign.nf:PrepareProteinLocalValidatorInput': (('process_low',), ('tuple val(producer_meta), path(source_pdb)',), ("tuple val(producer_meta), path(source_pdb), path('*.validator_contract.json'), path('*.protenix.json'), emit: prepared",), ('scripts/prepare_protein_local_validator_input.py',), ("errorStrategy 'ignore'",)),
    'workflows/protein_local_redesign.nf:FinalizeProteinLocalValidatorSuite': (('process_low',), ('val requested_validators', 'val expected_candidate_count', 'val validator_summaries'), ("path 'validator_suite_receipt.json', emit: receipt",), (), ()),
    'workflows/protein_local_redesign.nf:EnforceProteinLocalValidatorSuite': (('process_low',), ('path suite_receipt',), ("path 'validator_suite_complete', emit: complete",), (), ()),
    'workflows/protein_local_redesign.nf:StageProteinLocalValidatedCandidates': (('process_low',), ('path candidate_pdbs', 'path suite_receipt', 'path validator_suite_complete'), ("path 'review_candidates/*.pdb', emit: candidates",), (), ()),
    'workflows/protein_local_redesign.nf:OpenInteractiveGate': (('process_low',), ('val job_id', 'val stage_name', 'val gate_trigger', 'val candidate_dir', 'val raw_dir', 'val filtered_dir', 'val framework_type', 'val antibody_chains', 'val structure_validator'), ('path "gate_${stage_name}.json", emit: report',), ('scripts/open_stage_gate.py',), ()),
    'workflows/protein_local_redesign.nf:ResolveProteinLocalRegion': (('pyrosetta_tools',), ('path input_pdb',), ("path 'resolved_design_chain.pdb', emit: seed_pdb", "path 'region_manifest.json', emit: manifest"), ('scripts/resolve_redesign_regions.py',), ()),
    'workflows/protein_local_redesign.nf:PrepProteinLocalRFD3Input': (('pyrosetta_tools',), ('path seed_pdb', 'path manifest_json'), ("tuple val('protein_local_redesign_0'), path('rfd3_input_protein_local_redesign_0.json'), path(seed_pdb), emit: input_json",), ('scripts/prep_protein_local_redesign_rfd3_input.py',), ()),
    'workflows/protein_local_redesign.nf:PrepareProteinLocalNativeRFD3Input': (('pyrosetta_tools',), ('path input_structure', 'path request_json'), ("tuple val('protein_local_redesign_0'), path('rfd3_input_protein_local_redesign_0.json'), path(input_structure), emit: input_json", "path 'rfd3_preparation_receipt.json', emit: receipt"), (), ()),
    'workflows/protein_local_redesign.nf:BuildProteinLocalRFD3ResultManifest': (('process_low',), ('tuple path(cif_files), path(json_files)', 'tuple val(native_input_id), path(native_input_json), path(source_structure)', 'path trajectory_dir', 'path request_json', 'path preparation_receipt', 'path producer_log', 'path producer_metadata_jsonl'), ("path 'rfd3_result_manifest.json', emit: manifest",), (), ()),
    'workflows/protein_local_redesign.nf:MergeProteinLocalComplexes': (('pyrosetta_tools',), ('tuple path(pdb_files), path(json_files)', 'path original_complex', 'path manifest_json'), ("tuple path('merged/*.pdb'), path('merged/*.json'), emit: structures_metadata",), ('scripts/merge_redesigned_complexes.py',), ()),
    'workflows/protein_local_redesign.nf:PrepProteinLocalFAMPNN': (('pyrosetta_tools',), ('tuple path(pdb_files), path(json_files)', 'path manifest_json'), ("path('fampnn_input/*.pdb'), emit: pdbs", "path('fampnn_input/*.fampnn_prep.json'), emit: provenance, optional: true", "path('fampnn.csv'), emit: csv"), ('scripts/prep_fampnn_constraints_from_spec.py', 'scripts/prep_fampnn_designs.py'), ()),
    'workflows/protein_local_redesign.nf:PrepProteinLocalMPNN': (('pyrosetta_tools',), ('tuple path(pdb_files), path(json_files)', 'path manifest_json'), ("path('mpnn_fixed/*.pdb'), emit: pdbs",), ('scripts/add_fixed_labels_from_spec.py', 'scripts/prep_mpnn_designs.py'), ()),
    'workflows/protein_local_redesign.nf:ExportProteinLocalMPNNResults': (('process_low',), ('tuple path(pdb_files), path(json_files)',), ("path('published/*.pdb'), emit: pdbs", "path('published/*.json'), emit: jsons"), (), ()),
    'workflows/protein_local_redesign.nf:ExportProteinLocalBoltzPredictions': (('process_low',), ('tuple path(pdb_files), path(json_files)',), ("path('published/*.pdb'), emit: pdbs", "path('published/*.json'), emit: jsons"), (), ("errorStrategy 'ignore'",)),
    'workflows/rfantibody_backbone.nf:NormalizeTargetPDB': (('process_low',), ('path target_pdb',), ('path "normalized_target.pdb", emit: normalized',), ('scripts/normalize_target_pdb.py',), ()),
}


# Images are the existing nextflow.config labels, not top-level model guesses.
LABEL_ASSETS = {
    'AF2': ('af2.sif', None, 'alphafold', 'af2_models'),
    'AF2_BACKPROP': ('af2.sif', None, 'alphafold', 'af2_models'),
    'Boltz': ('boltz2.sif', None, 'boltz', 'boltz_models'),
    'BoltzCP': ('fold-cp.sif', 'bcp_container_path', 'boltz', 'boltz_models'),
    'ConforNets': ('confornets.sif', 'cn_container_path', 'openfold3', 'cn_checkpoint_path'),
    'ConforNetsCanonical': ('confornets-canonical.sif', 'cm_confornets_container_path', 'openfold3', 'cn_checkpoint_path'),
    'MolecularDynamicsPreparation': ('md-preparation-v1.sif', 'md_preparation_container', None, None),
    'MolecularDynamicsAnalysis': ('md-analysis-1.0.0.sif', 'md_analysis_container', None, None),
    'MolecularDynamicsGromacs': ('gromacs-md-2025.3.sif', 'md_gromacs_container', None, None),
    'MolecularDynamicsOpenMM': ('openmm-md-8.5.2.sif', 'md_openmm_container', None, None),
    'MolecularDynamicsCpu': ('gromacs-md-2025.3.sif', 'md_gromacs_container', None, None),
    'ESMFold2': ('esmfold2.sif', 'esmf_container_path', 'esmfold2', None),
    'FAMPNN': ('fampnn.sif', None, None, None),
    'MPNN': ('dl_binder_design.sif', None, None, None),
    'pyrosetta_tools': ('pyrosetta_tools.sif', None, None, None),
    # A container label does not imply inference: RF3/RFD3 filters only use
    # structure readers. Checkpoints are selected by the native process below.
    'Foundry': ('foundry.sif', None, None, None),
    'ShapeRFD3': ('shape_rfd3.sif', None, None, None),
    'ShapeEvaluate': ('shape_rfd3.sif', None, None, None),
    'BoltzGen': ('boltzgen.sif', None, None, None),
    'LaProteina': ('laproteina.sif', None, 'laproteina', 'pcad_laproteina_checkpoint_dir'),
    'DISCO': ('disco.sif', None, 'disco', 'pcad_disco_checkpoint_path'),
    'Antiberty': ('antibody_tools.sif', None, None, None),
    'ThermoMPNN': ('stability_tools.sif', None, 'thermompnn', None),
    'OpenMM': ('openmm.sif', None, None, None),
    'RFDpoly': ('rfdpoly_v2.sif', None, 'rfdpoly', None),
    'nampnn': ('nampnn.sif', None, 'nampnn', None),
    'PPIFlow': ('ppiflow.sif', None, 'ppiflow', 'ppiflow_weights_dir'),
    'Protenix': ('protenix.sif', 'protenix_container_path', 'protenix', 'protenix_weights'),
    'dorado_gpu': ('dorado.sif', 'dorado_runtime_sif', 'dorado', None),
    'dorado_cpu': ('dorado.sif', 'dorado_runtime_sif', None, None),
    'fastq_qc_cpu': ('dorado.sif', 'dorado_runtime_sif', None, None),
    'pooled_assignment_cpu': ('dorado.sif', 'dorado_runtime_sif', None, None),
}


def cm_gpu_requirements(request):
    """Canonical CM capacity constraint, distinct from scheduler reservation.

    Consumed by selected native metadata and the scheduler's persisted Job
    projection. Never inspect controller devices or mutate the native request.
    Zero means this policy adds no constraint; ordinary resource estimates stay
    authoritative for the other CM configurations.
    """
    requirements = {'minimum_gpu_memory_mb': 0, 'gpu_memory_mb': 0}
    if not isinstance(request, dict) or request.get('backend') != 'confornets':
        return requirements
    settings = request.get('confornets')
    count = settings.get('confornet_count') if isinstance(settings, dict) else None
    if type(count) is not int or count < 1:
        raise ValueError('Canonical ConforNets request requires positive confornet_count')
    if count >= 5:
        requirements.update(minimum_gpu_memory_mb=32000, gpu_memory_mb=24000)
    return requirements


def job_gpu_capacity_requirements(model_id, params):
    """Scheduler projection of the same selected CM native resource authority.

    Only CM reads its bounded managed canonical request. Other models keep
    their existing reservation estimates and need no native input IO here.
    """
    if model_id != 'conformational_mapping':
        return {'minimum_gpu_memory_mb': 0}
    from services.nextflow import _native_plan_metadata_settings
    if isinstance(params, dict) and 'backend' in params:
        request = params
    else:
        request = _native_plan_metadata_settings(model_id, params).get('cm_request')
    if not isinstance(request, dict):
        raise ValueError('CM resource admission requires its canonical request')
    requirements = cm_gpu_requirements(request)
    if not requirements['gpu_memory_mb']:
        requirements.pop('gpu_memory_mb')
    return requirements


# Only native tasks that synchronously await shared-runtime children are exempt
# from the compute slot. Collectors, spawners and similarly labelled science are
# compute tasks unless named here; labels alone never grant an exemption.
NATIVE_COORDINATORS = frozenset({
    'modules/boltzgen.nf:WaitForBoltzGenChildren',
    'modules/frustrampnn_parent_fanout.nf:SpawnWaitFrustraMPNNParentChildren',
    'workflows/antibody_denovo.nf:WaitForChildren',
    'workflows/antibody_denovo.nf:WaitForFAMPNNChildren',
    'workflows/antibody_denovo.nf:WaitForMaturationChildren',
    'workflows/antibody_denovo.nf:WaitForValidatedMaturationChildren',
    'workflows/antibody_denovo.nf:WaitAndAggregateChildResults',
    'workflows/experimental/molecular_dynamics/orchestrator.nf:MD_WAIT_FOR_REPLICAS',
    'workflows/experimental/molecular_dynamics/orchestrator.nf:MD_WAIT_FOR_ANALYSIS',
})


def native_resource_policy(params, label='CPU'):
    """Configured request, with explicit profile/placement binding requirements.

    A default is not an observation or an invented admission limit. The native
    compiler adds workstation_ryzen7960x; explicit params override its defaults.
    """
    from component_runtime import canonical_bytes
    gpu = label in {'gpu', 'gpu_light', 'process_gpu', 'frustrampnn_gpu',
                    'MolecularDynamicsGromacs', 'MolecularDynamicsOpenMM'}
    cpu_key, mem_key = ('cpus_per_gpu', 'memory_gpu') if gpu else ('cpus', 'memory_cpu')
    fixed = {
        'MolecularDynamicsPreparation': (1, '4 GB', '1h'),
        'MolecularDynamicsCoordinator': (1, '2 GB', '7d'),
        'MolecularDynamicsAnalysis': (4, '12 GB', '4h'),
        'MolecularDynamicsGromacs': (8, '16 GB', '24h'),
        'MolecularDynamicsOpenMM': (4, '8 GB', '24h'),
        'MolecularDynamicsCpu': (1, '2 GB', '1h'),
        'ShapeEvaluate': (2, '8 GB', None),
    }.get(label)
    return canonical_bytes({
        'authority': 'nextflow.config:process.withLabel.' + label,
        'profile_authority': 'nextflow.config:profiles.workstation_ryzen7960x',
        'execution_role': 'compute',
        'cpus': {'value': fixed[0] if fixed else 2 if label == 'local_cpu' else
                 8 if label == 'pooled_assignment_cpu' else params.get(cpu_key, 4),
                 'parameter': None if fixed else cpu_key,
                 'base_default': 8 if gpu else 24, 'profile_default': 4},
        'memory': {'value': fixed[1] if fixed else '16GB' if label == 'gpu_light' else
                   params.get(mem_key, '12GB' if gpu else '8GB'),
                   'parameter': None if fixed or label == 'gpu_light' else mem_key,
                   'base_default': '24GB', 'profile_default': '12GB' if gpu else '8GB'},
        'time': fixed[2] if fixed else None,
        'gpu': {'count': 1, 'vendor': 'nvidia', 'api': 'cuda',
                'assignment_authority': 'platform/api/services/gpu_orchestrator.py',
                'binding_required': True} if gpu else None,
        'scratch': {'native_directive': None, 'mode': 'nextflow_work_directory',
                    'capacity_authority': 'platform/api/services/remote_execution/bundle.py',
                    'byte_estimate_binding_required': True},
        'max_forks': 1 if label in {'frustrampnn_gpu', 'wf_clone'} else
                     4 if label == 'gpu_light' else 25 if label in {'gpu', 'process_gpu'} else None,
        'aggregate_admission_authority': 'platform/api/services/gpu_orchestrator.py',
        'profile_binding_required': True,
        'placement_binding_fields': ['available_cpus', 'available_memory_bytes', 'scratch_bytes'] +
                                    (['physical_gpu_ids', 'available_vram_bytes'] if gpu else []),
    })


def native_lifecycle_policy(params, label='CPU'):
    from component_runtime import canonical_bytes
    retry = params.get('allow_retries') is True and label == 'gpu'
    return canonical_bytes({
        'authority': 'nextflow.config:process; profiles.workstation_ryzen7960x.process',
        'error_strategy': 'retry' if label == 'MolecularDynamicsAnalysis' else
                          'retry_on_137_139_otherwise_finish' if retry else 'terminate',
        'max_retries': 2 if label == 'MolecularDynamicsAnalysis' else 3 if retry else 0,
        'retry_exit_statuses': [137, 139] if retry else [],
        'cancellation_authority': 'Nextflow task/process lifecycle; platform/api/component_runtime.py:ComponentLedger',
        'resume_authority': 'Nextflow cache and native scientific checkpoint contract',
        'automatic_checkpoint_restart': False,
        'interactive_decision': 'explicit_checkpoint_bound_operator_decision_only',
    })


class _NativeAnnotations:
    """Append descriptors to the existing registry-owned plan, never execute."""
    def __init__(self, params, entrypoint, components, dynamic, dependencies, roles, services, unresolved):
        self.p, self.entrypoint = params, entrypoint
        self.components, self.dynamic, self.dependencies = components, dynamic, dependencies
        self.roles, self.services, self.unresolved = roles, services, unresolved

    def asset(self, kind, path, authority, selector=None, *, condition=None):
        from component_runtime import SelectedDependency
        key = kind + ':' + (path or selector)
        previous = self.dependencies.get(key)
        selector = selector or (previous.selector if previous else None)
        self.dependencies[key] = SelectedDependency(key, kind, path, authority,
            selector=selector, compatibility_authority='docs/Shared_Scientific_Runtime_Images.md',
            condition=condition)
        return key

    def stage(self, name, after=(), *, source=None, condition=None, expansion=None, extra=()):
        import re
        from component_runtime import NativeArtifactRole, NativeComponent, canonical_bytes
        keys = [k for k in PROCESS_CONTRACTS if k.endswith(':' + name) and
                (source is None or k == source + ':' + name)]
        if len(keys) != 1:
            raise ValueError('Native process authority is ambiguous or missing: ' + name)
        authority = keys[0]
        native_name = name
        count = sum(row.authority == authority for row in (*self.components, *self.dynamic))
        if count:
            name = name + ':' + str(count + 1)
        labels, inputs, outputs, helpers, directives = PROCESS_CONTRACTS[authority]
        deps = ['support-python', 'nextflow', 'apptainer', 'bms-source', *extra]
        for label in labels:
            if label in LABEL_ASSETS:
                image, selector, weights, weight_selector = LABEL_ASSETS[label]
                deps.append(self.asset('image', image, 'nextflow.config:process.withLabel.' + label, selector))
                if weights:
                    deps.append(self.asset('weights', weights, authority, weight_selector))
        for helper in helpers:
            deps.append(self.asset('support_tool', helper, authority))
        for owner, path, selector in (
            ('RFANTIBODY', 'rfantibody', 'rfd_models'),
            ('ANTIFOLD', 'antifold', None),
            ('RunFAMPNN', None, 'fampnn_checkpoint_path'),
            ('RunMaturationFAMPNN', None, 'fampnn_checkpoint_path'),
        ):
            if native_name == owner and (path is not None or self.p.get(selector)):
                deps.append(self.asset('weights', path, authority, selector))
        from model_registry import native_checkpoint_dependencies
        selected_weights, weight_blockers = native_checkpoint_dependencies(native_name, self.p)
        for dependency in selected_weights:
            self.dependencies[dependency.logical_id] = dependency
            deps.append(dependency.logical_id)
        for blocker in weight_blockers:
            self.unresolved(blocker.component_or_dependency_id, blocker.field,
                blocker.needed_authority, blocker.reason, blocks=blocker.blocks)
        if native_name == 'RFANTIBODY' and self.p.get('rfantibody_debug_repo_overlay') is True:
            deps.append(self.asset('runtime_data', 'rfantibody', authority))
        # Process-level containers are native authority too (RFantibody/AntiFold/ANARCII).
        for directive in directives:
            if directive.startswith('container '):
                images = re.findall(r'([A-Za-z0-9_.-]+\.sif)', directive)
                for image in images:
                    deps.append(self.asset('image', image, authority))
        input_ids, output_ids = [], []
        upstream_roles = tuple(role.role_id for role in self.roles
            if role.direction == 'output' and role.component_key in after)
        for direction, declarations, ids in (('input', inputs, input_ids), ('output', outputs, output_ids)):
            for index, declaration in enumerate(declarations):
                match = re.search(r'emit:\s*(\w+)', declaration)
                name_part = match.group(1) if match else str(index)
                role_id = name + ':' + direction + ':' + name_part
                ids.append(role_id)
                category = name_part if match else ('native_tuple' if declaration.startswith('tuple') else
                           'native_artifact' if declaration.startswith(('path', 'file')) else 'native_value')
                fmt = 'native_tuple' if declaration.startswith('tuple') else next(
                    (fmt for suffix, fmt in (('.json', 'json'), ('.parquet', 'parquet'), ('.pdb', 'pdb'),
                     ('.cif', 'mmcif'), ('.bam', 'bam'), ('.fastq', 'fastq'), ('.fasta', 'fasta'),
                     ('.tsv', 'tsv'), ('.csv', 'csv'), ('.yaml', 'yaml'), ('.log', 'text'))
                     if suffix in declaration), 'native_path_set' if 'path' in declaration else 'native_value')
                if direction == 'output' and native_name == 'StageProteinDesignPublishStructure':
                    category = 'canonical_candidates'
                self.roles.append(NativeArtifactRole(role_id, name, direction, category, authority,
                    requiredness='optional' if 'optional: true' in declaration else 'required',
                    identity_authority=authority + '; ' + self.entrypoint + ':channel metadata',
                    publication_authority=authority + ':publishDir' if direction == 'output' else None,
                    condition=condition, format=fmt, cardinality_authority=authority + ':' + direction,
                    native_declaration=declaration,
                    source_role_ids=upstream_roles if direction == 'input' else ()))
        # Parameter-only preparation has typed settings input, not an empty placeholder.
        if not input_ids:
            input_ids = [name + ':effective_settings']
            self.roles.append(NativeArtifactRole(input_ids[0], name, 'input', 'effective_settings', authority,
                identity_authority='platform/api/model_registry.py:selected_execution_metadata', format='json'))
        if not output_ids:
            self.unresolved(name, 'native_output_contract', authority,
                'Selected native process has no declared output; native completion receipt binding required')
        label = next((x for x in labels if x.startswith('MolecularDynamics')), None) or next(
            (x for x in labels if x in {'gpu', 'gpu_light', 'process_gpu', 'frustrampnn_gpu',
             'local_cpu', 'wf_clone', 'pooled_assignment_cpu', 'ShapeEvaluate'}), 'CPU')
        import ast
        import json
        resources = json.loads(native_resource_policy(self.p, label))
        resources['execution_role'] = 'coordinator' if authority in NATIVE_COORDINATORS else 'compute'
        if resources['execution_role'] == 'coordinator':
            resources['max_forks'] = 1
        if native_name == 'RunCanonicalConforNets':
            resources['gpu'].update(cm_gpu_requirements(self.p.get('cm_request')))
        lifecycle = json.loads(native_lifecycle_policy(self.p, label))
        for directive in directives:
            field, _, expression = directive.partition(' ')
            try:
                value = ast.literal_eval(expression.strip())
            except (ValueError, SyntaxError):
                continue  # Native closure remains explicit in selection.directives.
            if field in {'cpus', 'memory'}:
                resources[field] = {'value': value, 'authority': authority + ':' + field}
            elif field in {'time', 'maxForks'}:
                resources['time' if field == 'time' else 'max_forks'] = value
            elif field in {'errorStrategy', 'maxRetries'}:
                lifecycle['error_strategy' if field == 'errorStrategy' else 'max_retries'] = value
        if native_name in {'RunPartialFlow', 'RunMaturationFAMPNN'}:
            resources['max_forks'] = 1
        if native_name == 'ANARCII':
            resources['max_forks'] = 4
        if 'BoltzCP' in labels:
            resources['gpu']['count'] = self.p.get('bcp_size_cp', 4)
            resources['gpu']['count_authority'] = 'modules/boltz_cp_experimental.nf:size_cp'
        component = NativeComponent(name, authority, canonical_bytes({'native_process': native_name,
            'labels': list(labels), 'directives': list(directives)}), depends_on=tuple(after),
            dependency_ids=tuple(dict.fromkeys(deps)), input_role_ids=tuple(input_ids), output_role_ids=tuple(output_ids),
            resources_json=canonical_bytes(resources),
            grouping_authority=self.entrypoint + ':native channel grouping',
            lifecycle_authority=authority + '; nextflow.config:process', condition=condition,
            expansion_authority=expansion['authority'] if expansion else None,
            lifecycle_json=canonical_bytes(lifecycle),
            expansion_json=canonical_bytes(expansion) if expansion else None)
        (self.dynamic if expansion else self.components).append(component)
        if expansion:
            # The selected native parent actually runs these blocking processes;
            # a dynamic compute template is not their resource declaration.
            waiter = {
                'boltzgen': ('modules/boltzgen.nf', 'WaitForBoltzGenChildren'),
                'frustrampnn': ('modules/frustrampnn_parent_fanout.nf', 'SpawnWaitFrustraMPNNParentChildren'),
                'rfantibody': ('workflows/antibody_denovo.nf', 'WaitForChildren'),
                'fampnn': ('workflows/antibody_denovo.nf', 'WaitForFAMPNNChildren'),
                'backbone_refine': ('workflows/antibody_denovo.nf', 'WaitForMaturationChildren'),
                'maturation': ('workflows/antibody_denovo.nf', 'WaitForMaturationChildren'),
                'maturation_post_validation': ('workflows/antibody_denovo.nf', 'WaitForValidatedMaturationChildren'),
                'structure_validation': ('workflows/antibody_denovo.nf', 'WaitAndAggregateChildResults'),
            }.get(expansion.get('child_stage'))
            if waiter and not any(row.authority == ':'.join(waiter) for row in self.components):
                self.stage(waiter[1], (name,), source=waiter[0])
        return name

    def chain(self, names, after=(), **kwargs):
        for name in names:
            after = (self.stage(name, after, **kwargs),)
        return after

    def callback(self, owner, authority):
        self.asset('support_tool', 'scripts/lib/component_adapter.py', authority)

    def msa(self, predictor, after, *, consumer=None):
        from component_runtime import ExternalServiceIntent, NativeArtifactRole, canonical_bytes
        key = predictor + ':generated_msa'
        if consumer == 'modules/antibody_batch.nf:BatchProtenixValidation':
            self.asset('support_tool', 'scripts/lib/component_adapter.py', consumer)
        enabled = self.p.get('protenix_use_msa', True) is not False if predictor == 'protenix' else self.p.get('boltz_use_msa') is True
        self.roles.append(NativeArtifactRole(key, predictor, 'input', 'native_chain_alignments',
            'platform/api/services/model_msa_handoff.py', requiredness='required' if enabled else 'optional',
            format='native_msa_handoff', identity_authority='biomodstack_msa_handoff.py'))
        self.services.append(ExternalServiceIntent(key, self.p.get('msa_provider'),
            'platform/api/services/model_msa_handoff.py; platform/api/services/msa_preparation.py'
                + ('; ' + consumer if consumer else ''),
            canonical_bytes({k: v for k, v in self.p.items()
                             if k.startswith(('msa_', 'boltz_', 'protenix_', 'colabfold_'))
                             and k not in {'msa_cache_dir', 'msa_local_db', 'protenix_container_path',
                                           'protenix_model_dir', 'protenix_download_cache_dir'}}),
            tuple(role.role_id for role in self.roles if role.component_key in after and role.direction == 'output'),
            (key,), 'planned_from_generated_candidates' if enabled else 'disabled'))

    def frustra(self, after, *, owner=None):
        deps = (self.asset('image', 'frustrampnn.sif', 'modules/frustrampnn_remote.nf', 'frustrampnn_container_path'),)
        # The canonical process consumes the native batch manifest; its allowed
        # expansion binds ordering and joins, not future candidate identities.
        name = 'RunPersistedFrustraMPNNGroupedBatch'
        node = self.stage(name, after, extra=deps, expansion={
            'authority': 'platform/api/component_runtime.py:plan_frustrampnn',
            'child_model': 'frustrampnn', 'child_mode': 'analyze', 'child_stage': 'frustrampnn',
            'candidate_identity': 'workflow_component_request_v3.source_artifact; candidate_id',
            'grouping': 'FrustraMPNNSettings.batching_enabled/structures_per_job; preserve candidate order',
            'join': 'all required candidate bundles; no optional/degraded completion',
            'settings': self.p.get('frustrampnn_settings'),
            'native_owner': owner or self.entrypoint,
        })
        self.callback(node, 'modules/frustrampnn_parent_fanout.nf')
        return (node,)



def append_native_frustra_coordinator(params, entrypoint, components, dynamic,
                                      dependencies, roles, services, unresolved):
    """Registry-selected prediction/design Frustra fanout uses the native waiter."""
    annotations = _NativeAnnotations(params, entrypoint, components, dynamic,
        dependencies, roles, services, unresolved)
    return annotations.stage('SpawnWaitFrustraMPNNParentChildren',
        tuple(row.component_key for row in components), source='modules/frustrampnn_parent_fanout.nf')


def append_native_workflow_metadata(model_id, mode, params, entrypoint, components,
                                    dynamic, dependencies, roles, services, unresolved):
    """Annotate the compiler-selected workflow; return whether it has a contract.

    Predicates below are from the named native workflow, not inferred from the
    model filename. Request snapshots are supplied by the native compiler owner.
    Missing snapshots are precise binding requirements, never guessed branches.
    """
    from pathlib import PurePosixPath
    p = params
    workflow = PurePosixPath(entrypoint).stem
    a = _NativeAnnotations(p, entrypoint, components, dynamic, dependencies, roles, services, unresolved)
    def yes(key, default=False):
        v = p.get(key)
        return default if v is None else v is True or str(v).lower() == 'true'
    def request(key, authority):
        value = p.get(key)
        if not isinstance(value, dict):
            unresolved(workflow, 'dependency_closure', authority,
                'Native compiler must bind validated immutable ' + key + ' snapshot; path alone cannot select nested dependencies')
            return None
        return value
    def external_weights(path, owner, selector=None):
        return a.asset('weights', path, owner, selector)
    def validation(after, predictor):
        name = {'boltz2': 'BatchBoltzValidation', 'protenix_v2': 'BatchProtenixValidation',
                'esmfold2': 'BatchESMFold2Validation'}.get(predictor)
        if name is None:
            unresolved(workflow, 'dependency_closure', entrypoint,
                       'Unsupported native structure_validator: ' + str(predictor))
            return after
        expansion = None
        if yes('exploration_mode') and workflow == 'antibody_denovo':
            expansion = {'authority': entrypoint + ':SpawnChildJobs',
                'child_model': 'antibody_child', 'child_mode': 'validation_batch',
                'child_stage': 'structure_validation',
                'candidate_identity': 'ordered source candidate keys and selected validation method',
                'grouping': 'resolveValidationBatchPlanValue; preserve hosted MSA pairing constraints',
                'join': 'WaitAndAggregateChildResults; all native required children'}
            a.callback(name, entrypoint + ':SpawnChildJobs')
        result = (a.stage(name, after, expansion=expansion),)
        if predictor == 'boltz2':
            result = a.chain(['AlignBoltzValidation'], result)
        if predictor in {'boltz2', 'protenix_v2'}:
            a.msa('protenix' if predictor == 'protenix_v2' else 'boltz2', after,
                  consumer='modules/antibody_batch.nf:' + name)
        return result

    if workflow == 'protein_sequence_design':
        engine = p.get('sequence_design_engine')
        selected_mode = p.get('sequence_design_mode')
        modes = {'fampnn': {'design', 'fixed_backbone', 'binder_design'},
                 'proteinmpnn': {'design'}}
        if (engine != model_id or selected_mode != mode or
                selected_mode not in modes.get(engine, set())):
            unresolved(workflow, 'dependency_closure', entrypoint,
                'Sequence-only wrapper must bind the exact public model/mode')
            return True
        if engine == 'fampnn':
            a.chain(['PrepFAMPNN', 'RunFAMPNN', 'FilterFAMPNN'])
            after = ('RunFAMPNN', 'FilterFAMPNN')
        else:
            a.chain(['PrepMPNN', 'RunMPNN', 'FilterMPNN'])
            after = ('RunMPNN', 'FilterMPNN')
        a.stage('PublishSequenceDesign', after, source='workflows/protein_sequence_design.nf')
        return True

    if workflow == 'boltzgen_child':
        # Prepared input and generation only; global selection belongs to parent.
        a.stage('RunBoltzGen')
        return True

    if workflow == 'protein_design':
        after = ()
        boltzgen = p.get('diffusion_method', 'rfd3') == 'boltzgen'
        generation_only = yes('run_rfd_only')
        sequence_prediction = bool(p.get('sequence_input') or p.get('sequence_batch_json_path'))
        if sequence_prediction:
            # This native branch calls the same structure_prediction_wf, then
            # rejoins protein-design publication below. Reuse its annotations;
            # Frustra/public admission/result contracts belong to the parent.
            from model_registry import selected_execution_metadata
            nested = selected_execution_metadata(model_id, mode,
                {**p, 'run_frustrampnn': False}, 'workflows/structure_prediction.nf')
            components.extend(nested.static_components)
            dynamic.extend(nested.dynamic_templates)
            dependencies.update((item.logical_id, item) for item in nested.dependencies)
            roles.extend(nested.artifact_roles)
            services.extend(nested.external_services)
            for blocker in nested.blockers:
                if blocker.field in {'dependency_closure', 'external_service_roles'}:
                    unresolved(blocker.component_or_dependency_id, blocker.field,
                        blocker.needed_authority, blocker.reason, blocks=blocker.blocks)
            after = tuple(row.component_key for row in nested.static_components)
        elif boltzgen:
            after = a.chain(['PrepBoltzGenInput'])
            # Native protein_design.nf selects the coordinator even for a
            # single child; request cardinality must not change that routing.
            parallel = (p.get('parallel_mode') == 'full_orchestrator' if 'parallel_mode' in p else
                        yes('boltzgen_parallel_mode'))
            expansion = {'authority': 'modules/boltzgen.nf:SpawnBoltzGenJobs',
                'child_model': 'boltzgen_child', 'child_mode': p.get('boltzgen_mode') or 'nanobody_binder',
                'child_stage': 'boltzgen',
                'candidate_identity': 'boltzgen child model/native design index',
                'grouping': {'num_designs': p.get('boltzgen_num_designs', 10),
                             'designs_per_job': p.get('boltzgen_designs_per_job', 100)},
                'join': 'WaitForBoltzGenChildren -> CollectBoltzGenOutputs -> AggregateBoltzGenResults'} if parallel else None
            if parallel:
                after = a.chain(['SpawnBoltzGenJobs'], after)
            run = a.stage('RunBoltzGen', after, expansion=expansion)
            after = a.chain(['CollectBoltzGenOutputs'], ('WaitForBoltzGenChildren',)) if parallel else (run,)
            after = a.chain(['FilterBoltzGen'], after)
            if parallel:
                after = a.chain(['AggregateBoltzGenResults'], after)
                a.callback(run, 'modules/boltzgen.nf:SpawnBoltzGenJobs')
        elif not any(yes(k) for k in ('skip_rfd', 'skip_rfd_seq', 'skip_rfd_seq_pred')):
            if p.get('diffusion_method', 'rfd3') != 'rfd3':
                unresolved(workflow, 'dependency_closure', entrypoint, 'Native workflow rejects the retired diffusion method')
            else:
                prep = 'PrepareGeneralRFD3Input' if p.get('rfd3_generation_request_path') else 'PrepRFD3Input'
                after = a.chain([prep, 'RunRFD3'])
                if p.get('rfd3_generation_request_path'):
                    a.stage('BuildGeneralRFD3ResultManifest', after)
                after = a.chain(['FilterRFD3'], after)
        if not sequence_prediction and not boltzgen and not generation_only and not any(yes(k) for k in ('skip_rfd_seq', 'skip_rfd_seq_pred')):
            seq = p.get('seq_method', 'fampnn')
            names = ['PrepMPNN', 'RunMPNN', 'FilterMPNN'] if seq == 'mpnn' else ['PrepFAMPNN', 'RunFAMPNN', 'FilterFAMPNN']
            after = a.chain(names, after, condition='!skip_rfd_seq && !skip_rfd_seq_pred && !run_rfd_only')
        if not sequence_prediction and not boltzgen and not generation_only and not yes('skip_rfd_seq_pred') and not yes('skip_pred'):
            if p.get('uncropped_target_pdb') and str(p.get('rfd_mode', '')).startswith('binder_'):
                after = a.chain(['MergeUncroppedTarget'], after)
            pred = p.get('pred_method', 'af2')
            if pred == 'af2':
                after = a.chain(['RunAF2', 'FilterAF2'], after)
                if str(p.get('rfd_mode', '')).startswith('binder_'):
                    after = a.chain(['AlignAF2'], after)
            elif pred == 'boltz':
                after = a.chain(['PrepBoltz', 'RunBoltz', 'AlignBoltz', 'FilterBoltz'], after)
                a.msa('boltz2', after)
            elif pred == 'rf3':
                after = a.chain(['RunRF3', 'FilterRF3'], after)
            else:
                unresolved(workflow, 'dependency_closure', entrypoint, 'Unsupported native design predictor: ' + str(pred))
        for producer in ('RunMPNN', 'RunFAMPNN', 'RunAF2', 'AlignBoltz'):
            if any(row.component_key == producer for row in components):
                a.stage('Compress', (producer,), condition='protein_design native run artifact publication')
        terminal = a.chain(['BindProteinDesignTerminalMetadata'], after)
        publish = a.chain(['StageProteinDesignPublishStructure'], after)
        metadata = a.chain(['CombineMetadata', 'ProjectProteinDesignMetadata'], terminal)
        a.chain(['PublishResults'], (*publish, *metadata))
        # Existing registry adds the exact native Frustra predicate/template.
        return True

    if workflow in {'orchestrator', 'replica', 'analyze'} and 'molecular_dynamics' in entrypoint:
        if workflow == 'analyze':
            a.stage('MD_ANALYZE_REPLICA')
            return True
        cfg = p.get('md_config')
        engine = p.get('md_engine') or (cfg.get('engine') if isinstance(cfg, dict) else None)
        if engine not in {'gromacs', 'openmm'}:
            unresolved('molecular_dynamics', 'dependency_closure', 'scripts/bms_md/contract.py:normalize_job_config',
                       'Bind normalized md_config.engine or native md_engine before selected engine provisioning')
        engines = [engine] if engine in {'gromacs', 'openmm'} else []
        retry = workflow == 'orchestrator' and bool(p.get('md_retry_spawn_receipt'))
        prep = a.chain(['MD_PREPARE_CONFIG']) if workflow == 'orchestrator' and not retry else ()
        if prep:
            a.asset('runtime_data', None, 'nextflow.config:params.md_preparation_runtime_lock', 'md_preparation_runtime_lock')
        spawn = a.chain(['MD_SPAWN_REPLICAS'], prep) if prep else ()
        for selected_engine in engines:
            process = 'MD_GROMACS_REPLICA' if selected_engine == 'gromacs' else 'MD_OPENMM_REPLICA'
            expansion = None if workflow == 'replica' else {
                'authority': 'scripts/bms_md/spawn_replicas.py; scripts/bms_md/contract.py',
                'child_model': 'molecular_dynamics', 'child_mode': 'replica', 'child_stage': 'md_replica',
                'candidate_identity': 'parent job + replica_index + normalized config digest',
                'grouping': 'one native engine replica per child, ordered replica_index',
                'count': cfg.get('replicas') if isinstance(cfg, dict) else None,
                'seed_authority': 'scripts/bms_md/contract.py; native per-replica random_seed',
                'join': 'MD_ASSERT_REPLICA_OUTCOME requires immutable completed replicas',
            }
            replica = a.stage(process, spawn, expansion=expansion)
            if workflow == 'orchestrator':
                wait = a.chain(['MD_WAIT_FOR_REPLICAS'], (*spawn, replica))
                collect = a.chain(['MD_COLLECT_REPLICAS', 'MD_ASSERT_REPLICA_OUTCOME'], wait)
                analysis_spawn = a.chain(['MD_SPAWN_ANALYSIS'], collect)
                analysis = a.stage('MD_ANALYZE_REPLICA', analysis_spawn, expansion={
                    'authority': 'scripts/bms_md/spawn_analysis.py',
                    'child_model': 'molecular_dynamics', 'child_mode': 'analyze', 'child_stage': 'md_analysis',
                    'candidate_identity': 'immutable replica aggregate + replica index',
                    'grouping': 'exactly one required analysis work item per replica',
                    'join': 'MD_ASSERT_ANALYSIS_OUTCOME; no success from trajectory bytes alone'})
                analysis_wait = a.chain(['MD_WAIT_FOR_ANALYSIS'], (*analysis_spawn, analysis))
                result = a.chain(['MD_COLLECT_ANALYSIS', 'MD_ASSERT_ANALYSIS_OUTCOME'], analysis_wait)
                a.stage('MD_COMPLETION_BARRIER', (*collect, *result))
                a.callback(replica, 'workflows/experimental/molecular_dynamics/orchestrator.nf:MD_SPAWN_REPLICAS/MD_SPAWN_ANALYSIS')
        return True

    if workflow == 'protein_cad_experimental':
        backend = p.get('pcad_backend') or 'disco'
        after = a.chain(['PrepProteinCadRequest', 'RunLaProteina' if backend == 'laproteina' else 'RunDISCO', 'FinalizeProteinCadOutputs'])
        if backend == 'laproteina':
            a.asset('runtime_data', 'laproteina', 'modules/protein_cad_experimental.nf:PrepProteinCadRequest', 'pcad_laproteina_data_path')
        return True

    if workflow == 'confornets_experimental':
        a.chain(['PrepConforNetsRequest', 'RunConforNets', 'FinalizeConforNetsOutputs'])
        a.asset('runtime_data', None, 'modules/confornets_experimental.nf:PrepConforNetsRequest', 'cn_confornets_repo_path')
        for selector in ('cn_confornet_path', 'cn_mse_dir', 'cn_source_test_cases'):
            if p.get(selector):a.asset('runtime_data', None, entrypoint, selector)
        if not yes('cn_skip_msa'):
            a.msa('protenix', ('PrepConforNetsRequest',))
        return True

    if workflow == 'boltz_cp_experimental':
        after = a.chain(['RunBoltzCPExperimental', 'FinalizeBoltzCPExperimental'])
        if p.get('bcp_repo_path'):
            a.asset('runtime_data', None, 'modules/boltz_cp_experimental.nf:RunBoltzCPExperimental', 'bcp_repo_path')
        a.msa('boltz2', ('RunBoltzCPExperimental',))
        if p.get('run_frustrampnn') is not False:
            a.frustra(after)
        return True

    if workflow == 'conformational_mapping':
        cfg = request('cm_request', 'workflows/conformational_mapping.nf:request.backend')
        after = ()
        if cfg:
            backend = cfg.get('backend')
            if backend == 'protenix_v2_ensemble':
                after = a.chain(['PrepareProtenixExecution', 'CanonicalProtenixEnsemble'])
                a.msa('protenix', after)
            elif backend == 'confornets':
                after = a.chain(['PrepCanonicalConforNetsRequest', 'RunCanonicalConforNets',
                    'FinalizeConforNetsOutputs', 'BindCanonicalConforNetsOutputLedger', 'FinalizeCanonicalConforNets'])
            elif backend == 'external_import':
                after = a.chain(['CanonicalConformationalImport'])
            else:
                unresolved(workflow, 'dependency_closure', entrypoint, 'Invalid native CM backend')
        after = a.chain(['PrepareConformationalMappingFrustraMPNNV2'], after)
        frustra = a.frustra(after, owner='cm_request.targets/ordered_seeds; workflow_component_request_v3')
        a.stage('CanonicalConformationalAnalysisPlaneV2', (*after, *frustra))
        return True

    if workflow == 'oligo_design':
        after = a.chain(['RFDPolyDesign', 'NAMPNNDesign', 'PyRosettaRebuild'])
        if yes('oligo_validate_boltz'):
            a.chain(['PrepBoltzOligo', 'RunBoltz', 'FilterBoltz'], after)
        return True

    if workflow == 'protein_local_redesign':
        native = bool(p.get('rfd3_request_path'))
        resume_validation = bool(p.get('plr_validation_input_pdbs'))
        resume_sequence = not resume_validation and bool(p.get('plr_sequence_input_pdbs'))
        resume_backbone = not resume_validation and not resume_sequence and bool(p.get('plr_backbone_input_pdbs'))
        after = ()
        if native:
            a.chain(['PrepareProteinLocalNativeRFD3Input', 'RunRFD3', 'BuildProteinLocalRFD3ResultManifest'])
            return True
        if not any((resume_validation, resume_sequence, resume_backbone)):
            after = a.chain(['ResolveProteinLocalRegion', 'PrepProteinLocalRFD3Input', 'RunRFD3', 'FilterRFD3', 'MergeProteinLocalComplexes'])
        if not resume_validation and not resume_sequence:
            if p.get('plr_seq_method', 'fampnn') == 'mpnn':
                after = a.chain(['PrepProteinLocalMPNN', 'RunMPNN', 'ExportProteinLocalMPNNResults', 'FilterMPNN'], after)
            else:
                after = a.chain(['PrepProteinLocalFAMPNN', 'RunFAMPNN', 'FilterFAMPNN'], after)
        if not resume_validation:
            validators = p.get('plr_structure_validators') or ['boltz2']
            if isinstance(validators, str):validators = [v.strip() for v in validators.split(',') if v.strip()]
            branches = []
            typed = a.chain(['PrepareProteinLocalValidatorInput'], after) if any(v != 'boltz2' for v in validators) else after
            for validator in validators:
                if validator == 'boltz2':
                    branches.extend(a.chain(['PrepBoltz', 'RunBoltz', 'ExportProteinLocalBoltzPredictions'], after))
                    a.msa('boltz2', after)
                elif validator == 'esmfold2':branches.extend(a.chain(['ESMFold2FromPdb'], typed))
                elif validator == 'protenix_v2':
                    branches.extend(a.chain(['ProtenixFromComplex'], typed)); a.msa('protenix', typed)
                else:unresolved(workflow, 'dependency_closure', entrypoint, 'Unsupported validator: ' + str(validator))
            after = a.chain(['FinalizeProteinLocalValidatorSuite', 'EnforceProteinLocalValidatorSuite', 'StageProteinLocalValidatedCandidates'], tuple(branches))
        if yes('interactive_gating') and not yes('interactive_gate_continue'):
            a.stage('OpenInteractiveGate', after, source=entrypoint,
                condition='interactive_gating && !interactive_gate_continue; native interactive_gate_stage',
                expansion={'authority': entrypoint + ':OpenInteractiveGate', 'grouping': 'selected stage artifacts',
                           'candidate_identity': 'checkpoint + selected artifact digest', 'join': 'explicit operator decision only'})
            a.callback('OpenInteractiveGate', 'scripts/open_stage_gate.py')
        return True

    if workflow in {'ppiflow_generator_design', 'maturation_child'}:
        after = a.chain(['IdentifyAnchorResidues', 'RunPartialFlow', 'ScorePartialFlowImprovement'])
        if workflow == 'maturation_child':
            # Preserve the native redesign predicate and top-N ranking authority.
            redesign = p.get('maturation_redesign_enabled') is not False and p.get('ppiflow_mode') != 'backbone_refine'
            if redesign:
                after = a.chain(['ANARCII', 'PrepMaturationRedesign', 'RunMaturationFAMPNN', 'ScoreMaturationImprovement'], after,
                                condition='maturation_child_core.runRedesign && redesign_enabled')
        after = a.chain(['FilterByMaturation'], after)
        if workflow == 'ppiflow_generator_design':
            a.stage('CollectPPIFlowGeneratorRaw', ('ScorePartialFlowImprovement',))
            a.stage('CollectPPIFlowGeneratorFiltered', after)
            if yes('interactive_gating') or yes('interactive_swa'):
                a.stage('OpenInteractiveGate', after, source=entrypoint, condition='interactiveGateEnabled')
                a.callback('OpenInteractiveGate', 'scripts/open_stage_gate.py')
        return True

    if workflow == 'rfantibody_backbone':
        a.chain(['NormalizeTargetPDB'], source=entrypoint)
        a.stage('RFANTIBODY', ('NormalizeTargetPDB',))
        return True

    if workflow == 'antibody_child':
        after = validation((), str(p.get('structure_validator') or 'boltz2').lower())
        if yes('run_thermompnn'):a.chain(['BatchStability'], after)
        if yes('run_immunogenicity_scoring'):a.chain(['BatchImmunogenicity'], after)
        return True

    if workflow == 'antibody_denovo':
        after = ()
        if not p.get('target_pdb') and p.get('target_protein_seq'):
            after = a.chain(['PredictTargetComplex']); a.msa('boltz2', after)
        after = a.chain(['NormalizeTargetPDB'], after, source=entrypoint)
        skip_backbone = yes('skip_rfantibody') or bool(p.get('selected_input_dir') or p.get('rfantibody_input_pdbs') or p.get('fampnn_collected_pdbs'))
        if not skip_backbone:
            expansion = {'authority': entrypoint + ':SpawnRFantibodyJobs',
                'child_model': 'rfantibody_child', 'child_mode': 'antibody_backbone', 'child_stage': 'rfantibody',
                'candidate_identity': 'child job/native backbone design index',
                'grouping': {'total_designs': p.get('rfantibody_num_designs', 10), 'designs_per_job': p.get('designs_per_job', 5)},
                'join': 'WaitForChildren -> CollectChildOutputs; native yield check'} if p.get('parallel_mode') == 'full_orchestrator' else None
            after = (a.stage('RFANTIBODY', after, expansion=expansion),)
            if expansion:a.callback('RFANTIBODY', entrypoint + ':SpawnRFantibodyJobs')
        after = a.chain(['StageRFantibodyBackbones'], after)
        if yes('enable_rfantibody_filter'):
            after = a.chain(['ScreenRFantibodyBackbones'], after)
        if not skip_backbone or yes('enable_rfantibody_filter'):
            after = a.chain(['CheckRFantibodyYield'], after)
        if yes('run_ppiflow_backbone_refine') or str(p.get('ppiflow_stage') or '').lower() in {'post_rfantibody', 'backbone_refine', 'ppiflow_backbone_refine'}:
            after = a.chain(['IdentifyAnchorResidues'], after)
            after = (a.stage('RunPartialFlow', after, expansion={
                'child_model': 'template_antibody_denovo', 'child_mode': 'maturation_child', 'child_stage': 'backbone_refine',
                'authority': entrypoint + ':SpawnMaturationJobs', 'candidate_identity': 'source backbone/selected refinement sample',
                'grouping': 'native maturation_designs_per_job',
                'join': 'WaitForMaturationChildren -> CollectMaturationOutputs'}),)
            after = a.chain(['ScorePartialFlowImprovement', 'FilterByMaturation'], after)
            a.callback('RunPartialFlow', entrypoint + ':SpawnMaturationJobs')
        branches = []
        artifact_class = str(p.get('selected_input_artifact_class') or '').strip().lower()
        stage_family = str(p.get('selected_input_stage_family') or p.get('source_stage_family') or '').strip().lower()
        conditioned = artifact_class in {'sequence_designed_complex', 'validated_complex', 'post_validation_refined_complex'} or (not artifact_class and p.get('fampnn_collected_pdbs') is not None)
        precollected = (yes('interactive_gate_continue') or p.get('resume_job_id') is not None) and conditioned and bool(p.get('selected_input_dir') or p.get('rfantibody_input_pdbs') or p.get('fampnn_collected_pdbs')) and stage_family in {'', 'fampnn'}
        if precollected and yes('seq_design_fampnn', True) and p.get('enable_fampnn_filter') is not False and any(p.get(k) is not None for k in ('fampnn_max_psce', 'fampnn_max_residue_psce')):
            branches.extend(a.chain(['FilterFAMPNN']))
        if yes('seq_design_fampnn', True) and not precollected:
            prep = a.chain(['PrepFAMPNN'], after)
            run = a.stage('RunFAMPNN', prep, expansion={'authority': entrypoint + ':SpawnFAMPNNJobs',
                'child_model': 'fampnn_child', 'child_mode': 'sequence_design', 'child_stage': 'fampnn',
                'candidate_identity': 'source backbone + native FAMPNN sample',
                'grouping': {'pdbs_per_job': p.get('pdbs_per_job', 5)},
                'analysis_policy': 'lib/FampnnAnalysisPolicy.groovy:authorized_sequence_design_region',
                'join': 'WaitForFAMPNNChildren -> CollectFAMPNNOutputs'})
            a.callback(run, entrypoint + ':SpawnFAMPNNJobs')
            fampnn_after = (run,)
            if p.get('enable_fampnn_filter') is not False and any(p.get(k) is not None for k in ('fampnn_max_psce', 'fampnn_max_residue_psce')):
                fampnn_after = a.chain(['FilterFAMPNN'], fampnn_after)
            branches.extend(fampnn_after)
        if yes('seq_design_antifold', True):branches.extend(a.chain(['ANARCII', 'ANTIFOLD'], after))
        if yes('seq_design_proteinmpnn', True):branches.extend(a.chain(['PrepMPNN', 'RunMPNN'], after))
        if yes('seq_design_caliby'):
            unresolved('caliby', 'availability', 'platform/api/config/models/caliby_experimental.yaml',
                       'Disabled native model is not enabled by workflow descriptor')
        after = tuple(branches) or after
        if yes('run_ppiflow_maturation', yes('run_maturation')):
            a.callback('maturation', entrypoint + ':SpawnMaturationJobs')
            # Bind at the unconditional child stage, retaining optional redesign.
            after = a.chain(['IdentifyAnchorResidues'], after)
            after = (a.stage('RunPartialFlow', after, expansion={
                'child_model': 'template_antibody_denovo', 'child_mode': 'maturation_child', 'child_stage': 'maturation',
                'authority': 'workflows/maturation_child_core.nf', 'candidate_identity': 'source backbone/selected maturation sample',
                'grouping': 'native anchor admission and top-N redesign ranking',
                'join': 'WaitForMaturationChildren -> CollectMaturationOutputs'}),)
            after = a.chain(['ScorePartialFlowImprovement'], after)
            if p.get('maturation_redesign_enabled') is not False:
                after = a.chain(['ANARCII', 'PrepMaturationRedesign', 'RunMaturationFAMPNN', 'ScoreMaturationImprovement'], after)
            after = a.chain(['FilterByMaturation'], after)
        if yes('run_thermompnn'):after = a.chain(['THERMOMPNN'], after)
        if yes('run_af2_backprop'):after = a.chain(['MergeComplex', 'AF2_BACKPROP'], after)
        if p.get('run_structure_validation') is not False:
            after = validation(after, str(p.get('structure_validator') or 'boltz2').lower())
            after = a.chain(['FinalizeSequentialValidationOutputs', 'StageStructureValidationArtifacts'], after)
            if yes('run_post_validation_maturation'):
                after = a.chain(['IdentifyAnchorResidues'], after)
                after = (a.stage('RunPartialFlow', after, expansion={
                    'child_model': 'template_antibody_denovo', 'child_mode': 'maturation_child', 'child_stage': 'maturation_post_validation',
                    'authority': entrypoint + ':SpawnValidatedMaturationJobs', 'candidate_identity': 'validated structure/selected maturation sample',
                    'grouping': 'native maturation_designs_per_job',
                    'join': 'WaitForValidatedMaturationChildren -> CollectValidatedMaturationOutputs'}),)
                after = a.chain(['ScorePartialFlowImprovement'], after)
                if p.get('maturation_redesign_enabled') is not False:
                    after = a.chain(['ANARCII', 'PrepMaturationRedesign', 'RunMaturationFAMPNN', 'ScoreMaturationImprovement'], after)
                after = a.chain(['FilterByMaturation'], after)
                a.callback('maturation_post_validation', entrypoint + ':SpawnValidatedMaturationJobs')
        else:
            after = a.chain(['FinalizeTerminalAntibodyOutputs'], after)
        if yes('openmm_enabled'):
            after = a.chain(['OpenMMRelaxation'], after)
            if p.get('openmm_compute_tier') == 'full' or p.get('openmm_mmgbsa_mode') != 'off':
                a.stage('OpenMMScore', after, condition="openmm_compute_tier == 'full' || openmm_mmgbsa_mode != 'off'")
        if p.get('run_immunogenicity_scoring') is not False:
            after = a.chain(['ANTIBERTY_SCORE'], after)
            if p.get('filter_immunogenic') is not False:after = a.chain(['ANTIBERTY_FILTER_STRUCTURES'], after)
        if yes('run_affinity_maturation'):after = a.chain(['IGGM_AFFINITY_MATURATION'], after)
        if yes('run_frustrampnn'):a.frustra(after)
        if yes('interactive_gating') or yes('interactive_swa'):
            a.stage('OpenInteractiveGate', after, source=entrypoint, condition='interactiveGateEnabled; native stage predicates')
            a.callback('OpenInteractiveGate', 'scripts/open_stage_gate.py')
        # Reporting/annotation calls in this native workflow are not replaced.
        a.callback(workflow, entrypoint + ':stage_reporter/ANARCII annotation authority')
        return True

    if workflow == 'shape_blueprint_design':
        cfg = request('shape_request', entrypoint + ':shapeRequest')
        prep = a.chain(['ValidateShapeBundle', 'PlanRFD3Batches', 'RunShapeRFD3', 'AdmitRFD3InitialCandidate'])
        aggregate = a.chain(['BuildRFD3Aggregate'], prep)
        after = a.chain(['PrepareShapeBackbone'], prep, condition='admission.status == accepted')
        if cfg and cfg.get('sequence_policy', 'auto') != 'skip' and cfg.get('sequences_per_backbone', 0) > 0:
            seq = cfg.get('sequence_engine') or 'proteinmpnn'
            sequences = a.chain(['RunShapeProteinMPNN' if seq == 'proteinmpnn' else 'RunShapeFAMPNN'], after)
            after = a.chain(['ESMFold2Predict'], sequences)
            evidence_sources = list(after)
            suite = cfg.get('validator_suite') or []
            if 'boltz2' in suite:
                evidence_sources.append(a.stage('RunShapeBoltzValidator', sequences))
            if 'protenix_v2' in suite:
                evidence_sources.append(a.stage('RunShapeProtenixValidator', sequences))
            evidence = a.chain(['AggregateShapeValidatorEvidence'], tuple(evidence_sources))
            after = a.chain(['EvaluateShapeCandidate'], after)
            after = a.chain(['AttachShapePostRefold'], (*after, *evidence))
        elif cfg:
            after = a.chain(['BuildShapeSkipBundle'], after)
        a.stage('BuildShapeResult', (*after, *aggregate))
        return True

    if workflow == 'frustrampnn_analysis':
        after = a.chain(['PreparePersistedFrustraMPNNCandidate'])
        a.frustra(after)
        a.chain(['PublishPersistedFrustraMPNNGroupedBundles', 'ReportPersistedFrustraMPNNComplete'], ('RunPersistedFrustraMPNNGroupedBatch',))
        return True

    if workflow.startswith('ont_') or workflow == 'wf_clone_validation':
        return _append_ngs(a, workflow, yes)
    return False



def _append_ngs(a, workflow, yes):
    p = a.p
    if workflow == 'ont_pooled_reference_assignment':
        a.stage('ONTPooledReferenceAssignment')
        return True
    basecall = workflow in {'ont_basecall_dna', 'ont_basecall_rna'}
    pod5 = bool(p.get('pod5_dir')) or basecall
    bam = bool(p.get('bam_path'))
    fastq = bool(p.get('fastq_path')) or workflow == 'ont_fastq_qc'
    reference = bool(p.get('reference_fasta'))
    run_qc = p.get('run_fastq_qc', p.get('run_multimer_qc', True)) is not False
    after = ()
    if pod5:
        after = a.chain(['DoradoPreflight', 'DoradoBasecall'])
        if workflow == 'ont_basecall_dna' and p.get('barcode_kit'):
            a.chain(['DoradoDemux'], after)
        if reference:
            after = a.chain(['DoradoAlign'], after)
        elif not basecall:
            after = a.chain(['PrepareBamForAnalysis'], after)
    elif bam:
        after = a.chain(['DoradoAlign' if reference and yes('bam_force_realign') else 'PrepareBamForAnalysis'])
        if reference and workflow in {'ont_construct_screening', 'wf_clone_validation', 'ont_methylation_analysis'}:
            after = a.chain(['ValidateMappedBam'], after)
    elif fastq:
        after = a.chain(['FastqAlign'])
    else:
        a.unresolved(workflow, 'dependency_closure', a.entrypoint,
                     'Native compiler must identify exactly one POD5/BAM/FASTQ input branch')
    if basecall:
        return True
    if workflow == 'ont_methylation_analysis':
        if reference:
            a.stage('PrepareReferenceForIGV')
            after = a.chain(['ValidateModifiedBaseBam'], after)
            a.stage('ModkitPileup', after)
            a.stage('ModkitSummary', after)
        return True
    if workflow == 'wf_clone_validation' or (workflow == 'ont_construct_screening' and yes('run_assembly')):
        clone = a.chain(['RunCloneValidation'], after)
        # This is an existing nested pinned workflow, not an arbitrary runner.
        a.asset('runtime_data', 'wf-clone-validation', 'modules/ngs/clone_validation.nf:/mnt/BioModStack/ngs/wf-clone-validation/v1.8.4-bms.1')
        a.unresolved('RunCloneValidation', 'dependency_closure',
            'modules/ngs/clone_validation.nf:RunCloneValidation',
            'Bind existing wf-clone runtime release and all selected nested process SIFs from native runtime provenance before provisioning')
        if workflow == 'wf_clone_validation':
            a.chain(['CloneValidationAdapter', 'ConstructVerify'], clone)
    if workflow == 'ont_plasmid_qc' and bam and reference:
        a.stage('PrepareReferenceForIGV')
    # FASTQ QC always creates native dimer evidence; plasmid/construct do so
    # under their native QC predicate and require an aligned reference.
    dimer = workflow == 'ont_fastq_qc' or (run_qc and reference and
             (workflow == 'ont_plasmid_qc' or fastq))
    if dimer:
        reads = a.chain(['BamToFastqForQC'], after) if not fastq else ()
        dimer_after = a.chain(['FastqDimerAnalysis', 'BuildDimerCanonicalOutputs'], reads)
        if run_qc:
            qc = a.chain(['FastqPlasmidQC'], tuple(dict.fromkeys((*after, *reads))))
            if workflow != 'wf_clone_validation':
                a.chain(['ConstructVerify'], (*qc, *dimer_after))
            if p.get('comparison_panel_snapshot') and workflow != 'wf_clone_validation':
                a.stage('ComparisonPanelAttribution', reads)
    return True
