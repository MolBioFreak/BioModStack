"""Hierarchical conformational-map comparison, support and ranking estimands."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Any, Mapping, Sequence

from .contracts import (
    AA_ORDER,
    analysis_source_row_key,
    canonical_json_bytes,
    canonical_sha256,
    validate_schema,
)
from .clash import CLASH_DETECTOR_SHA256


class ConformationalAnalysisError(ValueError):
    """Validated inputs cannot support an unambiguous analysis."""


def _coordinate_axes(coordinate: Mapping[str, Any]) -> tuple[str, str]:
    backend = coordinate.get("backend")
    target_id = str(coordinate["target_id"])
    if backend == "protenix_v2_ensemble":
        return canonical_json_bytes([target_id, coordinate["ordered_seed"]]).decode("utf-8"), str(coordinate["sample_index"])
    if backend == "confornets":
        outer = canonical_json_bytes(
            [
                target_id, coordinate["task"], coordinate["test_case_id"], coordinate.get("reference_id"),
                coordinate["run_index"], coordinate["saved_step"], coordinate["confornet_index"],
            ]
        ).decode("utf-8")
        return outer, str(coordinate["sample_index"])
    if backend == "external_import":
        return canonical_json_bytes([target_id, coordinate["staged_index"]]).decode("utf-8"), "0"
    raise ConformationalAnalysisError("unknown backend coordinate")


def _pair_coordinate_axes(coordinate: Mapping[str, Any]) -> tuple[str, str]:
    backend = coordinate.get("backend")
    if backend == "protenix_v2_ensemble":
        return str(coordinate["ordered_seed"]), str(coordinate["sample_index"])
    if backend == "confornets":
        return canonical_json_bytes([
            coordinate["task"], coordinate["test_case_id"], coordinate.get("reference_id"),
            coordinate["run_index"], coordinate["saved_step"], coordinate["confornet_index"],
        ]).decode("utf-8"), str(coordinate["sample_index"])
    if backend == "external_import":
        return str(coordinate["staged_index"]), "0"
    raise ConformationalAnalysisError("unknown matched backend coordinate")


def _residue_key(residue: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        residue["entity_instance_id"], residue["auth_asym_id"], residue["auth_seq_id"],
        residue.get("insertion_code") or "", residue["sequence_index"],
    )


def _landscape_index(landscape: Mapping[str, Any]) -> dict[tuple[Any, ...], Mapping[str, Any]]:
    index: dict[tuple[Any, ...], Mapping[str, Any]] = {}
    for residue in landscape.get("residues", []):
        key = (landscape["target_id"], *_residue_key(residue))
        for slot in residue.get("slots", []):
            slot_key = (*key, slot["mutation_aa"])
            if slot_key in index:
                raise ConformationalAnalysisError("duplicate canonical landscape slot")
            index[slot_key] = {**slot, "wt": residue["wt"]}
    return index


def _sample_variance(values: Sequence[float]) -> float | None:
    return statistics.variance(values) if len(values) >= 2 else None


def _hierarchical(
    values: Mapping[tuple[str, str], float], expected: Mapping[str, Sequence[str]]
) -> dict[str, Any] | None:
    strata: dict[str, dict[str, Any]] = {}
    stratum_means: list[float] = []
    valid_count = 0
    for outer, expected_inner in expected.items():
        valid = [values[(outer, inner)] for inner in expected_inner if (outer, inner) in values]
        valid_count += len(valid)
        if valid:
            mean = sum(valid) / len(valid)
            stratum_means.append(mean)
            strata[outer] = {
                "expected_inner": list(expected_inner),
                "valid_inner": [inner for inner in expected_inner if (outer, inner) in values],
                "mean": mean,
                "variance": _sample_variance(valid),
                "range": [min(valid), max(valid)],
                "median": statistics.median(valid),
            }
        else:
            strata[outer] = {
                "expected_inner": list(expected_inner), "valid_inner": [], "mean": None,
                "variance": None, "range": None, "median": None,
            }
    if not stratum_means:
        return None
    expected_count = sum(len(value) for value in expected.values())
    sorted_means = sorted(stratum_means)
    q1, q3 = (
        statistics.quantiles(sorted_means, n=4, method="inclusive")[0],
        statistics.quantiles(sorted_means, n=4, method="inclusive")[2],
    ) if len(sorted_means) >= 2 else (sorted_means[0], sorted_means[0])
    return {
        "mean": sum(stratum_means) / len(stratum_means),
        "strata": strata,
        "outer_support_fraction": len(stratum_means) / len(expected),
        "coordinate_support_fraction": valid_count / expected_count,
        "valid_coordinate_count": valid_count,
        "expected_coordinate_count": expected_count,
        "between_variance": _sample_variance(stratum_means),
        "between_range": [min(stratum_means), max(stratum_means)],
        "stratum_mean_median": statistics.median(stratum_means),
        "stratum_mean_iqr": q3 - q1,
    }


def _hierarchical_fraction(
    values: Mapping[tuple[str, str], bool], expected: Mapping[str, Sequence[str]]
) -> dict[str, Any] | None:
    numeric = {key: 1.0 if value else 0.0 for key, value in values.items()}
    return _hierarchical(numeric, expected)


def _average_ranks(values: Mapping[tuple[Any, ...], float]) -> dict[tuple[Any, ...], float]:
    ordered = sorted(values, key=lambda key: (-values[key], key))
    ranks: dict[tuple[Any, ...], float] = {}
    cursor = 0
    while cursor < len(ordered):
        end = cursor + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[cursor]]:
            end += 1
        average = ((cursor + 1) + end) / 2
        for key in ordered[cursor:end]:
            ranks[key] = average
        cursor = end
    return ranks


def _spearman(left: Mapping[tuple[Any, ...], float], right: Mapping[tuple[Any, ...], float]) -> float | None:
    keys = sorted(set(left) & set(right))
    if len(keys) < 3:
        return None
    x = [left[key] for key in keys]
    y = [right[key] for key in keys]
    x_mean, y_mean = statistics.mean(x), statistics.mean(y)
    numerator = sum((a - x_mean) * (b - y_mean) for a, b in zip(x, y, strict=True))
    denominator = math.sqrt(sum((a - x_mean) ** 2 for a in x) * sum((b - y_mean) ** 2 for b in y))
    return numerator / denominator if denominator else None


def _rank_stability(
    values_by_item: Mapping[tuple[Any, ...], Mapping[tuple[str, str], float]],
    expected: Mapping[str, Sequence[str]], *, inner_minimum: float,
    outer_minimum: float, common_minimum: int,
) -> tuple[float | None, set[tuple[Any, ...]], list[dict[str, Any]], dict[str, str]]:
    rank_scores: dict[str, dict[tuple[Any, ...], float]] = {}
    excluded: dict[str, str] = {}
    for outer, inner_ids in expected.items():
        scores: dict[tuple[Any, ...], float] = {}
        for item, values in values_by_item.items():
            valid = [abs(values[(outer, inner)]) for inner in inner_ids if (outer, inner) in values]
            if valid and len(valid) / len(inner_ids) >= inner_minimum:
                scores[item] = (len(valid) / len(inner_ids)) * statistics.mean(valid)
        if len(scores) >= common_minimum:
            rank_scores[outer] = scores
        else:
            excluded[outer] = "ranked universe below minimum"
    common = set.intersection(*(set(value) for value in rank_scores.values())) if rank_scores else set()
    pairwise: list[dict[str, Any]] = []
    stability: float | None = None
    if len(rank_scores) >= max(3, math.ceil(outer_minimum * len(expected))) and len(common) >= max(3, common_minimum):
        ranks = {outer: _average_ranks({key: scores[key] for key in common}) for outer, scores in rank_scores.items()}
        correlations: list[float] = []
        outer_ids = list(expected)
        ranked_outer_ids = [outer for outer in outer_ids if outer in ranks]
        for left_index, left in enumerate(ranked_outer_ids):
            for right in ranked_outer_ids[left_index + 1:]:
                correlation = _spearman(ranks[left], ranks[right])
                pairwise.append({"left": left, "right": right, "spearman": correlation})
                if correlation is None:
                    return None, common, pairwise, excluded
                correlations.append(correlation)
        if correlations and len(correlations) == len(pairwise):
            stability = statistics.median(correlations)
    return stability, common, pairwise, excluded


def _matched_comparison(
    comparison: Mapping[str, Any], policy: Mapping[str, Any]
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    required = {
        "comparison_id", "ensemble_a", "ensemble_b", "landscapes_a", "landscapes_b",
        "invariant_fields_a", "invariant_fields_b", "mutated_residue_keys",
    }
    if set(comparison) != required:
        raise ConformationalAnalysisError("matched comparison fields are incomplete or unknown")
    if comparison["invariant_fields_a"] != comparison["invariant_fields_b"]:
        raise ConformationalAnalysisError("matched comparison invariant fields differ")
    ensemble_a, ensemble_b = comparison["ensemble_a"], comparison["ensemble_b"]
    for field in ("backend", "runtime_identity", "container_digest", "checkpoint_sha256"):
        if ensemble_a.get(field) != ensemble_b.get(field):
            raise ConformationalAnalysisError(f"matched comparison {field} differs")

    def candidates(ensemble: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
        result: dict[tuple[str, str], Mapping[str, Any]] = {}
        for candidate in ensemble.get("candidates", []):
            coordinate = _pair_coordinate_axes(candidate["backend_coordinates"])
            if coordinate in result:
                raise ConformationalAnalysisError("matched ensemble coordinate is duplicated")
            result[coordinate] = candidate
        return result

    def landscapes(value: Any) -> dict[str, Mapping[str, Any]]:
        rows = list(value.values()) if isinstance(value, Mapping) else value
        if not isinstance(rows, (list, tuple)):
            raise ConformationalAnalysisError("matched landscapes must be an array or object")
        result: dict[str, Mapping[str, Any]] = {}
        for row in rows:
            candidate_id = str(row.get("candidate_id") or "")
            if not candidate_id or candidate_id in result:
                raise ConformationalAnalysisError("matched landscape identity is missing or duplicated")
            result[candidate_id] = row
        return result

    candidates_a, candidates_b = candidates(ensemble_a), candidates(ensemble_b)
    landscapes_a, landscapes_b = landscapes(comparison["landscapes_a"]), landscapes(comparison["landscapes_b"])
    expected_coordinates = list(dict.fromkeys([*candidates_a, *candidates_b]))
    expected: dict[str, list[str]] = defaultdict(list)
    for outer, inner in expected_coordinates:
        if inner not in expected[outer]:
            expected[outer].append(inner)
    pair_ledger: list[dict[str, Any]] = []
    indexes_a: dict[tuple[str, str], dict[tuple[Any, ...], Mapping[str, Any]]] = {}
    indexes_b: dict[tuple[str, str], dict[tuple[Any, ...], Mapping[str, Any]]] = {}
    for coordinate in expected_coordinates:
        left, right = candidates_a.get(coordinate), candidates_b.get(coordinate)
        if left is None or right is None:
            pair_ledger.append({
                "comparison_id": comparison["comparison_id"], "coordinate": list(coordinate),
                "status": "unmatched", "reason": "missing_state_a" if left is None else "missing_state_b",
            })
            continue
        landscape_a, landscape_b = landscapes_a.get(left["candidate_id"]), landscapes_b.get(right["candidate_id"])
        if landscape_a is None or landscape_b is None:
            pair_ledger.append({
                "comparison_id": comparison["comparison_id"], "coordinate": list(coordinate),
                "status": "unmatched", "reason": "missing_landscape_a" if landscape_a is None else "missing_landscape_b",
            })
            continue
        indexes_a[coordinate] = {key[1:]: value for key, value in _landscape_index(landscape_a).items()}
        indexes_b[coordinate] = {key[1:]: value for key, value in _landscape_index(landscape_b).items()}
        pair_ledger.append({
            "comparison_id": comparison["comparison_id"], "coordinate": list(coordinate),
            "status": "matched", "candidate_a": left["candidate_id"], "candidate_b": right["candidate_id"],
        })

    mutated = {str(value) for value in comparison["mutated_residue_keys"]}
    context_values: dict[tuple[Any, ...], dict[tuple[str, str], float]] = defaultdict(dict)
    transition_values: dict[tuple[Any, ...], dict[tuple[str, str], bool]] = defaultdict(dict)
    redistribution: list[dict[str, Any]] = []
    for coordinate in expected_coordinates:
        left, right = indexes_a.get(coordinate), indexes_b.get(coordinate)
        if left is None or right is None:
            continue
        for key in sorted(set(left) & set(right)):
            left_slot, right_slot = left[key], right[key]
            if left_slot.get("status") != "ok" or right_slot.get("status") != "ok":
                continue
            context_values[key][coordinate] = float(right_slot["score"]) - float(left_slot["score"])
            transition_values[key][coordinate] = left_slot.get("class") != right_slot.get("class")
        native_keys = [
            key for key in set(left) & set(right)
            if key[-1] == left[key].get("wt") == right[key].get("wt")
            and left[key].get("status") == right[key].get("status") == "ok"
            and canonical_sha256(list(key[:-1])) not in mutated
        ]
        if not native_keys:
            redistribution.append({
                "comparison_id": comparison["comparison_id"], "coordinate": list(coordinate),
                "status": "insufficient_support", "included_residues": [],
                "excluded_residues": [], "reason": "no common unmutated mapped residues",
            })
        else:
            differences = [float(right[key]["score"]) - float(left[key]["score"]) for key in native_keys]
            transitions = [left[key].get("class") != right[key].get("class") for key in native_keys]
            redistribution.append({
                "comparison_id": comparison["comparison_id"], "coordinate": list(coordinate),
                "status": "ok", "included_residues": [list(key[:-1]) for key in sorted(native_keys)],
                "excluded_residues": [], "signed_mean": statistics.mean(differences),
                "absolute_mean": statistics.mean(abs(value) for value in differences),
                "transition_fraction": statistics.mean(1.0 if value else 0.0 for value in transitions),
            })

    output: dict[str, dict[str, Any]] = {}
    for key, values in context_values.items():
        aggregate = _hierarchical(values, expected)
        transition = _hierarchical_fraction(transition_values[key], expected)
        if aggregate is None or transition is None:
            continue
        output[canonical_sha256(list(key))] = {
            "comparison_id": comparison["comparison_id"],
            "context_hierarchical": aggregate,
            "context_transition": transition,
            "switch_score": aggregate["coordinate_support_fraction"] * transition["mean"] * abs(aggregate["mean"]),
        }
    return output, pair_ledger, redistribution


def analyze_landscapes(
    ensemble: Mapping[str, Any],
    landscapes_by_candidate: Mapping[str, Mapping[str, Any]],
    *,
    policy: Mapping[str, Any],
    clash_rows: Mapping[tuple[str, tuple[Any, ...], str], Mapping[str, Any]] | None = None,
    comparisons: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compute hotspot, sign, clash and rank support over exact manifest coordinates."""

    expected: dict[str, list[str]] = defaultdict(list)
    expected_by_target: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    candidate_coordinate: dict[str, tuple[str, str]] = {}
    exclusions: list[dict[str, str]] = []
    for candidate in ensemble["candidates"]:
        outer, inner = _coordinate_axes(candidate["backend_coordinates"])
        if inner in expected[outer]:
            raise ConformationalAnalysisError("duplicate inner coordinate in one outer stratum")
        expected[outer].append(inner)
        target_id = str(candidate["backend_coordinates"]["target_id"])
        expected_by_target[target_id][outer].append(inner)
        candidate_coordinate[candidate["candidate_id"]] = (outer, inner)
    expected = {key: value for key, value in expected.items()}
    expected_by_target = {
        target: {outer: list(inner) for outer, inner in strata.items()}
        for target, strata in expected_by_target.items()
    }
    indexes: dict[str, dict[tuple[Any, ...], Mapping[str, Any]]] = {}
    item_universe: set[tuple[Any, ...]] = set()
    for candidate_id, coordinate in candidate_coordinate.items():
        landscape = landscapes_by_candidate.get(candidate_id)
        if landscape is None:
            exclusions.append({"coordinate": f"{coordinate[0]}:{coordinate[1]}", "reason": "conformer_missing"})
            continue
        if landscape.get("candidate_id") != candidate_id:
            raise ConformationalAnalysisError("landscape candidate identity mismatch")
        index = _landscape_index(landscape)
        indexes[candidate_id] = index
        item_universe.update(key for key in index if key[-1] != index[key]["wt"])

    per_item_values: dict[tuple[Any, ...], dict[tuple[str, str], float]] = {}
    for item in sorted(item_universe):
        values: dict[tuple[str, str], float] = {}
        for candidate_id, coordinate in candidate_coordinate.items():
            index = indexes.get(candidate_id)
            if index is None:
                continue
            mutant = index.get(item)
            native = index.get((*item[:-1], mutant["wt"])) if mutant else None
            if not mutant or not native or mutant.get("status") != "ok" or native.get("status") != "ok":
                continue
            values[coordinate] = float(mutant["score"]) - float(native["score"])
        per_item_values[item] = values

    inner_minimum = float(policy["inner_support_minimum"])
    outer_minimum = float(policy["outer_support_minimum"])
    common_minimum = int(policy["minimum_common_ranked_universe_size"])
    rank_by_target = {
        target: _rank_stability(
            {item: values for item, values in per_item_values.items() if str(item[0]) == target},
            target_expected, inner_minimum=inner_minimum,
            outer_minimum=outer_minimum, common_minimum=common_minimum,
        )
        for target, target_expected in expected_by_target.items()
    }

    results: list[dict[str, Any]] = []
    support_records: list[dict[str, Any]] = []
    epsilon = float(policy["sign_zero_epsilon"])
    clash_rows = clash_rows or {}
    comparisons = list(comparisons or [])
    if len(comparisons) > 1:
        raise ConformationalAnalysisError("cm_analysis_v1 admits one named matched comparison per analysis authority")
    matched_switch: dict[str, dict[str, Any]] = {}
    pair_ledger: list[dict[str, Any]] = []
    redistribution_records: list[dict[str, Any]] = []
    if comparisons:
        matched_switch, pair_ledger, redistribution_records = _matched_comparison(comparisons[0], policy)
    for item in sorted(item_universe):
        values = per_item_values[item]
        item_expected = expected_by_target[str(item[0])]
        rank_stability, common, pairwise, excluded_rank_strata = rank_by_target[str(item[0])]
        aggregate = _hierarchical(values, item_expected)
        item_wt = next(
            str(index[item]["wt"]) for index in indexes.values() if item in index
        )
        identity = {
            "target_id": str(item[0]), "entity_instance_id": str(item[1]),
            "auth_asym_id": str(item[2]), "auth_seq_id": int(item[3]),
            "insertion_code": str(item[4]), "sequence_index": int(item[5]),
            "validated_wt": item_wt, "substitution": str(item[-1]),
        }
        source_key = analysis_source_row_key(identity)
        if aggregate is None:
            matched_key = canonical_sha256(list((*item[1:-1], item[-1])))
            matched = matched_switch.get(matched_key)
            results.append(
                {
                    "source_row_key": source_key, "identity": identity,
                    "status": "insufficient_support",
                    "expected_coordinate_count": sum(map(len, item_expected.values())), "valid_coordinate_count": 0,
                    "outer_support_fraction": 0.0, "coordinate_support_fraction": 0.0,
                    "hierarchical_mean": None, "hotspot_score": None,
                    "switch_score": None,
                    "failure_reason": "no finite substitution differences",
                    "components": {"matched_comparison": matched} if matched else {},
                    "sort_keys": {
                        "switch_score": None,
                        "entity_instance_id": item[1], "sequence_index": item[5],
                        "insertion_code": item[4], "mutation_order": AA_ORDER.index(item[-1]),
                    },
                }
            )
            continue
        absolute = _hierarchical({key: abs(value) for key, value in values.items()}, item_expected)
        mean = aggregate["mean"]
        reference_sign = 0 if abs(mean) <= epsilon else (1 if mean > 0 else -1)
        sign_values = {
            key: reference_sign != 0 and abs(value) > epsilon and (1 if value > 0 else -1) == reference_sign
            for key, value in values.items()
        }
        sign = _hierarchical_fraction(sign_values, item_expected)
        clash_values: dict[tuple[str, str], bool] = {}
        clash_reasons: list[str] = []
        for candidate_id, coordinate in candidate_coordinate.items():
            if coordinate not in values:
                continue
            row = clash_rows.get((candidate_id, item[1:-1], item[-1]))
            if not isinstance(row, Mapping) or not isinstance(row.get("clash_flag"), bool):
                clash_reasons.append(f"{coordinate[0]}:{coordinate[1]}:missing clash result")
                continue
            if row.get("detector_id") != policy["clash_detector_id"] or row.get("detector_version") != policy["clash_detector_version"]:
                clash_reasons.append(f"{coordinate[0]}:{coordinate[1]}:clash detector mismatch")
                continue
            if row.get("detector_sha256") != CLASH_DETECTOR_SHA256:
                clash_reasons.append(f"{coordinate[0]}:{coordinate[1]}:clash detector hash mismatch")
                continue
            clash_values[coordinate] = not row["clash_flag"]
        clash = _hierarchical_fraction(clash_values, item_expected)
        insufficient = []
        if aggregate["outer_support_fraction"] < outer_minimum:
            insufficient.append("outer substitution support below minimum")
        valid_inner_fractions = [len(record["valid_inner"]) / len(record["expected_inner"]) for record in aggregate["strata"].values() if record["valid_inner"]]
        if not valid_inner_fractions or min(valid_inner_fractions) < inner_minimum:
            insufficient.append("inner substitution support below minimum")
        if sign is None or sign["outer_support_fraction"] < outer_minimum:
            insufficient.append("sign support below minimum")
        if clash is None or clash["outer_support_fraction"] < outer_minimum:
            insufficient.append("clash support below minimum")
        if rank_stability is None or item not in common:
            insufficient.append("rank stability is unavailable")
        hotspot = aggregate["coordinate_support_fraction"] * absolute["mean"]
        threshold_failures: list[str] = []
        if not insufficient:
            if sign["mean"] < float(policy["sign_consistency_minimum"]):
                threshold_failures.append("sign consistency below minimum")
            if clash["mean"] < float(policy["clash_free_minimum"]):
                threshold_failures.append("clash-free fraction below minimum")
            if rank_stability < float(policy["rank_stability_minimum"]):
                threshold_failures.append("rank stability below minimum")
        if insufficient:
            status = "insufficient_support"
            hierarchical_mean = hotspot_value = switch_value = None
            failure_reason = "; ".join(insufficient)
        else:
            status = "conditional" if threshold_failures else "robust"
            hierarchical_mean, hotspot_value, switch_value = mean, hotspot, 0.0
            failure_reason = "; ".join(threshold_failures) if threshold_failures else None
        components = {
            "hierarchical": aggregate,
            "hierarchical_absolute": absolute,
            "sign_reference": reference_sign,
            "sign_consistency": sign,
            "clash_free": clash,
            "clash_exclusions": clash_reasons,
            "rank_stability": rank_stability,
            "common_ranked_universe": [list(key) for key in sorted(common)],
            "pairwise_rank_correlations": pairwise,
            "excluded_rank_strata": excluded_rank_strata,
        }
        result = {
            "source_row_key": source_key,
            "identity": identity,
            "status": status,
            "expected_coordinate_count": aggregate["expected_coordinate_count"],
            "valid_coordinate_count": aggregate["valid_coordinate_count"],
            "outer_support_fraction": aggregate["outer_support_fraction"],
            "coordinate_support_fraction": aggregate["coordinate_support_fraction"],
            "hierarchical_mean": hierarchical_mean,
            "hotspot_score": hotspot_value,
            "switch_score": switch_value,
            "failure_reason": failure_reason,
            "components": components,
            "sort_keys": {
                "status": status,
                "coordinate_support_fraction": aggregate["coordinate_support_fraction"],
                "outer_support_fraction": aggregate["outer_support_fraction"],
                "hotspot_score": hotspot_value,
                "switch_score": switch_value,
                "absolute_hierarchical_mean": abs(mean),
                "entity_instance_id": item[1], "sequence_index": item[5],
                "insertion_code": item[4], "mutation_order": AA_ORDER.index(item[-1]),
            },
        }
        matched_key = canonical_sha256(list((*item[1:-1], item[-1])))
        matched = matched_switch.get(matched_key)
        if matched is not None:
            result["switch_score"] = matched["switch_score"]
            result["sort_keys"]["switch_score"] = matched["switch_score"]
            result["components"]["matched_comparison"] = matched
        results.append(result)
        support_records.append({"source_row_key": source_key, **components})

    status_order = {"robust": 0, "conditional": 1, "insufficient_support": 2}
    results.sort(
        key=lambda result: (
            status_order[result["status"]], -result["coordinate_support_fraction"],
            -result["outer_support_fraction"],
            -result["hotspot_score"] if result["hotspot_score"] is not None else float("inf"),
            -result["switch_score"] if result["switch_score"] is not None else float("inf"),
            -result["sort_keys"]["absolute_hierarchical_mean"]
            if result["sort_keys"].get("absolute_hierarchical_mean") is not None else float("inf"),
            str(result["sort_keys"].get("entity_instance_id", "")),
            int(result["sort_keys"].get("sequence_index", 0)),
            str(result["sort_keys"].get("insertion_code", "")),
            int(result["sort_keys"].get("mutation_order", 0)),
        )
    )
    analysis = {
        "schema_name": "cm_analysis", "schema_version": 1,
        "analysis_id": "cm_analysis_" + canonical_sha256({
            "ensemble": ensemble,
            "landscapes": {key: landscapes_by_candidate[key] for key in sorted(landscapes_by_candidate)},
            "policy": policy,
            "comparisons": comparisons,
            "clash_rows": [
                dict(clash_rows[key])
                for key in sorted(clash_rows, key=lambda value: canonical_json_bytes(list(value)))
            ],
        })[:32],
        "source_ensemble_sha256": canonical_sha256(ensemble),
        "source_landscape_sha256": canonical_sha256(
            {key: landscapes_by_candidate[key] for key in sorted(landscapes_by_candidate)}
        ),
        "formula_version": "cm_analysis_v1",
        "expected_strata": list(expected),
        "results": results,
        "exclusions": exclusions,
        "pair_ledger": pair_ledger,
        "support_records": [*support_records, *redistribution_records],
        "ranking_policy": dict(policy),
        "clash_records": [
            dict(clash_rows[key]) for key in sorted(clash_rows, key=lambda value: canonical_json_bytes(list(value)))
        ],
    }
    validate_schema("cm_analysis_v1", analysis)
    return analysis
