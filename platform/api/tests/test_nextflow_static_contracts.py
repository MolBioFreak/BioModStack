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


def test_region_loop_shell_quotes_current_source_and_selection_arguments() -> None:
    source = (REPO_ROOT / "workflows/protein_local_redesign.nf").read_text()
    assert "def shellQuote(value)" in source
    for argument in (
        "--input-pdb ${shellQuote(source_pdb)}",
        "--model-seeds ${shellQuote(params.protenix_seeds ?: '42')}",
        "--manifest ${shellQuote(params.plr_region_manifest)}",
        "--fixed_positions ${shellQuote(params.plr_fixed_positions)}",
        "--designable_positions ${shellQuote(params.plr_designable_positions)}",
        "--design_chains ${shellQuote(params.plr_design_chains)}",
        "--fixed-positions ${shellQuote(params.plr_fixed_positions)}",
        "--designable-positions ${shellQuote(params.plr_designable_positions)}",
        "--design-chains ${shellQuote(params.plr_design_chains)}",
    ):
        assert argument in source
    # Generation no longer belongs to the Resequence workflow.
    assert "rfd3_local_redesign/prepare" not in source
    assert "rfd3_local_redesign/build_result_manifest.py" not in source

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


def _include_errors(source_files: list[Path], root: Path) -> tuple[list[str], list[str]]:
    missing_targets: list[str] = []
    missing_symbols: list[str] = []
    for source_file in source_files:
        source_text = source_file.read_text(encoding="utf-8")
        for match in INCLUDE_RE.finditer(source_text):
            include_source = match.group("source")
            target_file = _resolve_include(source_file, include_source)
            if target_file is None:
                missing_targets.append(f"{source_file.relative_to(root)} -> {include_source}")
                continue
            target_text = target_file.read_text(encoding="utf-8")
            # Ignore commented declarations; this remains a bounded declaration
            # inventory, not a replacement for the Nextflow/Groovy parser.
            target_text = re.sub(r"(?m)^[ \t]*(?:/\*.*?\*/|//[^\n]*)", "", target_text, flags=re.S)
            for symbol in _included_symbols(match.group("symbols")):
                name = re.escape(symbol)
                declaration = rf"(?m)^\s*(?:(?:workflow|process)\s+{name}\s*\{{|def\s+{name}\s*\([^)]*\)\s*\{{)"
                if not re.search(declaration, target_text):
                    missing_symbols.append(
                        f"{source_file.relative_to(root)} imports {symbol} from {target_file.relative_to(root)}"
                    )
    return missing_targets, missing_symbols


def test_nextflow_include_targets_and_symbols_resolve() -> None:
    assert _include_errors(_nextflow_files(), REPO_ROOT) == ([], [])


def test_include_scanner_accepts_functions_processes_workflows_and_aliases(tmp_path: Path) -> None:
    target = tmp_path / "owner.nf"
    target.write_text(
        "def helper(value) { return value }\n"
        'process Run { output: path "predictions/*.pdb" }\n'
        "workflow FLOW { }\n"
    )
    source = tmp_path / "entry.nf"
    source.write_text("include { helper as renamed; Run; FLOW } from './owner'\n")
    assert _include_errors([source], tmp_path) == ([], [])


def test_include_scanner_rejects_missing_target(tmp_path: Path) -> None:
    source = tmp_path / "entry.nf"
    source.write_text("include { helper } from './missing'\n")
    assert _include_errors([source], tmp_path) == (["entry.nf -> ./missing"], [])


def test_include_scanner_rejects_missing_or_commented_symbol(tmp_path: Path) -> None:
    (tmp_path / "owner.nf").write_text(
        "def helper(value) { return value }\n"
        "// def absent(value) { return value }\n"
        "/* workflow absent { } */\n"
    )
    source = tmp_path / "entry.nf"
    source.write_text("include { helper; absent as renamed } from './owner.nf'\n")
    assert _include_errors([source], tmp_path) == (
        [], ["entry.nf imports absent from owner.nf"]
    )


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
    # Region preparation/padding was removed from this validation-only owner;
    # zero-valued padding transport is covered at build_nextflow_command.
    assert "samples = json.loads(Path('predictions/boltz_samples.manifest').read_text())['samples']" in boltz_text
    assert "completed = sorted({row['source_id'] for row in samples if row['confidence_present']})" in boltz_text
    assert ") + '\\\\n'," in boltz_text


def test_protein_local_boltz_batches_use_flatmap_so_group_tuple_gets_pairs() -> None:
    source = (REPO_ROOT / "workflows" / "protein_local_redesign.nf").read_text(encoding="utf-8")

    assert ".map { allPdbs -> partitionProteinLocalGpuBatches(allPdbs, params.gpus) }\n                .flatMap { batches -> batches }\n                .groupTuple()" in source
    assert "partitionProteinLocalGpuBatches(allPdbs, params.gpus) }\n                .flatten()" not in source
