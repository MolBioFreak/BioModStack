#!/usr/bin/env bash
set -euo pipefail

: > dimer_candidates.fastq
: > dimer_candidates.fasta
: > dimer_read_lengths.tsv
: > dimer_read_junctions.tsv
: > dimer_alignment.log
: > dimer_single_ref_alignment.log
: > dimer_consensus.log
: > dominant_dimer_consensus.log

printf 'position_mod_ref\tread_count\tjunction_spanning_reads\n' > dimer_junction_profile.tsv
printf 'read_id\tstart\tend\tposition_mod_ref\tcrosses_junction\tevent_type\tmethod\tsegment_count\tleft_ref\tright_ref\tleft_mod_ref\tright_mod_ref\tmissing_bp\tmissing_left_bp\tmissing_right_bp\tsupport_bp\torientation\tcopy_transition\n' > dimer_junction_events.tsv
printf 'position_mod_ref\tread_count\tcrossing_reads\tsupport_percent\tsupport_reads\tsplit_reads\tseam_reads\tsingle_reads\tmissing_bp_sum\tmissing_bp_mean\tmissing_bp_max\tsupport_bp_sum\tsupport_bp_mean\n' > dimer_junction_clusters.tsv
printf 'position_mod_ref\tsupport_reads\tsupport_pct\tin_boundary_window\n' > dimer_junction_hotspots.tsv
printf 'position_rotated\tposition_mod_ref\tsupport_reads\tsupport_pct\tin_boundary_window\n' > dimer_junction_rotated_profile.tsv
printf 'position_mod_ref\ttotal_support_reads\tseam_support_reads\tsplit_support_reads\tsupport_pct_all\tsplit_pct_of_position\tsplit_pct_of_all_split\tin_boundary_window\tboundary_start_reads\tboundary_start_fraction\tseam_fraction\tsplit_to_seam_ratio\tartifact_flag\tconfidence\n' > dimer_breakpoint_screen.tsv
printf 'position_mod_ref\tboundary_start_reads\tposition_event_reads\tboundary_seam_or_single_start_reads\n' > dimer_breakpoint_start_counts.tsv
printf 'read_id\tlen_bp\taligned\tevent_type\tmethod\tcrosses\tpos_mod\tstart\tend\tleft_mod\tright_mod\tmissing_bp\tmissing_left_bp\tmissing_right_bp\tsupport_bp\torientation\tcopy_transition\n' > dimer_read_ledger.tsv
printf 'read_id\tlen_bp\tevent_type\tmethod\tpos_mod\tleft_mod\tright_mod\tmissing_bp\tsupport_bp\tstart\tend\torientation\tcopy_transition\n' > dimer_breakpoint_reads.tsv
printf 'offset_bp\tmode\taligned_reads\ttotal_support\tboundary_support\tboundary_pct\tsplit_support\tseam_support\tsingle_support\tdom_rot\tdom_mod\tdom_reads\tdom_pct\tseam_only\n' > dimer_rotated_remap_summary.tsv
printf 'offset_bp\tpos_mod\tsupport\tsplit\tseam\tpos_rot\tin_boundary\n' > dimer_rotated_remap_breakpoints.tsv
printf 'read_id\tsegment_count\tleft_ref\tright_ref\tposition_mod_ref\tquery_gap_bp\tsupport_bp\torientation_pair\tmethod\n' > dimer_single_ref_split_events.tsv
printf 'position_mod_ref\tsplit_support_reads\tsupport_bp_sum\tsupport_bp_mean\tsupport_pct\n' > dimer_single_ref_split_profile.tsv
