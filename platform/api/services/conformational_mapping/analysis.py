"""Hierarchical conformational-map comparison, support and ranking estimands."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Any, Iterable, Mapping, NamedTuple, Sequence

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


def _clash_key_sort_bytes(value: tuple[str, tuple[Any, ...], str]) -> bytes:
    """Return canonical bytes for the typed nested clash-row identity."""
    return canonical_json_bytes([value[0], list(value[1]), value[2]])


def _ranked_universe_reference(common: Iterable[tuple[Any, ...]]) -> dict[str, Any]:
    identities = [list(key) for key in sorted(common)]
    return {"count": len(identities), "sha256": canonical_sha256(identities)}


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


_HIERARCHICAL_KEYS = (
    "mean",
    "outer_support_fraction",
    "coordinate_support_fraction",
    "valid_outer_count",
    "expected_outer_count",
    "valid_coordinate_count",
    "expected_coordinate_count",
    "between_variance",
    "between_range",
    "stratum_mean_median",
    "stratum_mean_iqr",
    "strata",
)
_HIERARCHICAL_MAX_SERIES = 5000
_HIERARCHICAL_MAX_TOTAL_CELLS = 500_000


def _to_nested_grid(values: Mapping[tuple[str, str], Any]) -> dict[str, dict[str, Any]]:
    nested: dict[str, dict[str, Any]] = {}
    for (outer, inner), value in values.items():
        nested.setdefault(str(outer), {})[str(inner)] = value
    return nested


def _normalize_hierarchical_entry(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    if raw.get("has_valid_data") is False or raw.get("mean") is None:
        return None
    return {key: raw.get(key) for key in _HIERARCHICAL_KEYS}


def _batch_hierarchical_summary(
    series: Mapping[str, Mapping[tuple[str, str], Any]],
    expected: Mapping[str, Sequence[str]],
    *,
    value_mode: str = "numeric",
    role: str = "hierarchical_summary",
) -> tuple[dict[str, dict[str, Any] | None] | None, list[dict[str, Any]], bool]:
    """Run descriptive.hierarchical_summary in BioModStack Stats Toolkit only (no local fallback)."""
    if not series or not expected:
        return {}, [], False
    from services.stats_toolkit_client import (
        StatsToolkitUnavailableError,
        get_stats_toolkit_client,
    )

    expected_payload = {str(outer): [str(inner) for inner in inners] for outer, inners in expected.items()}
    expected_cells = max(1, sum(len(inners) for inners in expected_payload.values()))
    max_chunk = max(1, min(_HIERARCHICAL_MAX_SERIES, _HIERARCHICAL_MAX_TOTAL_CELLS // expected_cells))
    keys = list(series.keys())
    client = get_stats_toolkit_client()
    results: dict[str, dict[str, Any] | None] = {}
    run_refs: list[dict[str, Any]] = []
    for start in range(0, len(keys), max_chunk):
        chunk_keys = keys[start : start + max_chunk]
        chunk_payload = {key: _to_nested_grid(series[key]) for key in chunk_keys}
        try:
            run_ref, output = client.run_tool(
                None,
                "descriptive.hierarchical_summary",
                {
                    "expected": expected_payload,
                    "series": chunk_payload,
                    "value_mode": value_mode,
                },
            )
        except StatsToolkitUnavailableError:
            return None, run_refs, True
        run_refs.append({"role": role, **run_ref})
        series_results = output.get("series_results")
        if isinstance(series_results, Mapping):
            for key in chunk_keys:
                results[key] = _normalize_hierarchical_entry(series_results.get(key))
        elif len(chunk_keys) == 1:
            results[chunk_keys[0]] = _normalize_hierarchical_entry(output)
        else:
            for key in chunk_keys:
                results[key] = None
    return results, run_refs, False


class _RankStability(NamedTuple):
    value: float | None
    common: set[tuple[Any, ...]]
    pairwise: list[dict[str, Any]]
    excluded: dict[str, str]
    correlation_run: dict[str, Any] | None = None
    summary_run: dict[str, Any] | None = None
    unavailable: bool = False


def _rank_stability(
    absolute_by_item: Mapping[tuple[Any, ...], Mapping[str, Any] | None],
    expected: Mapping[str, Sequence[str]],
    *,
    inner_minimum: float,
    outer_minimum: float,
    common_minimum: int,
) -> _RankStability:
    # Reuse the inner means from the existing absolute hierarchical summary run.
    rank_scores: dict[str, dict[tuple[Any, ...], float]] = {}
    excluded: dict[str, str] = {}
    for outer, inner_ids in expected.items():
        scores: dict[tuple[Any, ...], float] = {}
        for item, absolute in absolute_by_item.items():
            stratum = (absolute["strata"].get(outer) if absolute else None)
            if not stratum or stratum["mean"] is None or not inner_ids:
                continue
            support = len(stratum["valid_inner"]) / len(inner_ids)
            if support >= inner_minimum:
                scores[item] = support * stratum["mean"]
        if len(scores) >= common_minimum:
            rank_scores[outer] = scores
        else:
            excluded[outer] = "ranked universe below minimum"
    common = set.intersection(*(set(value) for value in rank_scores.values())) if rank_scores else set()
    pairwise: list[dict[str, Any]] = []
    stability: float | None = None
    run_ref: dict[str, Any] | None = None
    summary_ref = None
    if len(rank_scores) >= max(3, math.ceil(outer_minimum * len(expected))) and len(common) >= max(3, common_minimum):
        outer_ids = list(expected)
        ranked_outer_ids = [outer for outer in outer_ids if outer in rank_scores]
        if 2 <= len(ranked_outer_ids) <= 50:
            from services.stats_toolkit_client import (
                StatsToolkitUnavailableError,
                get_stats_toolkit_client,
            )
            common_keys = sorted(common)
            columns = {
                outer: [float(rank_scores[outer][key]) for key in common_keys]
                for outer in ranked_outer_ids
            }
            try:
                client = get_stats_toolkit_client()
                run_ref, res = client.run_tool(
                    None,
                    "inference.correlation_matrix",
                    {
                        "columns": columns,
                        "method": "spearman",
                        "p_adjust_method": "benjamini_hochberg",
                    },
                )
            except StatsToolkitUnavailableError:
                return _RankStability(None, common, [], excluded, unavailable=True)
            pair_lookup = {
                (str(item["var_a"]), str(item["var_b"])): item
                for item in (res.get("pairs") or [])
                if isinstance(item, Mapping)
            }
            correlations: list[float] = []
            for left_index, left in enumerate(ranked_outer_ids):
                for right in ranked_outer_ids[left_index + 1:]:
                    info = pair_lookup.get((left, right)) or pair_lookup.get((right, left)) or {}
                    stat = info.get("statistic")
                    corr_val = float(stat) if stat is not None else None
                    entry: dict[str, Any] = {
                        "left": left,
                        "right": right,
                        "spearman": corr_val,
                        "p_value": info.get("p_value"),
                        "adjusted_p_value": info.get("adjusted_p_value"),
                        "stats_backend": "stats_toolkit",
                        "stats_run": run_ref,
                    }
                    pairwise.append(entry)
                    if corr_val is None:
                        return _RankStability(None, common, pairwise, excluded, run_ref)
                    correlations.append(corr_val)
            if correlations and len(correlations) == len(pairwise):
                try:
                    summary_ref, summary = client.run_tool(
                        None, "descriptive.summary", {"columns": {"spearman": correlations}},
                    )
                except StatsToolkitUnavailableError:
                    return _RankStability(None, common, pairwise, excluded, run_ref, unavailable=True)
                stability = summary["columns"]["spearman"]["median"]
    return _RankStability(stability, common, pairwise, excluded, run_ref, summary_ref)


def _matched_comparison_with_runs(
    comparison: Mapping[str, Any], policy: Mapping[str, Any]
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], bool]:
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
    stats_runs: list[dict[str, Any]] = []
    unavailable = False
    if context_values:
        series_key_map = {canonical_sha256(list(key)): key for key in context_values}
        agg_by_key, agg_runs, agg_unavail = _batch_hierarchical_summary(
            {sk: context_values[orig] for sk, orig in series_key_map.items()},
            expected,
            value_mode="numeric",
            role=f"matched_comparison_context:{comparison['comparison_id']}",
        )
        stats_runs.extend(agg_runs)
        trans_by_key, trans_runs, trans_unavail = _batch_hierarchical_summary(
            {sk: transition_values[orig] for sk, orig in series_key_map.items()},
            expected,
            value_mode="boolean_fraction",
            role=f"matched_comparison_transition:{comparison['comparison_id']}",
        )
        stats_runs.extend(trans_runs)
        unavailable = agg_unavail or trans_unavail
        if not unavailable and agg_by_key is not None and trans_by_key is not None:
            for sk in series_key_map:
                aggregate = agg_by_key.get(sk)
                transition = trans_by_key.get(sk)
                if aggregate is None or transition is None:
                    continue
                output[sk] = {
                    "comparison_id": comparison["comparison_id"],
                    "context_hierarchical": aggregate,
                    "context_transition": transition,
                    "switch_score": aggregate["coordinate_support_fraction"] * transition["mean"] * abs(aggregate["mean"]),
                }
    return output, pair_ledger, redistribution, stats_runs, unavailable


def _matched_comparison(
    comparison: Mapping[str, Any], policy: Mapping[str, Any]
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    output, pair_ledger, redistribution, _runs, _unavail = _matched_comparison_with_runs(comparison, policy)
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
    from services.stats_toolkit_client import STATS_TOOLKIT_UNAVAILABLE_REASON

    stats_runs: list[dict[str, Any]] = []
    any_stats_unavailable = False
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
        matched_switch, pair_ledger, redistribution_records, matched_runs, matched_unavail = _matched_comparison_with_runs(comparisons[0], policy)
        stats_runs.extend(matched_runs)
        if matched_unavail:
            any_stats_unavailable = True

    sorted_items = sorted(item_universe)
    item_identities: dict[tuple[Any, ...], dict[str, Any]] = {}
    item_source_keys: dict[tuple[Any, ...], str] = {}
    items_by_target: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
    for item in sorted_items:
        item_wt = next(str(index[item]["wt"]) for index in indexes.values() if item in index)
        identity = {
            "target_id": str(item[0]), "entity_instance_id": str(item[1]),
            "auth_asym_id": str(item[2]), "auth_seq_id": int(item[3]),
            "insertion_code": str(item[4]), "sequence_index": int(item[5]),
            "validated_wt": item_wt, "substitution": str(item[-1]),
        }
        item_identities[item] = identity
        item_source_keys[item] = analysis_source_row_key(identity)
        items_by_target[str(item[0])].append(item)

    agg_by_key: dict[str, dict[str, Any] | None] = {}
    abs_by_key: dict[str, dict[str, Any] | None] = {}
    sign_by_key: dict[str, dict[str, Any] | None] = {}
    clash_by_key: dict[str, dict[str, Any] | None] = {}
    clash_reasons_by_key: dict[str, list[str]] = {}
    reference_sign_by_key: dict[str, int] = {}
    hierarchical_unavailable_targets: set[str] = set()

    for target, target_items in items_by_target.items():
        target_expected = expected_by_target[target]
        non_empty_items = [item for item in target_items if per_item_values[item]]
        if not non_empty_items:
            continue
        signed_series = {item_source_keys[item]: per_item_values[item] for item in non_empty_items}
        abs_series = {
            item_source_keys[item]: {coord: abs(val) for coord, val in per_item_values[item].items()}
            for item in non_empty_items
        }
        signed_out, signed_runs, signed_unavail = _batch_hierarchical_summary(
            signed_series, target_expected, value_mode="numeric", role=f"hierarchical:{target}"
        )
        stats_runs.extend(signed_runs)
        if signed_unavail or signed_out is None:
            hierarchical_unavailable_targets.add(target)
            any_stats_unavailable = True
            continue
        abs_out, abs_runs, abs_unavail = _batch_hierarchical_summary(
            abs_series, target_expected, value_mode="numeric", role=f"hierarchical_absolute:{target}"
        )
        stats_runs.extend(abs_runs)
        if abs_unavail or abs_out is None:
            hierarchical_unavailable_targets.add(target)
            any_stats_unavailable = True
            continue
        sign_series: dict[str, dict[tuple[str, str], bool]] = {}
        clash_series: dict[str, dict[tuple[str, str], bool]] = {}
        for item in non_empty_items:
            sk = item_source_keys[item]
            agg_entry = signed_out.get(sk)
            agg_by_key[sk] = agg_entry
            abs_by_key[sk] = abs_out.get(sk)
            if agg_entry is None:
                continue
            mean = agg_entry["mean"]
            ref_sign = 0 if abs(mean) <= epsilon else (1 if mean > 0 else -1)
            reference_sign_by_key[sk] = ref_sign
            values = per_item_values[item]
            sign_series[sk] = {
                key: ref_sign != 0 and abs(value) > epsilon and (1 if value > 0 else -1) == ref_sign
                for key, value in values.items()
            }
            c_values: dict[tuple[str, str], bool] = {}
            c_reasons: list[str] = []
            for candidate_id, coordinate in candidate_coordinate.items():
                if coordinate not in values:
                    continue
                row = clash_rows.get((candidate_id, item[1:-1], item[-1]))
                if not isinstance(row, Mapping) or not isinstance(row.get("clash_flag"), bool):
                    c_reasons.append(f"{coordinate[0]}:{coordinate[1]}:missing clash result")
                    continue
                if row.get("detector_id") != policy["clash_detector_id"] or row.get("detector_version") != policy["clash_detector_version"]:
                    c_reasons.append(f"{coordinate[0]}:{coordinate[1]}:clash detector mismatch")
                    continue
                if row.get("detector_sha256") != CLASH_DETECTOR_SHA256:
                    c_reasons.append(f"{coordinate[0]}:{coordinate[1]}:clash detector hash mismatch")
                    continue
                c_values[coordinate] = not row["clash_flag"]
            clash_reasons_by_key[sk] = c_reasons
            if c_values:
                clash_series[sk] = c_values
        if sign_series:
            sign_out, sign_runs, sign_unavail = _batch_hierarchical_summary(
                sign_series, target_expected, value_mode="boolean_fraction", role=f"sign_consistency:{target}"
            )
            stats_runs.extend(sign_runs)
            if sign_unavail or sign_out is None:
                hierarchical_unavailable_targets.add(target)
                any_stats_unavailable = True
                continue
            sign_by_key.update(sign_out)
        if clash_series:
            clash_out, clash_runs, clash_unavail = _batch_hierarchical_summary(
                clash_series, target_expected, value_mode="boolean_fraction", role=f"clash_free:{target}"
            )
            stats_runs.extend(clash_runs)
            if clash_unavail or clash_out is None:
                hierarchical_unavailable_targets.add(target)
                any_stats_unavailable = True
                continue
            clash_by_key.update(clash_out)

    rank_by_target = {}
    for target, target_expected in expected_by_target.items():
        rank = _rank_stability(
            {item: abs_by_key.get(item_source_keys[item]) for item in items_by_target[target]},
            target_expected, inner_minimum=inner_minimum, outer_minimum=outer_minimum,
            common_minimum=common_minimum,
        )
        rank_by_target[target] = rank
        if rank.correlation_run:
            stats_runs.append({"role": f"rank_correlations:{target}", **rank.correlation_run})
        if rank.summary_run:
            stats_runs.append({"role": f"rank_stability:{target}", **rank.summary_run})
        any_stats_unavailable |= rank.unavailable
    ranked_universe_reference_by_target = {
        target: _ranked_universe_reference(rank.common) for target, rank in rank_by_target.items()
    }

    for item in sorted_items:
        target = str(item[0])
        values = per_item_values[item]
        item_expected = expected_by_target[target]
        rank = rank_by_target[target]
        rank_stability = rank.value
        identity = item_identities[item]
        source_key = item_source_keys[item]
        hier_unavail = target in hierarchical_unavailable_targets
        aggregate = None if hier_unavail else agg_by_key.get(source_key)
        if aggregate is None:
            matched_key = canonical_sha256(list((*item[1:-1], item[-1])))
            matched = matched_switch.get(matched_key)
            unavail_components: dict[str, Any] = {}
            if matched:
                unavail_components["matched_comparison"] = matched
            if hier_unavail or rank.unavailable:
                unavail_components["stats_status"] = "unavailable"
                unavail_components["stats_unavailable_reason"] = STATS_TOOLKIT_UNAVAILABLE_REASON
                unavail_components["rank_stability"] = None
                unavail_components["rank_stability_pairwise"] = []
            results.append(
                {
                    "source_row_key": source_key, "identity": identity,
                    "status": "insufficient_support",
                    "expected_coordinate_count": sum(map(len, item_expected.values())), "valid_coordinate_count": 0,
                    "outer_support_fraction": 0.0, "coordinate_support_fraction": 0.0,
                    "hierarchical_mean": None, "hotspot_score": None,
                    "switch_score": None,
                    "failure_reason": (
                        f"{STATS_TOOLKIT_UNAVAILABLE_REASON}: hierarchical summary unavailable"
                        if hier_unavail else "no finite substitution differences"
                    ),
                    "components": unavail_components,
                    "sort_keys": {
                        "switch_score": None,
                        "entity_instance_id": item[1], "sequence_index": item[5],
                        "insertion_code": item[4], "mutation_order": AA_ORDER.index(item[-1]),
                    },
                }
            )
            if hier_unavail or rank.unavailable:
                support_records.append({"source_row_key": source_key, **unavail_components})
            continue
        absolute = abs_by_key.get(source_key)
        mean = aggregate["mean"]
        reference_sign = reference_sign_by_key[source_key]
        sign = sign_by_key.get(source_key)
        clash_reasons = clash_reasons_by_key.get(source_key, [])
        clash = clash_by_key.get(source_key)
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
        if rank_stability is None or item not in rank.common:
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
            "common_ranked_universe": ranked_universe_reference_by_target[str(item[0])],
            "pairwise_rank_correlations": rank.pairwise,
            "excluded_rank_strata": rank.excluded,
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
                for key in sorted(clash_rows, key=_clash_key_sort_bytes)
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
            dict(clash_rows[key]) for key in sorted(clash_rows, key=_clash_key_sort_bytes)
        ],
        "stats_status": "unavailable" if any_stats_unavailable else "available",
        "stats_unavailable_reason": STATS_TOOLKIT_UNAVAILABLE_REASON if any_stats_unavailable else None,
        "stats_runs": stats_runs,
    }
    validate_schema("cm_analysis_v1", analysis)
    return analysis
