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
    "caliby_experimental": "workflows/caliby_experimental.nf",
    "protein_hunter_experimental": "workflows/protein_hunter_experimental.nf",
    "boltz_cp_experimental": "workflows/boltz_cp_experimental.nf",
    "confornets_experimental": "workflows/confornets_experimental.nf",
    "esmfold2_experimental": "workflows/esmfold2_experimental.nf",
    "protein_design": "workflows/protein_design.nf",
    "structure_prediction": "workflows/structure_prediction.nf",
    "complex_prediction": "workflows/complex_prediction.nf",
    "bindcraft_design": "workflows/bindcraft_design.nf",
    "ppiflow_generator": "workflows/ppiflow_generator_design.nf",
    "boltzgen_design": "workflows/boltzgen_design.nf",
    "docking": "workflows/docking.nf",
    "antibody_denovo": "workflows/antibody_denovo.nf",
    "antibody_child": "workflows/antibody_child.nf",
    "rfantibody_backbone": "workflows/rfantibody_backbone.nf",
    "fampnn_child": "workflows/fampnn_child.nf",
    "maturation_child": "workflows/maturation_child.nf",
}

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
    "BINDCRAFT_DESIGN",
    "PPIFLOW_GENERATOR_DESIGN",
    "BOLTZGEN_DESIGN",
    "DOCKING",
    "ANTIBODY_DENOVO",
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
    "ANTIBODY_DENOVO",
    "BINDCRAFT_DESIGN",
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
