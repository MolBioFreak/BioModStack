"""ViennaRNA-backed RNA secondary structure analysis."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional


MAX_GLOBAL_FOLD_LENGTH = 2000
MAX_PARTITION_LENGTH = 1200
MAX_BOUNDED_FOLD_LENGTH = 5000
MAX_BP_SPAN = 1000

CANONICAL_RNA_BASES = frozenset({"A", "U", "G", "C"})


class RnaStructureError(ValueError):
    """Raised when an RNA folding request is invalid."""


@dataclass(slots=True)
class RnaStructureSettings:
    temperature_c: float = 37.0
    no_lonely_pairs: bool = False
    dangles: int = 2
    circular: bool = False
    max_bp_span: Optional[int] = None
    gamma: float = 1.0
    probability_cutoff: float = 0.02
    max_pairs: int = 800
    shape_method: Optional[str] = None
    shape_slope: float = 1.8
    shape_intercept: float = -0.6
    shape_reactivities: Optional[list[Optional[float]]] = None
    hard_constraints: Optional[str] = None


def default_structure_settings() -> RnaStructureSettings:
    return RnaStructureSettings()


def structure_limits() -> dict[str, int]:
    return {
        "max_global_fold_length": MAX_GLOBAL_FOLD_LENGTH,
        "max_partition_length": MAX_PARTITION_LENGTH,
        "max_bounded_fold_length": MAX_BOUNDED_FOLD_LENGTH,
        "max_bp_span": MAX_BP_SPAN,
    }


def _load_viennarna() -> Any:
    try:
        import RNA  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on runtime env
        raise RuntimeError("ViennaRNA is not installed in the API environment") from exc
    return RNA


def normalize_rna_sequence(sequence: str) -> str:
    compact = "".join(sequence.upper().split())
    if not compact:
        raise RnaStructureError("RNA sequence is empty after whitespace removal")

    invalid = sorted({base for base in compact if base not in CANONICAL_RNA_BASES})
    if invalid:
        raise RnaStructureError(
            "RNA folding currently requires canonical RNA bases A/U/G/C only; "
            f"found {', '.join(invalid)}"
        )

    return compact


def _validate_problem_size(sequence_length: int, settings: RnaStructureSettings, include_partition: bool) -> None:
    if settings.max_bp_span is not None:
        if settings.max_bp_span < 2:
            raise RnaStructureError("max_bp_span must be at least 2 when provided")
        if settings.max_bp_span > MAX_BP_SPAN:
            raise RnaStructureError(f"max_bp_span cannot exceed {MAX_BP_SPAN}")
        if sequence_length > MAX_BOUNDED_FOLD_LENGTH:
            raise RnaStructureError(
                f"RNA sequence length {sequence_length} exceeds the bounded folding limit of {MAX_BOUNDED_FOLD_LENGTH} nt"
            )
        return

    hard_limit = MAX_PARTITION_LENGTH if include_partition else MAX_GLOBAL_FOLD_LENGTH
    if sequence_length > hard_limit:
        mode = "partition" if include_partition else "fold"
        raise RnaStructureError(
            f"RNA {mode} analysis is limited to {hard_limit} nt without max_bp_span; "
            "set a bounded max_bp_span for longer local analyses"
        )


def _build_model_details(RNA: Any, sequence_length: int, settings: RnaStructureSettings, include_partition: bool) -> Any:
    md = RNA.md()
    md.temperature = settings.temperature_c
    md.noLP = settings.no_lonely_pairs
    md.dangles = settings.dangles
    md.circ = settings.circular
    md.uniq_ML = 1
    md.compute_bpp = 1 if include_partition else 0
    if settings.max_bp_span is not None:
        md.max_bp_span = min(settings.max_bp_span, sequence_length)
    return md


def _apply_constraints(RNA: Any, fc: Any, sequence: str, settings: RnaStructureSettings, warnings: list[str]) -> None:
    if settings.hard_constraints:
        constraint = settings.hard_constraints.strip()
        if len(constraint) != len(sequence):
            raise RnaStructureError(
                f"Hard-constraint string has length {len(constraint)} but the RNA is {len(sequence)} nt long"
            )
        fc.hc_add_from_db(constraint, RNA.CONSTRAINT_DB_DEFAULT)
        warnings.append("Applied hard constraints from pseudo dot-bracket notation")

    if not settings.shape_method:
        return

    if settings.shape_method != "deigan":
        raise RnaStructureError(f"Unsupported SHAPE guidance method '{settings.shape_method}'")
    if settings.shape_reactivities is None:
        raise RnaStructureError("SHAPE-guided folding requires per-base reactivity values")
    if len(settings.shape_reactivities) != len(sequence):
        raise RnaStructureError(
            f"SHAPE guidance has {len(settings.shape_reactivities)} values but the RNA is {len(sequence)} nt long"
        )

    reactivities = RNA.DoubleVector(
        [
            -999.0 if value is None else float(value)
            for value in settings.shape_reactivities
        ]
    )
    fc.sc_add_SHAPE_deigan(
        reactivities,
        float(settings.shape_slope),
        float(settings.shape_intercept),
        RNA.OPTION_DEFAULT,
    )
    warnings.append(
        f"Applied SHAPE-guided folding with Deigan soft constraints (m={settings.shape_slope}, b={settings.shape_intercept})"
    )


def _paired_count(dot_bracket: str) -> int:
    return dot_bracket.count("(") + dot_bracket.count("[") + dot_bracket.count("{") + dot_bracket.count("<")


def _extract_probabilities(fc: Any, sequence: str, cutoff: float, max_pairs: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, bool]:
    length = len(sequence)
    paired_probabilities = [0.0] * length
    pairs: list[dict[str, Any]] = []

    for entry in fc.plist_from_probs(cutoff):
        i = int(entry.i) - 1
        j = int(entry.j) - 1
        probability = float(entry.p)
        if i < 0 or j < 0 or i >= length or j >= length or i >= j:
            continue

        paired_probabilities[i] += probability
        paired_probabilities[j] += probability
        pairs.append({
            "i": i,
            "j": j,
            "probability": probability,
        })

    total_pair_count = len(pairs)
    pairs.sort(key=lambda pair: (-pair["probability"], pair["i"], pair["j"]))
    truncated = total_pair_count > max_pairs
    if truncated:
        pairs = pairs[:max_pairs]

    positional_entropy = list(fc.positional_entropy())
    if len(positional_entropy) == length + 1:
        positional_entropy = positional_entropy[1:]
    elif len(positional_entropy) != length:
        positional_entropy = [None] * length

    bases = []
    for index, base in enumerate(sequence):
        paired = min(max(paired_probabilities[index], 0.0), 1.0)
        bases.append({
            "index": index,
            "base": base,
            "paired_probability": paired,
            "unpaired_probability": max(0.0, 1.0 - paired),
            "positional_entropy": None if positional_entropy[index] is None else float(positional_entropy[index]),
        })

    return pairs, bases, total_pair_count, truncated


def analyze_rna_structure(
    sequence: str,
    settings: RnaStructureSettings,
    *,
    include_partition: bool = True,
) -> dict[str, Any]:
    RNA = _load_viennarna()
    normalized = normalize_rna_sequence(sequence)
    _validate_problem_size(len(normalized), settings, include_partition)

    warnings: list[str] = []
    md = _build_model_details(RNA, len(normalized), settings, include_partition)
    fc = RNA.fold_compound(normalized, md)
    _apply_constraints(RNA, fc, normalized, settings, warnings)
    mfe_dot_bracket, mfe_energy = fc.mfe()

    result: dict[str, Any] = {
        "sequence": normalized,
        "length": len(normalized),
        "circular": settings.circular,
        "settings": asdict(settings),
        "mfe": {
            "dot_bracket": mfe_dot_bracket,
            "energy_kcal_mol": float(mfe_energy),
            "score": None,
            "distance": None,
            "paired_count": _paired_count(mfe_dot_bracket),
        },
        "centroid": None,
        "mea": None,
        "partition": None,
        "pair_probabilities": [],
        "bases": [],
        "warnings": warnings,
    }

    if not include_partition:
        return result

    fc.exp_params_rescale(mfe_energy)
    partition_dot_bracket, ensemble_free_energy = fc.pf()
    centroid_dot_bracket, centroid_distance = fc.centroid()
    mea_dot_bracket, mea_score = fc.MEA(settings.gamma)
    pair_probabilities, bases, total_pair_count, truncated = _extract_probabilities(
        fc,
        normalized,
        settings.probability_cutoff,
        settings.max_pairs,
    )

    if truncated:
        warnings.append(
            f"Pair-probability output truncated to the top {settings.max_pairs} pairs above p >= {settings.probability_cutoff:.3f}"
        )
    if settings.max_bp_span is not None:
        warnings.append(f"Local folding used max_bp_span={settings.max_bp_span}")

    result["centroid"] = {
        "dot_bracket": centroid_dot_bracket,
        "energy_kcal_mol": None,
        "score": None,
        "distance": float(centroid_distance),
        "paired_count": _paired_count(centroid_dot_bracket),
    }
    result["mea"] = {
        "dot_bracket": mea_dot_bracket,
        "energy_kcal_mol": None,
        "score": float(mea_score),
        "distance": None,
        "paired_count": _paired_count(mea_dot_bracket),
    }
    result["partition"] = {
        "dot_bracket": partition_dot_bracket,
        "ensemble_free_energy_kcal_mol": float(ensemble_free_energy),
        "mean_bp_distance": float(fc.mean_bp_distance()),
        "probability_cutoff": settings.probability_cutoff,
        "pair_count": total_pair_count,
        "truncated": truncated,
    }
    result["pair_probabilities"] = pair_probabilities
    result["bases"] = bases
    return result
