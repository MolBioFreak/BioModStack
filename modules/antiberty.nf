// AntiBERTy - Antibody Immunogenicity Scoring Module
// Uses pseudo-log-likelihood to assess sequence "naturalness"
// Low PLL = unusual sequence = higher immunogenicity risk

process ANTIBERTY_SCORE {
    tag "${meta.id}"
    label 'process_medium'
    container "${params.container_dir}/antibody_tools.sif"

    input:
    tuple val(meta), path(input_file)  // Can be FASTA or PDB

    output:
    tuple val(meta), path("${meta.id}_pll_scores.csv"), emit: scores
    tuple val(meta), path("${meta.id}_embeddings.pt"), emit: embeddings, optional: true
    path "antiberty_${meta.id}.log"

    script:
    def extract_emb = params.antiberty_extract_embeddings ?: false
    """
    python3 << 'ANTIBERTY_SCRIPT'
import sys
import csv
import torch
from pathlib import Path

try:
    from antiberty import AntiBERTyRunner
    from Bio import SeqIO
    from Bio.PDB import PDBParser
    from Bio.SeqUtils import seq1
except ImportError as e:
    print(f"Import error: {e}", file=sys.stderr)
    sys.exit(1)

# Initialize model
print("Loading AntiBERTy model...")
antiberty = AntiBERTyRunner()

# Parse sequences - auto-detect file type
input_file = "${input_file}"
sequences = []
seq_ids = []

# Check file extension
if input_file.endswith('.pdb'):
    # Extract sequences from PDB
    print(f"Detected PDB file, extracting sequences...")
    parser = PDBParser(QUIET=True)
    structure = parser.get_structure("structure", input_file)
    
    for model in structure:
        for chain in model:
            residues = [r for r in chain if r.id[0] == ' ']  # Standard residues only
            if residues:
                seq = ''.join([seq1(r.resname) for r in residues])
                if seq and len(seq) > 10:  # Skip very short chains
                    seq_ids.append(f"{Path(input_file).stem}_{chain.id}")
                    sequences.append(seq)
                    print(f"  Chain {chain.id}: {len(seq)} residues")
else:
    # Parse as FASTA
    for record in SeqIO.parse(input_file, "fasta"):
        sequences.append(str(record.seq))
        seq_ids.append(record.id)

if not sequences:
    print(f"Error: No sequences found in {input_file} (file type: {'PDB' if input_file.endswith('.pdb') else 'FASTA'})", file=sys.stderr)
    sys.exit(1)

print(f"Processing {len(sequences)} sequences...")

# Calculate pseudo-log-likelihood scores
pll_scores = antiberty.pseudo_log_likelihood(sequences, batch_size=8)

# Classify species and chain type
species_preds, chain_preds = antiberty.classify(sequences)

# Write scores to CSV
output_csv = "${meta.id}_pll_scores.csv"
with open(output_csv, 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['sequence_id', 'sequence', 'pll_score', 'species', 'chain_type', 'naturalness_flag'])
    
    for seq_id, seq, pll, species, chain in zip(seq_ids, sequences, pll_scores, species_preds, chain_preds):
        # Flag sequences with low naturalness (high immunogenicity risk)
        # Threshold is configurable, default -10 is conservative
        threshold = ${params.antiberty_pll_threshold ?: -10}
        flag = "HIGH_RISK" if pll < threshold else "OK"
        writer.writerow([seq_id, seq[:50] + "..." if len(seq) > 50 else seq, 
                        f"{pll:.4f}", species, chain, flag])

print(f"Scores written to {output_csv}")

# Optionally extract embeddings
extract_embeddings = ${extract_emb ? 'True' : 'False'}
if extract_embeddings:
    print("Extracting embeddings...")
    embeddings = antiberty.embed(sequences)
    torch.save(embeddings, "${meta.id}_embeddings.pt")
    print("Embeddings saved")

print("AntiBERTy scoring complete")
ANTIBERTY_SCRIPT

    # Capture log
    mv /dev/null antiberty_${meta.id}.log 2>/dev/null || touch antiberty_${meta.id}.log
    """
}

// Filter sequences based on AntiBERTy PLL scores
process ANTIBERTY_FILTER {
    tag "${meta.id}"
    label 'process_low'
    container "${params.container_dir}/antibody_tools.sif"

    input:
    tuple val(meta), path(scores_csv), path(fasta)

    output:
    tuple val(meta), path("${meta.id}_filtered.fasta"), emit: filtered_fasta
    tuple val(meta), path("${meta.id}_filter_report.json"), emit: report

    script:
    def threshold = params.antiberty_pll_threshold ?: -10
    """
    python3 << 'FILTER_SCRIPT'
import csv
import json
from Bio import SeqIO
from Bio.SeqRecord import SeqRecord
from Bio.Seq import Seq

threshold = ${threshold}

# Read scores
scores = {}
with open("${scores_csv}") as f:
    reader = csv.DictReader(f)
    for row in reader:
        scores[row['sequence_id']] = float(row['pll_score'])

# Filter FASTA
filtered_records = []
rejected = []

for record in SeqIO.parse("${fasta}", "fasta"):
    pll = scores.get(record.id, float('-inf'))
    if pll >= threshold:
        filtered_records.append(record)
    else:
        rejected.append({'id': record.id, 'pll': pll})

# Write filtered FASTA
SeqIO.write(filtered_records, "${meta.id}_filtered.fasta", "fasta")

# Write report
report = {
    'total_sequences': len(scores),
    'passed': len(filtered_records),
    'rejected': len(rejected),
    'threshold': threshold,
    'rejected_sequences': rejected[:20]  # First 20 for brevity
}

with open("${meta.id}_filter_report.json", 'w') as f:
    json.dump(report, f, indent=2)

print(f"Filtered: {len(filtered_records)}/{len(scores)} passed (threshold: {threshold})")
FILTER_SCRIPT
    """
}

// Filter structure records while preserving PDB-based downstream flow.
process ANTIBERTY_FILTER_STRUCTURES {
    tag "${meta.id}"
    label 'process_low'
    container "${params.container_dir}/antibody_tools.sif"

    input:
    tuple val(meta), path(scores_csv), path(structure_pdb)

    output:
    tuple val(meta), path("${meta.id}_filtered.pdb"), emit: filtered_pdb, optional: true
    tuple val(meta), path("${meta.id}_filter_report.json"), emit: report

    script:
    def threshold = params.antiberty_pll_threshold ?: -10
    """
    python3 << 'FILTER_STRUCTURES_SCRIPT'
import csv
import json
import shutil
from pathlib import Path

threshold = ${threshold}
scores = []

with open("${scores_csv}") as handle:
    reader = csv.DictReader(handle)
    for row in reader:
        try:
            pll = float(row["pll_score"])
        except (TypeError, ValueError, KeyError):
            continue
        row["pll_score"] = pll
        scores.append(row)

antibody_like = {
    "heavy",
    "light",
    "kappa",
    "lambda",
    "vh",
    "vl",
    "vhh",
    "nanobody",
    "single_domain",
}

selected_rows = []
for row in scores:
    chain_type = str(row.get("chain_type", "")).strip().lower()
    if chain_type in antibody_like or chain_type.startswith("heavy") or chain_type.startswith("light"):
        selected_rows.append(row)

if not selected_rows:
    selected_rows = scores

aggregate_pll = min((row["pll_score"] for row in selected_rows), default=float("-inf"))
passed = bool(selected_rows) and aggregate_pll >= threshold

report = {
    "structure_id": "${meta.id}",
    "structure_path": "${structure_pdb}",
    "threshold": threshold,
    "passed": passed,
    "aggregate_pll": aggregate_pll if selected_rows else None,
    "aggregation_mode": "min_selected_chain_pll",
    "selected_row_count": len(selected_rows),
    "total_row_count": len(scores),
    "selected_rows": [
        {
            "sequence_id": row.get("sequence_id"),
            "chain_type": row.get("chain_type"),
            "pll_score": row.get("pll_score"),
        }
        for row in selected_rows[:10]
    ],
}

if passed:
    shutil.copy2("${structure_pdb}", "${meta.id}_filtered.pdb")

with open("${meta.id}_filter_report.json", "w") as handle:
    json.dump(report, handle, indent=2)

print(
    f"Structure ${meta.id}: {'passed' if passed else 'rejected'} "
    f"(aggregate_pll={aggregate_pll if selected_rows else 'N/A'}, threshold={threshold})"
)
FILTER_STRUCTURES_SCRIPT
    """
}
