#!/usr/bin/env python3
"""
Build canonical dimer-analysis outputs from legacy event/profile tables.

Outputs:
- dimer_breakpoint_call.tsv (single-row call summary)
- dimer_evidence_by_position.tsv (position-level evidence with explicit sources)
- dimer_read_events.tsv (normalized read/event table)
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build canonical dimer output tables")
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--single-ref-events", required=False, type=Path, default=None)
    parser.add_argument("--single-ref-profile", required=False, type=Path, default=None)
    parser.add_argument("--breakpoint-screen", required=False, type=Path, default=None)
    parser.add_argument("--reference-fasta", required=False, type=Path, default=None)
    parser.add_argument("--window-bp", required=False, type=int, default=50)
    parser.add_argument("--out-call", required=True, type=Path)
    parser.add_argument("--out-evidence", required=True, type=Path)
    parser.add_argument("--out-read-events", required=True, type=Path)
    parser.add_argument("--out-breakpoint-sequences", required=False, type=Path, default=None)
    parser.add_argument("--out-secondary-anomalies", required=False, type=Path, default=None)
    parser.add_argument("--out-secondary-summary", required=False, type=Path, default=None)
    return parser.parse_args()


def read_tsv_rows(path: Optional[Path]) -> List[Dict[str, str]]:
    if path is None or not path.exists() or not path.is_file():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if not reader.fieldnames:
            return []
        rows: List[Dict[str, str]] = []
        for row in reader:
            clean: Dict[str, str] = {}
            for key, value in row.items():
                if key is None:
                    continue
                clean[key.strip()] = (value or "").strip()
            if clean:
                rows.append(clean)
        return rows


def to_lower_map(row: Dict[str, str]) -> Dict[str, str]:
    return {k.strip().lower(): (v or "").strip() for k, v in row.items() if k is not None}


def parse_number(raw: Optional[str]) -> Optional[float]:
    if raw is None:
        return None
    value = str(raw).strip()
    if not value:
        return None
    lower = value.lower()
    if lower in {"na", "n/a", "none", "null", "undefined"}:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_int(raw: Optional[str]) -> Optional[int]:
    num = parse_number(raw)
    if num is None:
        return None
    return int(round(num))


def parse_bool(raw: Optional[str]) -> Optional[bool]:
    if raw is None:
        return None
    value = str(raw).strip().lower()
    if value in {"1", "true", "t", "yes", "y", "crossing", "crosses", "spanning", "spans"}:
        return True
    if value in {"0", "false", "f", "no", "n", "non_crossing", "noncrossing", "not_crossing"}:
        return False
    num = parse_number(value)
    if num is None:
        return None
    return num > 0


def pct(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return (100.0 * numerator) / denominator


def fmt_pct(value: float) -> str:
    return f"{value:.4f}"


def fmt_ratio(numerator: float, denominator: float) -> str:
    if denominator > 0:
        return f"{(numerator / denominator):.4f}"
    if numerator > 0:
        return "999.0000"
    return "0.0000"


def read_summary_metrics(path: Path) -> Dict[str, str]:
    metrics: Dict[str, str] = {}
    for row in read_tsv_rows(path):
        lower = to_lower_map(row)
        key = lower.get("metric", "").strip().lower()
        value = lower.get("value", "").strip()
        if key:
            metrics[key] = value
    return metrics


def read_first_fasta_record(path: Optional[Path]) -> Dict[str, str]:
    if path is None or not path.exists() or not path.is_file():
        return {"name": "", "sequence": ""}

    header = ""
    chunks: List[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            if line.startswith(">"):
                if chunks:
                    break
                header = line[1:].strip()
                continue
            chunks.append(line.upper())

    sequence = "".join(chunks)
    return {"name": header, "sequence": sequence}


def format_circular_junction_window(sequence: str, right_position: int, flank: int) -> Dict[str, str]:
    seq = sequence.strip().upper()
    if not seq or right_position <= 0 or flank <= 0:
        return {"label": "", "window": ""}

    seq_len = len(seq)

    def normalize(pos: int) -> int:
        return ((pos - 1) % seq_len) + 1

    def slice_circular(start_pos: int, count: int) -> str:
        out: List[str] = []
        for i in range(count):
            pos = normalize(start_pos + i)
            out.append(seq[pos - 1])
        return "".join(out)

    right_start = normalize(right_position)
    left_end = normalize(right_start - 1)
    left_start = normalize(right_start - flank)
    right_end = normalize(right_start + flank - 1)

    upstream = slice_circular(right_start - flank, flank)
    downstream = slice_circular(right_start, flank)
    return {
        "label": f"{left_start}-{left_end}|{right_start}-{right_end}",
        "window": f"{upstream}[|]{downstream}",
    }


def metric_int(metrics: Dict[str, str], key: str, default: int = 0) -> int:
    value = parse_int(metrics.get(key))
    if value is None:
        return default
    return value


def metric_float(metrics: Dict[str, str], key: str, default: float = 0.0) -> float:
    value = parse_number(metrics.get(key))
    if value is None:
        return default
    return float(value)


def metric_text(metrics: Dict[str, str], key: str, default: str = "") -> str:
    value = metrics.get(key)
    if value is None:
        return default
    trimmed = value.strip()
    return trimmed if trimmed else default


def write_tsv(path: Path, fieldnames: Iterable[str], rows: Iterable[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def status_to_confidence(status: str) -> str:
    normalized = (status or "").strip().lower()
    if normalized == "split_supported":
        return "high"
    if normalized == "provisional_split_supported":
        return "medium"
    if normalized == "split_detected_unresolved":
        return "exploratory"
    if normalized == "seam_only_unresolved":
        return "artifact_likely"
    if normalized == "no_junction_evidence":
        return "insufficient"
    return "unconfirmed"


def derive_confidence(split_reads: int, seam_reads: int, seam_fraction: float, boundary_start_fraction: float, in_boundary: bool) -> str:
    split_to_seam_ratio = (split_reads / seam_reads) if seam_reads > 0 else (999.0 if split_reads > 0 else 0.0)
    artifact_likely = in_boundary and split_reads < 3 and (seam_fraction >= 50.0 or boundary_start_fraction >= 60.0)
    if artifact_likely:
        return "artifact_likely"
    if split_reads >= 8 and seam_fraction <= 35.0 and boundary_start_fraction <= 40.0 and split_to_seam_ratio >= 0.5:
        return "high"
    if split_reads >= 5 and seam_fraction <= 50.0 and boundary_start_fraction <= 50.0:
        return "medium"
    if split_reads >= 3 and (not in_boundary or seam_fraction <= 70.0):
        return "low"
    if split_reads >= 1:
        return "exploratory"
    return "unconfirmed"


def confidence_weight(confidence: str) -> float:
    normalized = (confidence or "").strip().lower()
    weights = {
        "high": 4.0,
        "medium": 3.0,
        "low": 2.0,
        "exploratory": 1.0,
        "unconfirmed": 0.0,
        "insufficient": -1.0,
        "artifact_likely": -2.0,
    }
    return weights.get(normalized, 0.0)


def build_secondary_anomalies(
    position_stats: List[Dict[str, object]],
    *,
    aligned_dimer_reads: int,
    total_split_support_reads: int,
    seam_support_reads: int,
    boundary_window_bp: int,
    boundary_window_support_pct: float,
) -> Dict[str, object]:
    rows: List[Dict[str, str]] = []
    non_boundary_support_positions = 0
    non_boundary_split_positions = 0
    non_boundary_support_reads = 0
    non_boundary_split_reads = 0
    non_boundary_dimer_ref_split_reads = 0

    for stat in position_stats:
        pos = int(stat.get("position_mod_ref", 0) or 0)
        support_reads = int(stat.get("support_reads", 0) or 0)
        split_support_reads = int(stat.get("split_support_reads", 0) or 0)
        dimer_ref_split_reads = int(stat.get("dimer_ref_split_reads", 0) or 0)
        single_ref_split_reads = int(stat.get("single_ref_split_reads", 0) or 0)
        seam_support_reads_pos = int(stat.get("seam_support_reads", 0) or 0)
        in_boundary_window = int(stat.get("in_boundary_window", 0) or 0)
        artifact_flag = int(stat.get("artifact_flag", 0) or 0)
        confidence = str(stat.get("confidence", "") or "")
        support_pct_all = float(stat.get("support_pct_all", 0.0) or 0.0)
        split_pct_of_all_split = float(stat.get("split_pct_of_all_split", 0.0) or 0.0)
        boundary_start_fraction = float(stat.get("boundary_start_fraction", 0.0) or 0.0)
        seam_fraction = float(stat.get("seam_fraction", 0.0) or 0.0)
        split_to_seam_ratio = float(stat.get("split_to_seam_ratio_numeric", 0.0) or 0.0)
        window_label = str(stat.get("junction_window_label", "") or "")
        window_seq = str(stat.get("junction_window_seq", "") or "")

        is_non_boundary = in_boundary_window == 0
        if is_non_boundary and support_reads > 0:
            non_boundary_support_positions += 1
            non_boundary_support_reads += support_reads
        if is_non_boundary and split_support_reads > 0:
            non_boundary_split_positions += 1
            non_boundary_split_reads += split_support_reads
            non_boundary_dimer_ref_split_reads += dimer_ref_split_reads

        # Candidate filtering:
        # - prefer non-boundary positions with either split support or repeated crossing support
        # - include boundary rows only when split support is substantial and not artifact-tagged
        include = False
        if is_non_boundary and (split_support_reads > 0 or support_reads >= 2):
            include = True
        elif (not is_non_boundary) and split_support_reads >= 3 and artifact_flag == 0:
            include = True
        if not include:
            continue

        if is_non_boundary and dimer_ref_split_reads > 0:
            anomaly_type = "non_boundary_dimer_split_hotspot"
        elif is_non_boundary and split_support_reads > 0:
            anomaly_type = "non_boundary_split_hotspot"
        elif is_non_boundary:
            anomaly_type = "non_boundary_crossing_hotspot"
        else:
            anomaly_type = "boundary_split_candidate"

        score = 0.0
        score += 8.0 if is_non_boundary else 1.0
        score += min(30.0, support_reads * 0.75)
        score += min(40.0, split_support_reads * 2.0)
        score += min(30.0, dimer_ref_split_reads * 4.0)
        score += min(30.0, single_ref_split_reads * 2.0)
        score += confidence_weight(confidence) * 1.5
        if dimer_ref_split_reads > 0:
            score += 3.0
        if split_support_reads > 0 and split_to_seam_ratio >= 1.0:
            score += 2.0
        if artifact_flag == 1:
            score -= 6.0
        if seam_fraction >= 90.0 and split_support_reads <= 0:
            score -= 3.0
        if (not is_non_boundary) and boundary_start_fraction >= 80.0:
            score -= 2.0

        rationale: List[str] = []
        rationale.append("outside_boundary_window" if is_non_boundary else "inside_boundary_window")
        if dimer_ref_split_reads > 0:
            rationale.append(f"dimer_ref_split={dimer_ref_split_reads}")
        if single_ref_split_reads > 0:
            rationale.append(f"single_ref_split={single_ref_split_reads}")
        if split_support_reads <= 0:
            rationale.append("no_split_support")
        if artifact_flag == 1:
            rationale.append("artifact_flagged")
        if confidence:
            rationale.append(f"confidence={confidence}")
        if seam_fraction >= 90.0:
            rationale.append("seam_dominant")

        rows.append(
            {
                "rank": "0",  # assigned after sorting
                "anomaly_type": anomaly_type,
                "anomaly_score": f"{score:.4f}",
                "position_mod_ref": str(pos),
                "support_reads": str(support_reads),
                "split_support_reads": str(split_support_reads),
                "dimer_ref_split_reads": str(dimer_ref_split_reads),
                "single_ref_split_reads": str(single_ref_split_reads),
                "seam_support_reads": str(seam_support_reads_pos),
                "support_pct_all": fmt_pct(support_pct_all),
                "split_pct_of_all_split": fmt_pct(split_pct_of_all_split),
                "split_to_seam_ratio": fmt_pct(split_to_seam_ratio),
                "in_boundary_window": str(in_boundary_window),
                "artifact_flag": str(artifact_flag),
                "confidence": confidence,
                "junction_window_label": window_label,
                "junction_window_seq": window_seq,
                "rationale": ";".join(rationale),
                "_sort_score": f"{score:.8f}",
            }
        )

    rows.sort(
        key=lambda row: (
            int(row.get("in_boundary_window", "1") or 1),
            -float(row.get("_sort_score", "0") or 0.0),
            -int(row.get("dimer_ref_split_reads", "0") or 0),
            -int(row.get("split_support_reads", "0") or 0),
            -int(row.get("support_reads", "0") or 0),
            int(row.get("position_mod_ref", "0") or 0),
        )
    )
    for idx, row in enumerate(rows, start=1):
        row["rank"] = str(idx)
        row.pop("_sort_score", None)

    top = rows[0] if rows else {}

    if non_boundary_dimer_ref_split_reads > 0:
        secondary_status = "anchored_non_boundary_split"
        recommendation = "Inspect top non-boundary split hotspot and validate with read-level edits."
    elif non_boundary_split_reads >= 2:
        secondary_status = "non_boundary_split_candidate"
        recommendation = "Prioritize non-boundary split candidates and validate consistency across read subsets."
    elif rows:
        secondary_status = "weak_secondary_candidates_only"
        recommendation = "No strong split anchor; review top non-boundary crossing hotspots for subtle anomalies."
    else:
        secondary_status = "seam_degenerate_no_secondary_anchor"
        recommendation = "Signal remains seam-degenerate; no secondary anchor detected in this run."

    summary_row = {
        "secondary_signal_status": secondary_status,
        "candidate_count": str(len(rows)),
        "non_boundary_candidate_count": str(sum(1 for row in rows if row.get("in_boundary_window") == "0")),
        "non_boundary_support_positions": str(non_boundary_support_positions),
        "non_boundary_split_positions": str(non_boundary_split_positions),
        "non_boundary_support_reads": str(non_boundary_support_reads),
        "non_boundary_split_reads": str(non_boundary_split_reads),
        "non_boundary_dimer_ref_split_reads": str(non_boundary_dimer_ref_split_reads),
        "aligned_dimer_reads": str(aligned_dimer_reads),
        "total_split_support_reads": str(total_split_support_reads),
        "seam_support_reads": str(seam_support_reads),
        "boundary_window_bp": str(boundary_window_bp),
        "boundary_window_support_pct": fmt_pct(boundary_window_support_pct),
        "top_anomaly_type": top.get("anomaly_type", ""),
        "top_position_mod_ref": top.get("position_mod_ref", ""),
        "top_anomaly_score": top.get("anomaly_score", ""),
        "top_support_reads": top.get("support_reads", ""),
        "top_split_support_reads": top.get("split_support_reads", ""),
        "top_dimer_ref_split_reads": top.get("dimer_ref_split_reads", ""),
        "top_single_ref_split_reads": top.get("single_ref_split_reads", ""),
        "top_confidence": top.get("confidence", ""),
        "top_junction_window_label": top.get("junction_window_label", ""),
        "top_rationale": top.get("rationale", ""),
        "recommendation": recommendation,
    }

    return {"rows": rows, "summary": summary_row}


def main() -> None:
    args = parse_args()

    metrics = read_summary_metrics(args.summary)

    ref_len = metric_int(metrics, "reference_length", default=0)
    boundary_window_bp = metric_int(metrics, "boundary_window_bp", default=0)
    aligned_dimer_reads = metric_int(metrics, "aligned_dimer_reads", default=0)
    dimer_candidate_reads = metric_int(metrics, "dimer_candidate_reads", default=0)
    junction_spanning_reads = metric_int(metrics, "junction_spanning_reads", default=0)
    dimer_ref_split_reads_metric = metric_int(
        metrics,
        "junction_event_split_reads_dimer_ref",
        default=metric_int(metrics, "junction_event_split_reads", default=0),
    )
    single_ref_split_reads_metric = metric_int(metrics, "single_ref_split_support_reads", default=0)
    total_split_support_reads_metric = metric_int(
        metrics,
        "split_support_reads",
        default=(dimer_ref_split_reads_metric + single_ref_split_reads_metric),
    )
    seam_support_reads_metric = metric_int(metrics, "junction_event_seam_reads", default=0)
    boundary_window_support_reads_metric = metric_int(metrics, "boundary_window_support_reads", default=0)
    boundary_window_support_pct_metric = metric_float(metrics, "boundary_window_support_pct", default=0.0)
    requested_window_bp = max(1, args.window_bp)

    reference_record = read_first_fasta_record(args.reference_fasta)
    reference_name = reference_record.get("name", "").strip() or metric_text(metrics, "reference_name", default="")
    reference_sequence_raw = reference_record.get("sequence", "").strip().upper()
    if reference_sequence_raw and ref_len > 0 and len(reference_sequence_raw) > ref_len:
        # Align to mod-ref coordinates when a concatenated dimer reference is supplied.
        reference_sequence = reference_sequence_raw[:ref_len]
    else:
        reference_sequence = reference_sequence_raw

    events = read_tsv_rows(args.events)
    single_ref_events = read_tsv_rows(args.single_ref_events)
    single_ref_profile = read_tsv_rows(args.single_ref_profile)
    breakpoint_screen = read_tsv_rows(args.breakpoint_screen)

    # Position-level counters from dimer-reference event table.
    pos_dimer_support: Dict[int, int] = defaultdict(int)
    pos_dimer_split: Dict[int, int] = defaultdict(int)
    pos_dimer_seam: Dict[int, int] = defaultdict(int)
    pos_dimer_single: Dict[int, int] = defaultdict(int)
    pos_boundary_start: Dict[int, int] = defaultdict(int)
    pos_event_count: Dict[int, int] = defaultdict(int)
    read_event_rows: List[Dict[str, str]] = []

    for raw_row in events:
        row = to_lower_map(raw_row)
        pos = parse_int(row.get("position_mod_ref"))
        crosses = parse_bool(row.get("crosses_junction"))
        event_type = row.get("event_type", "").strip().lower()
        start = parse_int(row.get("start"))

        if pos is not None and pos > 0 and crosses:
            pos_dimer_support[pos] += 1
            pos_event_count[pos] += 1
            if event_type == "split":
                pos_dimer_split[pos] += 1
            elif event_type == "seam":
                pos_dimer_seam[pos] += 1
            else:
                pos_dimer_single[pos] += 1
            if (
                start is not None
                and start > 0
                and ref_len > 0
                and boundary_window_bp > 0
                and (start <= boundary_window_bp or start > (ref_len - boundary_window_bp))
            ):
                pos_boundary_start[pos] += 1

        read_event_rows.append(
            {
                "read_id": row.get("read_id", ""),
                "start": row.get("start", ""),
                "end": row.get("end", ""),
                "position_mod_ref": row.get("position_mod_ref", ""),
                "crosses_junction": "1" if crosses else "0",
                "event_type": row.get("event_type", ""),
                "method": row.get("method", ""),
                "segment_count": row.get("segment_count", ""),
                "left_ref": row.get("left_ref", ""),
                "right_ref": row.get("right_ref", ""),
                "left_mod_ref": row.get("left_mod_ref", ""),
                "right_mod_ref": row.get("right_mod_ref", ""),
                "missing_bp": row.get("missing_bp", ""),
                "missing_left_bp": row.get("missing_left_bp", ""),
                "missing_right_bp": row.get("missing_right_bp", ""),
                "support_bp": row.get("support_bp", ""),
                "orientation": row.get("orientation", ""),
                "copy_transition": row.get("copy_transition", ""),
                "evidence_source": "dimer_ref",
            }
        )

    # Single-reference split support profile.
    pos_single_ref_split: Dict[int, int] = defaultdict(int)
    for raw_row in single_ref_profile:
        row = to_lower_map(raw_row)
        pos = parse_int(row.get("position_mod_ref"))
        support = parse_int(row.get("split_support_reads"))
        if support is None:
            support = parse_int(row.get("support_reads"))
        if support is None:
            support = parse_int(row.get("read_count"))
        if pos is None or pos <= 0 or support is None or support <= 0:
            continue
        pos_single_ref_split[pos] += support

    # Append single-reference split events to canonical read-events table.
    for raw_row in single_ref_events:
        row = to_lower_map(raw_row)
        pos = parse_int(row.get("position_mod_ref"))
        read_event_rows.append(
            {
                "read_id": row.get("read_id", ""),
                "start": "",
                "end": "",
                "position_mod_ref": "" if pos is None else str(pos),
                "crosses_junction": "1",
                "event_type": "split",
                "method": row.get("method", "single_ref_adjacent_split"),
                "segment_count": row.get("segment_count", ""),
                "left_ref": row.get("left_ref", ""),
                "right_ref": row.get("right_ref", ""),
                "left_mod_ref": "",
                "right_mod_ref": "",
                "missing_bp": "",
                "missing_left_bp": "",
                "missing_right_bp": "",
                "support_bp": row.get("support_bp", ""),
                "orientation": row.get("orientation_pair", ""),
                "copy_transition": "",
                "evidence_source": "single_ref_split",
            }
        )

    # Optional carry-through metadata from legacy breakpoint screen.
    screen_by_pos: Dict[int, Dict[str, str]] = {}
    for raw_row in breakpoint_screen:
        row = to_lower_map(raw_row)
        pos = parse_int(row.get("position_mod_ref"))
        if pos is None or pos <= 0:
            continue
        screen_by_pos[pos] = row

    total_dimer_support = sum(pos_dimer_support.values())
    total_single_ref_split = sum(pos_single_ref_split.values())
    total_split_support = sum((pos_dimer_split[p] + pos_single_ref_split[p]) for p in set(pos_dimer_split) | set(pos_single_ref_split))
    if total_split_support <= 0:
        total_split_support = total_split_support_reads_metric

    evidence_rows: List[Dict[str, str]] = []
    position_stats: List[Dict[str, object]] = []
    positions = sorted(set(pos_dimer_support) | set(pos_single_ref_split) | set(screen_by_pos))
    for pos in positions:
        dimer_support = pos_dimer_support.get(pos, 0)
        dimer_split = pos_dimer_split.get(pos, 0)
        dimer_seam = pos_dimer_seam.get(pos, 0)
        dimer_single = pos_dimer_single.get(pos, 0)
        single_ref_split = pos_single_ref_split.get(pos, 0)
        combined_split = dimer_split + single_ref_split

        in_boundary = 0
        if ref_len > 0 and boundary_window_bp > 0:
            in_boundary = 1 if (pos <= boundary_window_bp or pos > (ref_len - boundary_window_bp)) else 0

        boundary_start_reads = pos_boundary_start.get(pos, 0)
        position_event_reads = pos_event_count.get(pos, 0)

        support_pct = pct(dimer_support, total_dimer_support)
        dimer_split_pct_of_position = pct(dimer_split, dimer_support)
        split_pct_of_all_split = pct(combined_split, total_split_support)
        total_split_pct_of_aligned = pct(combined_split, aligned_dimer_reads)
        boundary_start_fraction = pct(boundary_start_reads, position_event_reads)
        seam_fraction = pct(dimer_seam, dimer_support)
        split_to_seam_ratio_numeric = (combined_split / dimer_seam) if dimer_seam > 0 else (999.0 if combined_split > 0 else 0.0)
        split_to_seam = fmt_ratio(float(combined_split), float(dimer_seam))

        screen = screen_by_pos.get(pos, {})
        artifact_flag_raw = parse_bool(screen.get("artifact_flag"))
        if artifact_flag_raw is None:
            artifact_flag_raw = parse_bool(screen.get("artifact_likely"))
        if artifact_flag_raw is None:
            artifact_flag = 1 if (in_boundary == 1 and combined_split < 3 and (seam_fraction >= 50.0 or boundary_start_fraction >= 60.0)) else 0
        else:
            artifact_flag = 1 if artifact_flag_raw else 0

        confidence = screen.get("confidence", "")
        if not confidence:
            confidence = derive_confidence(combined_split, dimer_seam, seam_fraction, boundary_start_fraction, in_boundary == 1)

        window_label = ""
        window_seq = ""
        if reference_sequence:
            window = format_circular_junction_window(reference_sequence, pos, requested_window_bp)
            window_label = window["label"]
            window_seq = window["window"]

        evidence_rows.append(
            {
                "position_mod_ref": str(pos),
                "support_reads": str(dimer_support),
                "crossing_reads": str(dimer_support),
                "support_percent": fmt_pct(support_pct),
                "support_pct_all": fmt_pct(support_pct),
                "total_support_reads": str(dimer_support),
                "seam_support_reads": str(dimer_seam),
                "split_support_reads": str(combined_split),
                "dimer_ref_split_reads": str(dimer_split),
                "single_ref_split_reads": str(single_ref_split),
                "split_pct_of_position": fmt_pct(dimer_split_pct_of_position),
                "split_pct_of_all_split": fmt_pct(split_pct_of_all_split),
                "total_split_pct_of_aligned_reads": fmt_pct(total_split_pct_of_aligned),
                "in_boundary_window": str(in_boundary),
                "boundary_start_reads": str(boundary_start_reads),
                "boundary_start_fraction": fmt_pct(boundary_start_fraction),
                "seam_fraction": fmt_pct(seam_fraction),
                "split_to_seam_ratio": split_to_seam,
                "artifact_flag": str(artifact_flag),
                "confidence": confidence,
                "junction_window_label": window_label,
                "junction_window_seq": window_seq,
            }
        )
        position_stats.append(
            {
                "position_mod_ref": pos,
                "support_reads": dimer_support,
                "split_support_reads": combined_split,
                "dimer_ref_split_reads": dimer_split,
                "single_ref_split_reads": single_ref_split,
                "seam_support_reads": dimer_seam,
                "support_pct_all": support_pct,
                "split_pct_of_all_split": split_pct_of_all_split,
                "in_boundary_window": in_boundary,
                "artifact_flag": artifact_flag,
                "confidence": confidence,
                "boundary_start_fraction": boundary_start_fraction,
                "seam_fraction": seam_fraction,
                "split_to_seam_ratio_numeric": split_to_seam_ratio_numeric,
                "junction_window_label": window_label,
                "junction_window_seq": window_seq,
            }
        )

    # Primary breakpoint call.
    breakpoint_status = metric_text(metrics, "breakpoint_model_status", default="not_evaluable")
    screened_pos = parse_int(metrics.get("screened_primary_breakpoint_position_mod_ref"))
    screened_support = metric_int(metrics, "screened_primary_breakpoint_support_reads", default=0)
    screened_confidence = metric_text(metrics, "screened_primary_breakpoint_confidence", default="")

    dominant_split_pos = parse_int(metrics.get("dominant_split_junction_position_mod_ref"))
    dominant_split_support = metric_int(metrics, "dominant_split_junction_support_reads", default=0)
    single_ref_dom_pos = parse_int(metrics.get("single_ref_dominant_split_position_mod_ref"))
    single_ref_dom_support = metric_int(metrics, "single_ref_dominant_split_support_reads", default=0)
    dominant_junction_pos = parse_int(metrics.get("dominant_junction_position_mod_ref"))
    dominant_junction_support = metric_int(metrics, "dominant_junction_support_reads", default=0)

    primary_pos: Optional[int] = None
    primary_support = 0
    primary_source = "none"
    if screened_pos is not None and screened_support > 0:
        primary_pos = screened_pos
        primary_support = screened_support
        primary_source = "screened_primary_breakpoint"
    elif dominant_split_pos is not None and dominant_split_support > 0:
        primary_pos = dominant_split_pos
        primary_support = dominant_split_support
        primary_source = "dominant_split_hotspot"
    elif single_ref_dom_pos is not None and single_ref_dom_support > 0:
        primary_pos = single_ref_dom_pos
        primary_support = single_ref_dom_support
        primary_source = "single_ref_split_hotspot"
    elif dominant_junction_pos is not None and dominant_junction_support > 0:
        primary_pos = dominant_junction_pos
        primary_support = dominant_junction_support
        primary_source = "dominant_junction_hotspot"

    call_confidence = screened_confidence if screened_confidence else status_to_confidence(breakpoint_status)
    primary_support_pct_aligned = pct(primary_support, aligned_dimer_reads)
    primary_window_label = ""
    primary_window_seq = ""
    if primary_pos is not None and reference_sequence:
        primary_window = format_circular_junction_window(reference_sequence, primary_pos, requested_window_bp)
        primary_window_label = primary_window["label"]
        primary_window_seq = primary_window["window"]

    notes: List[str] = []
    if total_split_support_reads_metric > 0 and dimer_ref_split_reads_metric == 0 and single_ref_split_reads_metric > 0:
        notes.append("split support originates from single-reference remap only")
    if boundary_window_support_pct_metric >= 40.0 and total_split_support_reads_metric < 3:
        notes.append("boundary-dominant seam signal")
    if primary_pos is None:
        notes.append("no primary breakpoint selected")

    call_row = {
        "call_status": breakpoint_status,
        "call_confidence": call_confidence,
        "primary_position_mod_ref": "" if primary_pos is None else str(primary_pos),
        "primary_support_reads": str(primary_support),
        "primary_support_pct_of_aligned_reads": fmt_pct(primary_support_pct_aligned),
        "primary_source": primary_source,
        "screened_primary_breakpoint_position_mod_ref": "" if screened_pos is None else str(screened_pos),
        "screened_primary_breakpoint_support_reads": str(screened_support),
        "dominant_split_position_mod_ref": "" if dominant_split_pos is None else str(dominant_split_pos),
        "dominant_split_support_reads": str(dominant_split_support),
        "single_ref_dominant_split_position_mod_ref": "" if single_ref_dom_pos is None else str(single_ref_dom_pos),
        "single_ref_dominant_split_support_reads": str(single_ref_dom_support),
        "dominant_junction_position_mod_ref": "" if dominant_junction_pos is None else str(dominant_junction_pos),
        "dominant_junction_support_reads": str(dominant_junction_support),
        "dimer_candidate_reads": str(dimer_candidate_reads),
        "aligned_dimer_reads": str(aligned_dimer_reads),
        "junction_spanning_reads": str(junction_spanning_reads),
        "dimer_ref_split_reads": str(dimer_ref_split_reads_metric),
        "single_ref_split_reads": str(single_ref_split_reads_metric),
        "total_split_support_reads": str(total_split_support_reads_metric),
        "seam_support_reads": str(seam_support_reads_metric),
        "boundary_window_bp": str(boundary_window_bp),
        "boundary_window_support_reads": str(boundary_window_support_reads_metric),
        "boundary_window_support_pct": fmt_pct(boundary_window_support_pct_metric),
        "primary_junction_window_label": primary_window_label,
        "primary_junction_window_seq": primary_window_seq,
        "notes": "; ".join(notes),
    }

    write_tsv(
        args.out_call,
        [
            "call_status",
            "call_confidence",
            "primary_position_mod_ref",
            "primary_support_reads",
            "primary_support_pct_of_aligned_reads",
            "primary_source",
            "screened_primary_breakpoint_position_mod_ref",
            "screened_primary_breakpoint_support_reads",
            "dominant_split_position_mod_ref",
            "dominant_split_support_reads",
            "single_ref_dominant_split_position_mod_ref",
            "single_ref_dominant_split_support_reads",
            "dominant_junction_position_mod_ref",
            "dominant_junction_support_reads",
            "dimer_candidate_reads",
            "aligned_dimer_reads",
            "junction_spanning_reads",
            "dimer_ref_split_reads",
            "single_ref_split_reads",
            "total_split_support_reads",
            "seam_support_reads",
            "boundary_window_bp",
            "boundary_window_support_reads",
            "boundary_window_support_pct",
            "primary_junction_window_label",
            "primary_junction_window_seq",
            "notes",
        ],
        [call_row],
    )

    write_tsv(
        args.out_evidence,
        [
            "position_mod_ref",
            "support_reads",
            "crossing_reads",
            "support_percent",
            "support_pct_all",
            "total_support_reads",
            "seam_support_reads",
            "split_support_reads",
            "dimer_ref_split_reads",
            "single_ref_split_reads",
            "split_pct_of_position",
            "split_pct_of_all_split",
            "total_split_pct_of_aligned_reads",
            "in_boundary_window",
            "boundary_start_reads",
            "boundary_start_fraction",
            "seam_fraction",
            "split_to_seam_ratio",
            "artifact_flag",
            "confidence",
            "junction_window_label",
            "junction_window_seq",
        ],
        evidence_rows,
    )

    secondary_outputs = build_secondary_anomalies(
        position_stats,
        aligned_dimer_reads=aligned_dimer_reads,
        total_split_support_reads=total_split_support_reads_metric,
        seam_support_reads=seam_support_reads_metric,
        boundary_window_bp=boundary_window_bp,
        boundary_window_support_pct=boundary_window_support_pct_metric,
    )
    secondary_anomaly_rows = secondary_outputs["rows"]
    secondary_summary_row = secondary_outputs["summary"]

    if args.out_secondary_anomalies is not None:
        write_tsv(
            args.out_secondary_anomalies,
            [
                "rank",
                "anomaly_type",
                "anomaly_score",
                "position_mod_ref",
                "support_reads",
                "split_support_reads",
                "dimer_ref_split_reads",
                "single_ref_split_reads",
                "seam_support_reads",
                "support_pct_all",
                "split_pct_of_all_split",
                "split_to_seam_ratio",
                "in_boundary_window",
                "artifact_flag",
                "confidence",
                "junction_window_label",
                "junction_window_seq",
                "rationale",
            ],
            secondary_anomaly_rows,
        )

    if args.out_secondary_summary is not None:
        write_tsv(
            args.out_secondary_summary,
            [
                "secondary_signal_status",
                "candidate_count",
                "non_boundary_candidate_count",
                "non_boundary_support_positions",
                "non_boundary_split_positions",
                "non_boundary_support_reads",
                "non_boundary_split_reads",
                "non_boundary_dimer_ref_split_reads",
                "aligned_dimer_reads",
                "total_split_support_reads",
                "seam_support_reads",
                "boundary_window_bp",
                "boundary_window_support_pct",
                "top_anomaly_type",
                "top_position_mod_ref",
                "top_anomaly_score",
                "top_support_reads",
                "top_split_support_reads",
                "top_dimer_ref_split_reads",
                "top_single_ref_split_reads",
                "top_confidence",
                "top_junction_window_label",
                "top_rationale",
                "recommendation",
            ],
            [secondary_summary_row],
        )

    if args.out_breakpoint_sequences is not None:
        breakpoint_sequence_rows = [
            {
                "reference_name": reference_name,
                "reference_length": str(ref_len if ref_len > 0 else len(reference_sequence)),
                "window_bp": str(requested_window_bp),
                "position_mod_ref": row.get("position_mod_ref", ""),
                "support_reads": row.get("support_reads", ""),
                "split_support_reads": row.get("split_support_reads", ""),
                "seam_support_reads": row.get("seam_support_reads", ""),
                "in_boundary_window": row.get("in_boundary_window", ""),
                "artifact_flag": row.get("artifact_flag", ""),
                "confidence": row.get("confidence", ""),
                "junction_window_label": row.get("junction_window_label", ""),
                "junction_window_seq": row.get("junction_window_seq", ""),
            }
            for row in evidence_rows
        ]
        write_tsv(
            args.out_breakpoint_sequences,
            [
                "reference_name",
                "reference_length",
                "window_bp",
                "position_mod_ref",
                "support_reads",
                "split_support_reads",
                "seam_support_reads",
                "in_boundary_window",
                "artifact_flag",
                "confidence",
                "junction_window_label",
                "junction_window_seq",
            ],
            breakpoint_sequence_rows,
        )

    write_tsv(
        args.out_read_events,
        [
            "read_id",
            "start",
            "end",
            "position_mod_ref",
            "crosses_junction",
            "event_type",
            "method",
            "segment_count",
            "left_ref",
            "right_ref",
            "left_mod_ref",
            "right_mod_ref",
            "missing_bp",
            "missing_left_bp",
            "missing_right_bp",
            "support_bp",
            "orientation",
            "copy_transition",
            "evidence_source",
        ],
        read_event_rows,
    )


if __name__ == "__main__":
    main()
