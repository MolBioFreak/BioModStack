from __future__ import annotations

import re
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent

INCLUDE_RE = re.compile(
    r"include\s*\{(?P<symbols>.*?)\}\s*from\s*['\"](?P<source>[^'\"]+)['\"]",
    re.S,
)

DIRECT_ENTRYPOINTS = {
    "oligo_design": "workflows/oligo_design.nf",
    "nanopore_methylation": "workflows/ngs/ont_methylation_analysis.nf",
    "ont_basecall_dna": "workflows/ngs/ont_basecall_dna.nf",
    "ont_basecall_rna": "workflows/ngs/ont_basecall_rna.nf",
    "ont_plasmid_qc": "workflows/ngs/ont_plasmid_qc.nf",
    "ont_construct_screening": "workflows/ngs/ont_construct_screening.nf",
    "ont_methylation_analysis": "workflows/ngs/ont_methylation_analysis.nf",
    "ont_fastq_qc": "workflows/ngs/ont_fastq_qc.nf",
    "protein_local_redesign": "workflows/protein_local_redesign.nf",
    "protein_cad_experimental": "workflows/protein_cad_experimental.nf",


    "boltz_cp_experimental": "workflows/boltz_cp_experimental.nf",
    "confornets_experimental": "workflows/confornets_experimental.nf",

    "protein_design": "workflows/protein_design.nf",
    "structure_prediction": "workflows/structure_prediction.nf",
    "complex_prediction": "workflows/complex_prediction.nf",
    "ppiflow_generator": "workflows/ppiflow_generator_design.nf",

    "docking": "workflows/docking.nf",
    "antibody_child": "workflows/antibody_child.nf",
    "antibody_child": "workflows/antibody_child.nf",
    "rfantibody_backbone": "workflows/rfantibody_backbone.nf",
    "fampnn_child": "workflows/fampnn_child.nf",
    "maturation_child": "workflows/maturation_child.nf",
}


def test_protein_local_fampnn_batches_process_output_without_collect_group_operator_collision() -> None:
    source = (REPO_ROOT / "workflows" / "protein_local_redesign.nf").read_text(encoding="utf-8")

    assert "PrepProteinLocalFAMPNN.out.pdbs\n            .map { pdbs -> [0, pdbs] }\n            .set { fampnnPdbs }" in source
    assert "PrepProteinLocalFAMPNN.out.pdbs\n            .collect()" not in source


def test_protein_local_redesign_shell_quotes_every_native_dynamic_argument() -> None:
    source = (REPO_ROOT / "workflows" / "protein_local_redesign.nf").read_text(encoding="utf-8")

    assert "def shellQuote(value)" in source
    assert "--input-structure ${inputStructureArg}" in source
    assert "--source-file ${sourceFileArg}" in source
    assert "--source-storage-path ${sourceStorageArg}" in source
    assert '--input-structure "${input_structure}"' not in source
    assert '--source-file "${source_structure}"' not in source
    assert '--source-storage-path "${params.plr_input_pdb}"' not in source
    assert "def nativeRfd3Request = params.rfd3_request_path ? true : false" in source
    assert "def nativeSequenceMethod = nativeRfd3Request ? 'skip'" in source
    assert "def sequenceMethod = nativeRfd3Request ? 'skip'" in source
    assert "Native RFD3 local redesign does not accept resume inputs" in source
    assert "Native RFD3 local redesign does not accept interactive gating" in source

    module_source = (REPO_ROOT / "modules" / "rfd3.nf").read_text(encoding="utf-8")
    assert "def nativeRequest = params.rfd3_request_path ? true : false" in module_source
    assert "params.rfd3_request_path && params.plr_redesign_mode" not in module_source

MIGRATED_SYMBOLS = (
    "OLIGO_DESIGNER",
    "PROTEIN_LOCAL_REDESIGN",
    "PROTEIN_CAD_EXPERIMENTAL",
    "CALIBY_EXPERIMENTAL",
    "PROTEIN_HUNTER_EXPERIMENTAL",
    "BOLTZ_CP_EXPERIMENTAL",
    "CONFORNETS_EXPERIMENTAL",
    "ESMFOLD2_EXPERIMENTAL",
    "STRUCTURE_PREDICTION",
    "COMPLEX_PREDICTION",
    "PPIFLOW_GENERATOR_DESIGN",
    "BOLTZGEN_DESIGN",
    "DOCKING",
    "ANTIBODY_CHILD",
    "RFANTIBODY_BACKBONE",
    "FAMPNN_CHILD",
    "MATURATION_CHILD",
)

FORBIDDEN_MAIN_NGS_TERMS = (
    "nanopore",
    "dorado",
    "modkit",
    "methylation",
    "clone_validation",
    "fastq",
    "bam_path",
    "reference_fasta",
    "ngs.nf",
)

FORBIDDEN_MAIN_WORKFLOW_TERMS = (
    "params.rfd_mode",
    "RunBoltz",
    "RunRF3",
    "RunDiffDock",
    "RunUniDock",
    "RunBoltzGen",
    "RFANTIBODY",
    "FAMPNN_CHILD",
)


def _nextflow_files() -> list[Path]:
    candidates = list(REPO_ROOT.glob("*.nf"))
    for dirname in ("workflows", "modules", "subworkflows"):
        base = REPO_ROOT / dirname
        if base.exists():
            candidates.extend(base.rglob("*.nf"))
    return sorted(set(candidates))


def _resolve_include(source_file: Path, source: str) -> Path | None:
    base = (source_file.parent / source).resolve()
    candidates = [base] if base.suffix else [Path(f"{base}.nf"), base]
    return next((candidate for candidate in candidates if candidate.exists()), None)


def _included_symbols(symbol_block: str) -> list[str]:
    symbols: list[str] = []
    for raw_symbol in symbol_block.replace("\n", ";").split(";"):
        symbol = raw_symbol.split("//", 1)[0].strip()
        if not symbol:
            continue
        symbol = re.split(r"\s+as\s+", symbol)[0].strip()
        if symbol:
            symbols.append(symbol)
    return symbols


def test_nextflow_include_targets_and_symbols_resolve() -> None:
    missing_targets: list[str] = []
    missing_symbols: list[str] = []

    for source_file in _nextflow_files():
        source_text = source_file.read_text(encoding="utf-8", errors="ignore")
        for match in INCLUDE_RE.finditer(source_text):
            include_source = match.group("source")
            target_file = _resolve_include(source_file, include_source)
            if target_file is None:
                missing_targets.append(f"{source_file.relative_to(REPO_ROOT)} -> {include_source}")
                continue

            target_text = target_file.read_text(encoding="utf-8", errors="ignore")
            for symbol in _included_symbols(match.group("symbols")):
                declaration = rf"\b(workflow|process)\s+{re.escape(symbol)}\s*\{{"
                if not re.search(declaration, target_text):
                    missing_symbols.append(
                        f"{source_file.relative_to(REPO_ROOT)} imports {symbol} from {target_file.relative_to(REPO_ROOT)}"
                    )

    assert missing_targets == []
    assert missing_symbols == []


def test_direct_workflow_entrypoints_exist_and_aggregate_bucket_is_absent() -> None:
    assert not (REPO_ROOT / "experimental.nf").exists()
    for workflow_id, rel_path in DIRECT_ENTRYPOINTS.items():
        assert (REPO_ROOT / rel_path).exists(), f"{workflow_id} -> {rel_path}"


def test_main_entrypoint_is_only_a_thin_compatibility_wrapper() -> None:
    main_text = (REPO_ROOT / "main.nf").read_text(encoding="utf-8")
    main_lower = main_text.lower()

    assert len(main_text.splitlines()) <= 12
    assert "include { PROTEIN_DESIGN } from './workflows/protein_design.nf'" in main_text
    assert "workflow {" in main_text
    assert "PROTEIN_DESIGN()" in main_text

    for symbol in MIGRATED_SYMBOLS:
        assert symbol not in main_text
    for term in FORBIDDEN_MAIN_NGS_TERMS:
        assert term not in main_lower
    for term in FORBIDDEN_MAIN_WORKFLOW_TERMS:
        assert term not in main_text


def test_core_protein_design_is_direct_entrypoint_not_root_main() -> None:
    protein_design_text = (REPO_ROOT / "workflows" / "protein_design.nf").read_text(
        encoding="utf-8",
        errors="ignore",
    )
    assert re.search(r"(?m)^\s*workflow\s+PROTEIN_DESIGN\s*\{", protein_design_text)
    assert re.search(r"(?m)^\s*workflow\s*\{", protein_design_text)
    assert "include { PROTEIN_DESIGN }" not in protein_design_text


def test_direct_workflow_entrypoints_expose_unnamed_workflows() -> None:
    for workflow_id, rel_path in DIRECT_ENTRYPOINTS.items():
        entrypoint_text = (REPO_ROOT / rel_path).read_text(encoding="utf-8", errors="ignore")
        assert re.search(r"(?m)^\s*workflow\s*\{", entrypoint_text), workflow_id


def test_protein_local_redesign_uses_peer_validator_suite_contract() -> None:
    workflow_text = (REPO_ROOT / "workflows" / "protein_local_redesign.nf").read_text(encoding="utf-8")
    esmfold_text = (REPO_ROOT / "modules" / "esmfold2_experimental.nf").read_text(encoding="utf-8")
    model_text = (REPO_ROOT / "platform" / "api" / "config" / "models" / "protein_modification_experimental.yaml").read_text(encoding="utf-8")

    assert "['boltz2', 'esmfold2', 'protenix_v2']" in workflow_text
    assert "parseProteinLocalValidators(params.plr_structure_validators)" in workflow_text
    assert "selectedValidators.contains('boltz2')" in workflow_text
    assert "selectedValidators.contains('esmfold2')" in workflow_text
    assert "selectedValidators.contains('protenix_v2')" in workflow_text
    assert "FinalizeProteinLocalValidatorSuite" in workflow_text
    assert "process EnforceProteinLocalValidatorSuite" in workflow_text
    assert "validator_suite_complete" in workflow_text
    assert "contract_root: 'validation/contracts'" in workflow_text
    assert "artifact_root:" in workflow_text
    assert "StageProteinLocalValidatedCandidates" in workflow_text
    assert "validation/review_candidates" in workflow_text
    assert "StageProteinLocalValidatedCandidates.out.candidates.map { 1 }" in workflow_text
    assert "['complete', 'partial', 'failed']" in workflow_text
    assert "validation/boltz2" in workflow_text
    assert "process ESMFold2FromPdb" in esmfold_text
    assert "validation/esmfold2" in esmfold_text
    assert "plr_run_boltz_validation" not in workflow_text
    assert "enum: [boltz2, esmfold2, protenix_v2]" in model_text


def test_protein_local_validator_failure_policy_is_scoped_to_plr() -> None:
    workflow_text = (REPO_ROOT / "workflows" / "protein_local_redesign.nf").read_text(encoding="utf-8")
    boltz_text = (REPO_ROOT / "modules" / "boltz.nf").read_text(encoding="utf-8")
    protenix_text = (REPO_ROOT / "modules" / "protenix.nf").read_text(encoding="utf-8")

    scoped_policy = "params.containsKey('plr_validator_suite_active') && params.plr_validator_suite_active == true ? 'ignore' : 'terminate'"
    assert scoped_policy in boltz_text
    assert scoped_policy in protenix_text
    assert "'validation/protenix_v2/' + input_sample.candidate_id" in protenix_text
    assert "params.plr_structure_validators ? 'ignore'" not in boltz_text
    assert "params.plr_structure_validators ? 'ignore'" not in protenix_text
    assert "error(\"Boltz-2 produced" not in workflow_text
    assert "boltz_completion.json" in boltz_text
    assert "RunBoltz.out.completion" in workflow_text
    assert "and Path('predictions', f'{candidate_id}_boltzpred.json').is_file()" in boltz_text
    assert ") + '\\\\n'," in boltz_text
