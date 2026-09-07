#!/usr/bin/env python3
"""
BoltzGen Results Filter with Diversity Selection

Implements upstream BoltzGen filtering capabilities:
- Budget: Final number of designs to keep
- Alpha: Quality vs diversity tradeoff (0.0=quality only, 1.0=diversity only)
- RMSD threshold: Maximum refolding RMSD
- pLDDT/pTM threshold: Minimum structure confidence
- Affinity threshold: Minimum binding probability
"""

from __future__ import annotations

import argparse
import shutil
import os
import json
import re
from pathlib import Path
from typing import List, Dict
import numpy as np

AA_THREE_TO_ONE = {
    "ALA": "A",
    "ARG": "R",
    "ASN": "N",
    "ASP": "D",
    "CYS": "C",
    "GLN": "Q",
    "GLU": "E",
    "GLY": "G",
    "HIS": "H",
    "ILE": "I",
    "LEU": "L",
    "LYS": "K",
    "MET": "M",
    "PHE": "F",
    "PRO": "P",
    "SER": "S",
    "THR": "T",
    "TRP": "W",
    "TYR": "Y",
    "VAL": "V",
}

METRIC_ALIASES = {
    "conf_score": "affinity_probability",
    "rmsd": "filter_rmsd",
}

def compute_sequence_diversity(seq1: str, seq2: str) -> float:
    """Compute sequence diversity as fraction of differing residues."""
    if not seq1 or not seq2:
        return 1.0
    min_len = min(len(seq1), len(seq2))
    if min_len == 0:
        return 1.0
    differences = sum(1 for a, b in zip(seq1[:min_len], seq2[:min_len]) if a != b)
    return differences / min_len


def parse_metrics_override(value: str | None) -> dict[str, float | None]:
    overrides: dict[str, float | None] = {}
    if not value:
        return overrides

    tokens = [token for token in re.split(r"[\s,]+", value.strip()) if token]
    for token in tokens:
        if "=" not in token:
            continue
        key, raw = token.split("=", 1)
        key = METRIC_ALIASES.get(key.strip(), key.strip())
        raw = raw.strip().lower()
        if not key:
            continue
        if raw == "none":
            overrides[key] = None
            continue
        try:
            overrides[key] = float(raw)
        except ValueError:
            continue
    return overrides


def parse_additional_filters(value: str | None) -> list[dict[str, float | str]]:
    filters: list[dict[str, float | str]] = []
    if not value:
        return filters

    tokens = [token for token in re.split(r"[\s,]+", value.strip()) if token]
    for token in tokens:
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)\s*([<>])\s*(-?\d+(?:\.\d+)?)", token)
        if not match:
            continue
        feature, operator, threshold = match.groups()
        filters.append({
            "feature": feature,
            "operator": operator,
            "threshold": float(threshold),
        })
    return filters


def parse_size_buckets(value: str | None) -> list[dict[str, int]]:
    buckets: list[dict[str, int]] = []
    if not value:
        return buckets

    tokens = [token for token in re.split(r"[\s,]+", value.strip()) if token]
    for token in tokens:
        match = re.fullmatch(r"(\d+)-(\d+):(\d+)", token)
        if not match:
            continue
        min_size, max_size, count = map(int, match.groups())
        buckets.append({
            "min": min_size,
            "max": max_size,
            "num_designs": count,
        })
    return buckets


def get_metric_value(metrics: Dict, design: Dict, key: str):
    key = METRIC_ALIASES.get(key, key)
    if metrics.get("core_protein_scientific_contract") == 1 and not key.endswith("_fraction"):
        from lib.filtering.evidence import metric_evidence
        item = metrics.get("metric_evidence", {}).get(key)
        if item is not None:
            return item["value"] if item["state"] == "ok" else None
        return metric_evidence(key, metrics.get(key), metrics.get("plddt_units"))["value"]
    if key.endswith("_fraction"):
        residue_code = key[:-9].upper()
        sequence = (design.get("sequence", "") or metrics.get("designed_sequence", "") or "").upper()
        if not sequence:
            return None
        aa = AA_THREE_TO_ONE.get(residue_code)
        if not aa:
            return None
        return sequence.count(aa) / len(sequence)
    if key == "plddt":
        if metrics.get("plddt") is not None:
            return float(metrics["plddt"])
        if metrics.get("design_ptm") is not None:
            return float(metrics["design_ptm"]) * 100.0
        return None
    if key == "design_ptm":
        value = metrics.get("design_ptm")
        return float(value) if value is not None else None
    if key in {"affinity_probability", "conf_score"}:
        value = metrics.get("affinity_probability", metrics.get("conf_score"))
        return float(value) if value is not None else None
    if key in {"filter_rmsd", "rmsd"}:
        value = metrics.get("filter_rmsd", metrics.get("filter_rmsd_design"))
        return float(value) if value is not None else None
    if key == "binder_length":
        value = metrics.get("binder_length")
        if value is not None:
            return float(value)
        sequence = design.get("sequence", "") or metrics.get("designed_sequence", "")
        return float(len(sequence)) if sequence else None

    value = metrics.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def metric_is_higher_better(metric_name: str) -> bool:
    lowered = metric_name.lower()
    if lowered.startswith("neg_"):
        return True
    lower_is_better_tokens = ("rmsd", "pae", "error", "loss", "asa")
    return not any(token in lowered for token in lower_is_better_tokens)


def resolve_metric_specs(overrides: dict[str, float | None], core_protein_scientific_contract=None) -> list[dict[str, object]]:
    metric_specs: dict[str, dict[str, object]] = {
        "plddt": {"name": "plddt", "higher_is_better": True, "weight": 1.0},
        "affinity_probability": {"name": "affinity_probability", "higher_is_better": True, "weight": 1.0},
        "filter_rmsd": {"name": "filter_rmsd", "higher_is_better": False, "weight": 1.0},
    }

    if core_protein_scientific_contract == 1:
        metric_specs = {("design_ptm" if k == "plddt" else k): {**v, "name": "design_ptm" if k == "plddt" else k} for k, v in metric_specs.items()}

    for metric_name, weight in overrides.items():
        metric_name = METRIC_ALIASES.get(metric_name, metric_name)
        if weight is None:
            metric_specs.pop(metric_name, None)
            continue
        spec = metric_specs.get(metric_name, {
            "name": metric_name,
            "higher_is_better": metric_is_higher_better(metric_name),
            "weight": 1.0,
        })
        spec["weight"] = max(float(weight), 1e-6)
        metric_specs[metric_name] = spec

    return list(metric_specs.values())


def apply_metric_ranking(designs: list[dict], metric_specs: list[dict[str, object]], core_protein_scientific_contract=None) -> None:
    if not designs:
        return
    if not metric_specs:
        for design in designs:
            design["quality_rank_key"] = 0.0
            design["quality_score"] = 1.0
        return

    if core_protein_scientific_contract == 1:
        from lib.filtering.evidence import metric_evidence
        complete = []
        for design in designs:
            valid = all(metric_evidence(str(s["name"]), get_metric_value(design["metrics"], design, str(s["name"])), "percent")["state"] == "ok" for s in metric_specs)
            if valid:
                complete.append(design)
            else:
                design["quality_rank_key"] = None
                design["quality_score"] = None
        designs = complete

    rank_maps: list[dict[str, object]] = []
    for spec in metric_specs:
        metric_name = str(spec["name"])
        higher_is_better = bool(spec["higher_is_better"])
        weight = float(spec["weight"])
        values = []
        for design in designs:
            metric_value = get_metric_value(design["metrics"], design, metric_name)
            design.setdefault("ranking_values", {})[metric_name] = metric_value
            if metric_value is not None:
                values.append((design["design_id"], float(metric_value)))

        values.sort(key=lambda item: item[1], reverse=higher_is_better)
        denom = max(len(values) - 1, 1)
        ranks = {design_id: index / denom for index, (design_id, _) in enumerate(values)}
        rank_maps.append({"metric": metric_name, "weight": weight, "ranks": ranks})

    for design in designs:
        scaled_ranks = []
        for rank_map in rank_maps:
            metric_rank = rank_map["ranks"].get(design["design_id"], 1.0)
            scaled_ranks.append(metric_rank / float(rank_map["weight"]))
        quality_rank_key = max(scaled_ranks) if scaled_ranks else 0.0
        design["quality_rank_key"] = quality_rank_key
        design["quality_score"] = max(0.0, 1.0 - min(1.0, quality_rank_key))


def bucket_for_design(design: dict, buckets: list[dict[str, int]]) -> int | None:
    if not buckets:
        return None
    sequence = design.get("sequence", "")
    length = int(round(get_metric_value(design["metrics"], design, "binder_length") or len(sequence) or 0))
    for index, bucket in enumerate(buckets):
        if bucket["min"] <= length <= bucket["max"]:
            return index
    return None

def select_diverse_subset(
    designs: List[Dict],
    budget: int,
    alpha: float = 0.01,
    size_buckets: list[dict[str, int]] | None = None,
    core_protein_scientific_contract=None,
) -> List[Dict]:
    """
    Select diverse subset using greedy max-min diversity with quality weight.
    
    Algorithm:
    1. Rank designs by quality (composite score)
    2. Start with top-quality design
    3. Iteratively add design that maximizes: (1-alpha)*quality_rank + alpha*min_diversity
    
    Args:
        designs: List of design dicts with 'sequence' and 'quality_score'
        budget: Number of designs to select
        alpha: Diversity weight (0=quality only, 1=diversity only)
    
    Returns:
        Selected subset of designs
    """
    # Strict ranking marks incomplete candidates with unavailable aggregates.
    # Gate before either the budget shortcut or sorting/arithmetic.
    designs = [d for d in designs if all(
        isinstance(d.get(key), (int, float)) and not isinstance(d.get(key), bool)
        and np.isfinite(d[key]) for key in ('quality_score', 'quality_rank_key')
    )] if core_protein_scientific_contract == 1 else designs
    size_buckets = size_buckets or []

    if len(designs) <= budget and not size_buckets:
        return designs
    
    if budget <= 0:
        return []
    
    # Sort by quality (higher is better)
    sorted_designs = sorted(
        designs,
        key=lambda x: (-x.get('quality_score', 0), x.get('quality_rank_key', 0), x.get('design_id', '')),
    )
    
    # Assign quality ranks (0 = best)
    for i, d in enumerate(sorted_designs):
        d['quality_rank'] = i / len(sorted_designs)
    
    selected = []
    remaining = list(sorted_designs)
    bucket_counts = {index: 0 for index in range(len(size_buckets))}

    def _can_accept(candidate: dict) -> bool:
        bucket_index = bucket_for_design(candidate, size_buckets)
        if bucket_index is None:
            return True
        return bucket_counts[bucket_index] < size_buckets[bucket_index]["num_designs"]

    def _record(candidate: dict) -> None:
        bucket_index = bucket_for_design(candidate, size_buckets)
        if bucket_index is not None:
            bucket_counts[bucket_index] += 1

    # Start with best quality design that satisfies any active bucket cap
    while remaining and not selected:
        candidate = remaining.pop(0)
        if not _can_accept(candidate):
            continue
        selected.append(candidate)
        _record(candidate)

    while len(selected) < budget and remaining:
        best_score = -float('inf')
        best_idx = None
        
        for i, candidate in enumerate(remaining):
            if not _can_accept(candidate):
                continue
            # Compute minimum diversity to any selected design
            min_div = min(
                compute_sequence_diversity(
                    candidate.get('sequence', ''),
                    s.get('sequence', '')
                )
                for s in selected
            )
            
            # Combined score: quality + diversity
            score = (1 - alpha) * (1 - candidate['quality_rank']) + alpha * min_div
            
            if score > best_score:
                best_score = score
                best_idx = i

        if best_idx is None:
            break

        selected.append(remaining[best_idx])
        _record(remaining[best_idx])
        remaining.pop(best_idx)

    return selected


def run_strict_filter(args):
    """Future-only path: all inputs retained in the disposition artifact."""
    import hashlib
    import math
    from lib.filtering.evidence import evaluate

    overrides = parse_metrics_override(args.metrics_override)
    if args.metrics_override:
        tokens = [t for t in re.split(r"[\s,]+", args.metrics_override.strip()) if t]
        if len(tokens) != len(overrides) or any(v is not None and (not math.isfinite(v) or v <= 0) for v in overrides.values()):
            raise ValueError("Unsupported metric override; use metric=positive_weight or metric=none")
    extras = parse_additional_filters(args.additional_filters)
    if args.additional_filters and len(extras) != len([t for t in re.split(r"[\s,]+", args.additional_filters.strip()) if t]):
        raise ValueError("Unsupported additional filter")
    if str(args.filter_biased).lower() == 'true':
        extras = [{"feature": f"{aa}_fraction", "operator": '<', "threshold": .3} for aa in ('ALA','GLY','GLU','LEU','VAL')] + extras
    specs = resolve_metric_specs(overrides, core_protein_scientific_contract=1)
    criteria = [('plddt', args.boltzgen_min_plddt, None), ('affinity_probability', args.boltzgen_min_conf_score, None), ('filter_rmsd', None, args.boltzgen_max_rmsd)]
    criteria += [(METRIC_ALIASES.get(str(e['feature']), str(e['feature'])), e['threshold'] if e['operator'] == '>' else None, e['threshold'] if e['operator'] == '<' else None) for e in extras]
    required = [str(s['name']) for s in specs]
    criteria += [(name, None, None) for name in required if name not in {c[0] for c in criteria if c[1] is not None or c[2] is not None}]
    # Validate configuration even with no inputs.
    evaluate(criteria, {}, '', required=required)
    buckets = parse_size_buckets(args.size_buckets)
    if args.size_buckets and len(buckets) != len([t for t in re.split(r"[\s,]+", args.size_buckets.strip()) if t]):
        raise ValueError('Unsupported size buckets')
    pdbs = args.pdbs or []
    jsons = args.jsons or []
    if len(pdbs) == 1:
        pdbs = pdbs[0].split()
    if len(jsons) == 1:
        jsons = jsons[0].split()
    dispositions, passing = [], []
    for pdb in pdbs:
        path = Path(pdb)
        identity = path.stem
        record = None
        try:
            matches = [Path(p) for p in jsons if Path(p).name in (f'confidence_{identity}.json', f'{identity}.json')]
            if len(matches) > 1:
                raise ValueError('ambiguous_metadata_identity')
            raw = matches[0].read_bytes() if matches else None
            metrics = json.loads(raw) if raw is not None else {}
            if not isinstance(metrics, dict) or (metrics.get('design_id') is not None and metrics['design_id'] != identity):
                raise ValueError('foreign_metadata_identity')
            metrics['core_protein_scientific_contract'] = 1
            sequence = metrics.get('designed_sequence') or ''
            for name, _, _ in criteria:
                if name.endswith('_fraction'):
                    metrics[name] = get_metric_value(metrics, {'sequence': sequence}, name)
            record = evaluate(criteria, metrics, identity, metrics.get('metric_evidence'), metrics.get('plddt_units'), required=required)
            record['source_sha256'] = hashlib.sha256(raw).hexdigest() if raw is not None else None
            record['structure_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            if record['disposition'] == 'passed':
                passing.append({'design_id': identity, 'path': path, 'json_path': matches[0] if matches else None, 'sequence': sequence, 'metrics': metrics})
        except Exception as exc:
            record = evaluate(criteria, {}, identity, required=required)
            record.update(disposition='invalid_evidence', reason_code=str(exc))
            for criterion in record['criteria']:
                criterion['disposition'] = 'invalid_evidence'
                criterion['evidence'].update(state='invalid', value=None, reason_code='candidate_evidence_failure')
        dispositions.append(record)
    apply_metric_ranking(passing, specs, core_protein_scientific_contract=1)
    selected = select_diverse_subset(passing, args.budget if args.budget is not None else len(passing), args.alpha, buckets, core_protein_scientific_contract=1)
    selected_ids = {d['design_id'] for d in selected}
    for record in dispositions:
        record['selected'] = record['candidate_id'] in selected_ids
        if record['disposition'] == 'passed' and not record['selected']:
            record['selection_rejection'] = {
                'criterion': 'diversity_budget_selection',
                'reason_code': 'not_selected_by_diversity_budget',
            }
    # Publish all dispositions before output copying can fail.
    summary = {'core_protein_scientific_contract': 1, 'input_count': len(pdbs), 'passed_thresholds': len(passing), 'final_count': len(selected), 'effective_metrics': specs, 'dispositions': dispositions}
    (Path(args.out_dir) / 'filter_summary.json').write_text(json.dumps(summary, allow_nan=False, indent=2))
    for design in selected:
        shutil.copy2(design['path'], Path(args.out_dir) / design['path'].name)
        if design['json_path']:
            # Write a new versioned artifact; never mutate the input sidecar.
            raw = next(r for r in dispositions if r['candidate_id'] == design['design_id'])
            from lib.filtering.evidence import metric_evidence
            payload = {k: v for k, v in design['metrics'].items() if isinstance(v, str) or v is None}
            payload.update({k: metric_evidence(k, v, design['metrics'].get('plddt_units'))['value'] for k, v in design['metrics'].items() if isinstance(v, (int, float))})
            payload.update(core_protein_scientific_contract=1, source_sha256=raw['source_sha256'], metric_evidence={c['criterion']: c['evidence'] for c in raw['criteria']})
            native = design['metrics'].get('native_scalar_source')
            if native is not None:
                if native['candidate_id'] != design['design_id']:
                    raise ValueError('foreign_native_candidate')
                declared = native['artifact']
                native_path = design['json_path'].parent / declared['path']
                if Path(declared['path']).name != declared['path'] or native_path.is_symlink():
                    raise ValueError('foreign_native_source')
                if hashlib.sha256(native_path.read_bytes()).hexdigest() != declared['sha256']:
                    raise ValueError('native_source_bytes_changed')
                shutil.copy2(native_path, Path(args.out_dir) / native_path.name)
                payload['native_scalar_source'] = native
            (Path(args.out_dir) / design['json_path'].name).write_text(json.dumps(payload, allow_nan=False, indent=2))
    # Only a fully copied set receives publication authority. The earlier report
    # remains useful failure evidence if copying stops, but cannot credit rows.
    summary['publication'] = {}
    for design in selected:
        artifacts = {}
        for role, source in [('structure', design['path']), ('metrics', design['json_path'])]:
            if source is None:
                raise ValueError('selected_candidate_metadata_missing')
            path = Path(args.out_dir) / source.name
            artifacts[role] = {'path': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        summary['publication'][design['design_id']] = artifacts
        if design['metrics'].get('native_scalar_source') is not None:
            artifacts['native'] = design['metrics']['native_scalar_source']['artifact']
    (Path(args.out_dir) / 'filter_summary.json').write_text(json.dumps(summary, allow_nan=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Filter BoltzGen results with diversity selection")
    parser.add_argument("--core-protein-scientific-contract", type=int, choices=[1], default=None)
    parser.add_argument("--pdbs", nargs="+", help="Input PDB files")
    parser.add_argument("--jsons", nargs="+", help="Input JSON metadata files")
    parser.add_argument("--out_dir", type=str, required=True, help="Output directory")
    
    # Quality thresholds
    parser.add_argument("--boltzgen-min-plddt", type=float, default=None,
                        help="Minimum pLDDT (derived from design_ptm * 100)")
    parser.add_argument("--boltzgen-min-conf-score", type=float, default=None,
                        help="Minimum affinity probability (0-1)")
    parser.add_argument("--boltzgen-max-rmsd", type=float, default=None,
                        help="Maximum refolding RMSD (lower is better)")
    
    # Diversity selection
    parser.add_argument("--budget", type=int, default=None,
                        help="Final number of designs to keep (with diversity selection)")
    parser.add_argument("--alpha", type=float, default=0.01,
                        help="Quality/diversity tradeoff: 0.0=quality only, 1.0=diversity only")
    parser.add_argument("--filter-biased", type=str, default="true",
                        help="Remove amino acid composition outliers (true/false)")
    
    # Advanced filtering (passed to upstream BoltzGen - logged for reference)
    parser.add_argument("--metrics-override", type=str, default=None,
                        help="Per-metric weights (e.g., 'plip_hbonds_refolded=4 delta_sasa_refolded=2')")
    parser.add_argument("--additional-filters", type=str, default=None,
                        help="Extra hard filters (e.g., 'design_ALA>0.3 design_GLY<0.2')")
    parser.add_argument("--size-buckets", type=str, default=None,
                        help="Size constraints (e.g., '10-20:5 20-30:10 30-40:5')")

    
    args = parser.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    if args.core_protein_scientific_contract == 1:
        return run_strict_filter(args)
    
    if not args.pdbs:
        print("No PDBs to filter")
        return
    
    # Parse PDB list (handle space-separated string)
    pdb_list = args.pdbs
    if len(pdb_list) == 1 and ' ' in pdb_list[0]:
        pdb_list = pdb_list[0].split()
    
    # Build JSON lookup for metrics
    json_metrics = {}
    json_list = args.jsons or []
    if len(json_list) == 1 and ' ' in json_list[0]:
        json_list = json_list[0].split()
    
    for json_path in json_list:
        try:
            with open(json_path) as f:
                data = json.load(f)
                # Try multiple ID formats
                design_id = data.get('design_id', '')
                if not design_id:
                    design_id = Path(json_path).stem
                    if design_id.startswith('confidence_'):
                        design_id = design_id[11:]  # Remove prefix
                data["__path__"] = json_path
                json_metrics[design_id] = data
                json_metrics[Path(json_path).stem] = data
        except Exception as e:
            print(f"Warning: Could not parse {json_path}: {e}")
    
    metric_overrides = parse_metrics_override(args.metrics_override)
    additional_filters = parse_additional_filters(args.additional_filters)
    size_buckets = parse_size_buckets(args.size_buckets)
    if str(args.filter_biased).lower() == "true":
        additional_filters = [
            {"feature": "ALA_fraction", "operator": "<", "threshold": 0.3},
            {"feature": "GLY_fraction", "operator": "<", "threshold": 0.3},
            {"feature": "GLU_fraction", "operator": "<", "threshold": 0.3},
            {"feature": "LEU_fraction", "operator": "<", "threshold": 0.3},
            {"feature": "VAL_fraction", "operator": "<", "threshold": 0.3},
            *additional_filters,
        ]

    # First pass: Apply hard filters
    passed_designs = []
    filtered_count = 0
    
    for pdb in pdb_list:
        path = Path(pdb)
        design_id = path.stem
        
        # Get metrics from JSON
        metrics = json_metrics.get(design_id, {})
        
        # Extract metrics
        sequence = metrics.get('designed_sequence', '')
        plddt = get_metric_value(metrics, {'sequence': sequence}, 'plddt') or 0.0
        conf_score = get_metric_value(metrics, {'sequence': sequence}, 'affinity_probability') or 0.0
        rmsd = get_metric_value(metrics, {'sequence': sequence}, 'filter_rmsd')
        rmsd = float('inf') if rmsd is None else rmsd

        # Apply hard filters
        if args.boltzgen_min_plddt and plddt < args.boltzgen_min_plddt:
            print(f"Filtered {design_id}: pLDDT {plddt:.1f} < {args.boltzgen_min_plddt}")
            filtered_count += 1
            continue
        
        if args.boltzgen_min_conf_score and conf_score < args.boltzgen_min_conf_score:
            print(f"Filtered {design_id}: confidence {conf_score:.3f} < {args.boltzgen_min_conf_score}")
            filtered_count += 1
            continue
        
        if args.boltzgen_max_rmsd and rmsd > args.boltzgen_max_rmsd:
            print(f"Filtered {design_id}: RMSD {rmsd:.2f} > {args.boltzgen_max_rmsd}")
            filtered_count += 1
            continue

        failed_additional_filter = None
        design_stub = {'sequence': sequence}
        for extra_filter in additional_filters:
            feature = str(extra_filter["feature"])
            operator = str(extra_filter["operator"])
            threshold = float(extra_filter["threshold"])
            metric_value = get_metric_value(metrics, design_stub, feature)
            if metric_value is None:
                failed_additional_filter = f"{feature} missing"
                break
            if operator == '>' and not metric_value >= threshold:
                failed_additional_filter = f"{feature} {metric_value:.3f} < {threshold}"
                break
            if operator == '<' and not metric_value <= threshold:
                failed_additional_filter = f"{feature} {metric_value:.3f} > {threshold}"
                break
        if failed_additional_filter:
            print(f"Filtered {design_id}: additional filter failed ({failed_additional_filter})")
            filtered_count += 1
            continue
        
        passed_designs.append({
            'path': path,
            'design_id': design_id,
            'sequence': sequence,
            'plddt': plddt,
            'conf_score': conf_score,
            'rmsd': rmsd,
            'metrics': metrics,
            'json_path': None,
        })

    for design in passed_designs:
        json_path = None
        for candidate_name in (
            design["design_id"],
            f"confidence_{design['design_id']}",
        ):
            if candidate_name in json_metrics:
                json_path = json_metrics[candidate_name].get("__path__")
                break
        design["json_path"] = Path(json_path) if json_path else None

    metric_specs = resolve_metric_specs(metric_overrides)
    apply_metric_ranking(passed_designs, metric_specs)
    
    print(f"Hard filters: {filtered_count} removed, {len(passed_designs)} passed")
    
    selection_budget = args.budget or len(passed_designs)
    if selection_budget < len(passed_designs) or size_buckets:
        print(f"Applying diversity selection: {len(passed_designs)} -> {selection_budget} (alpha={args.alpha})")
        selected = select_diverse_subset(passed_designs, selection_budget, args.alpha, size_buckets=size_buckets)
    else:
        selected = sorted(
            passed_designs,
            key=lambda design: (design.get("quality_rank_key", 0), -design.get("quality_score", 0), design.get("design_id", "")),
        )

    # Copy selected designs to output
    for design in selected:
        shutil.copy(design['path'], Path(args.out_dir) / design['path'].name)
        
        # Also copy JSON if it exists
        json_path = design.get('json_path')
        if json_path and json_path.exists():
            shutil.copy(json_path, Path(args.out_dir) / json_path.name)
    
    print(f"Final output: {len(selected)} designs copied to {args.out_dir}")
    
    # Write summary
    summary_path = Path(args.out_dir) / "filter_summary.json"
    with open(summary_path, 'w') as f:
        json.dump({
            'input_count': len(pdb_list),
            'filtered_by_thresholds': filtered_count,
            'passed_thresholds': len(passed_designs),
            'budget': args.budget,
            'alpha': args.alpha,
            'final_count': len(selected),
            'filters_applied': {
                'min_plddt': args.boltzgen_min_plddt,
                'min_conf_score': args.boltzgen_min_conf_score,
                'max_rmsd': args.boltzgen_max_rmsd,
                'metrics_override': metric_overrides,
                'additional_filters': additional_filters,
                'size_buckets': size_buckets,
            }
        }, f, indent=2)


if __name__ == "__main__":
    main()
