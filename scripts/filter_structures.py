#!/usr/bin/env python3
"""
Unified structure filtering script for BioModStack.

Replaces 7 separate filter scripts with a single configurable tool.

Usage:
    # Backbone filtering (RFD/RFD3)
    python filter_structures.py backbone --input-dir . --output-dir filtered/ \\
        --min-helices 2 --max-rog 20
    
    # Sequence filtering (MPNN/FAMPNN)
    python filter_structures.py sequence --input-dir . --output-dir filtered/ \\
        --max-score 1.5
    
    # Prediction filtering (AF2/Boltz/RF3)
    python filter_structures.py prediction --input-dir . --output-dir filtered/ \\
        --min-plddt 70 --max-rmsd 3.0
"""

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Dict, Any, Optional

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent))

from lib.filtering.base import StructureFilter
from lib.filtering.metrics import (
    calculate_backbone_metrics,
    extract_confidence_metrics,
)
from lib.filtering.converters import load_structure, cif_to_pdb

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('filter_structures.log')
    ]
)
logger = logging.getLogger(__name__)


class BackboneFilter(StructureFilter):
    """Filter backbone structures from RFD/RFD3."""
    
    def extract_metrics(self, structure_path: Path, metadata: dict) -> Dict[str, Any]:
        """Extract SS and RoG metrics from backbone structure."""
        if self.core_protein_scientific_contract == 1:
            import hashlib
            import tempfile
            import biotite
            from lib.filtering.evidence import metric_evidence
            # Hash exactly the immutable bytes given to the existing parser,
            # including compression for .cif.gz; never reopen the source to hash.
            raw = structure_path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            with tempfile.TemporaryDirectory(prefix='backbone-snapshot-') as owned:
                snapshot = Path(owned) / structure_path.name
                snapshot.write_bytes(raw)
                structure = load_structure(snapshot)
            metrics: Dict[str, Any] = dict(calculate_backbone_metrics(structure, 1))
            provenance = {}
            for name in ('rog', 'helices', 'strands', 'total_ss'):
                provenance[name] = {
                    'source': str(structure_path), 'source_sha256': digest,
                    'calculation': ('peptide_backbone_coordinate_radius_of_gyration'
                                    if name == 'rog' else 'biotite.annotate_sse_contiguous_element_count'),
                    'calculation_version': 1, 'biotite_version': biotite.__version__,
                    'model': 1, 'evidence': metric_evidence(name, metrics.get(name)),
                }
            metrics['_descriptor_provenance'] = provenance
        else:
            structure = load_structure(structure_path)
            if structure is None:
                return {}
            metrics = calculate_backbone_metrics(structure, self.core_protein_scientific_contract)
        # Add any metrics from JSON metadata
        if self.core_protein_scientific_contract != 1:
            metrics.update(metadata)
        return metrics


class SequenceFilter(StructureFilter):
    """Filter sequences from MPNN/FAMPNN."""
    
    def extract_metrics(self, structure_path: Path, metadata: dict) -> Dict[str, Any]:
        """Extract sequence metrics from metadata."""
        metrics = {}
        
        # MPNN score (negative log likelihood)
        if 'score' in metadata:
            metrics['score'] = metadata['score']
        if 'mpnn_score' in metadata:
            metrics['score'] = metadata['mpnn_score']
        
        # FAMPNN PSCE
        if 'psce' in metadata:
            metrics['psce'] = metadata['psce']
        if 'fampnn_psce' in metadata:
            metrics['psce'] = metadata['fampnn_psce']
        
        return metrics


class PredictionFilter(StructureFilter):
    """Filter predictions from AF2/Boltz/RF3."""
    
    def extract_metrics(self, structure_path: Path, metadata: dict) -> Dict[str, Any]:
        """Extract confidence and RMSD metrics from predictions."""
        return extract_confidence_metrics(metadata, self.core_protein_scientific_contract)


def parse_thresholds(args, stage: str) -> Dict[str, tuple]:
    """Parse CLI args into threshold dict."""
    thresholds = {}
    
    if stage == 'backbone':
        if args.min_ss is not None or args.max_ss is not None:
            thresholds['total_ss'] = (args.min_ss, args.max_ss)
        if args.min_helices is not None or args.max_helices is not None:
            thresholds['helices'] = (args.min_helices, args.max_helices)
        if args.min_strands is not None or args.max_strands is not None:
            thresholds['strands'] = (args.min_strands, args.max_strands)
        if args.min_rog is not None or args.max_rog is not None:
            thresholds['rog'] = (args.min_rog, args.max_rog)
    
    elif stage == 'sequence':
        if args.max_score is not None:
            thresholds['score'] = (None, args.max_score)
        if args.max_psce is not None:
            thresholds['psce'] = (None, args.max_psce)
    
    elif stage == 'prediction':
        if args.min_plddt is not None:
            thresholds['plddt'] = (args.min_plddt, None)
        if args.min_ptm is not None:
            thresholds['ptm'] = (args.min_ptm, None)
        if args.max_pae is not None:
            thresholds['pae'] = (None, args.max_pae)
        if args.max_rmsd is not None:
            thresholds['rmsd'] = (None, args.max_rmsd)
        if args.max_rmsd_binder is not None:
            thresholds['rmsd_binder'] = (None, args.max_rmsd_binder)
    
    return thresholds


def _coerce_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def derive_ids(structure_path: Path, metadata: dict) -> Dict[str, Optional[Any]]:
    """
    Derive fold_id/seq_id/description from metadata or filename.
    """
    description = (
        metadata.get('description')
        or metadata.get('design')
        or metadata.get('name')
        or metadata.get('id')
    )
    if not description:
        description = structure_path.stem
        if description.endswith('.cif'):
            description = description[:-4]

    fold_id = _coerce_int(metadata.get('fold_id'))
    seq_id = _coerce_int(metadata.get('seq_id'))

    sources = [str(description), structure_path.name]
    if fold_id is None:
        for src in sources:
            match = re.search(r'fold[_-]?(\d+)', src)
            if not match:
                match = re.search(r'model[_-]?(\d+)', src)
            if match:
                fold_id = int(match.group(1))
                break

    if seq_id is None:
        for src in sources:
            match = re.search(r'seq[_-]?(\d+)', src)
            if not match:
                match = re.search(r'sample[_-]?(\d+)', src)
            if match:
                seq_id = int(match.group(1))
                break

    return {
        "description": description,
        "fold_id": fold_id,
        "seq_id": seq_id,
    }


def main():
    parser = argparse.ArgumentParser(
        description='Unified structure filtering for BioModStack'
    )
    subparsers = parser.add_subparsers(dest='stage', required=True)
    
    # Common arguments
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--core-protein-scientific-contract', type=int, choices=[1], default=None)
    common.add_argument('--input-dir', required=True, help='Input directory')
    common.add_argument('--output-dir', required=True, help='Output directory')
    common.add_argument('--convert-to-pdb', action='store_true',
                        help='Convert CIF outputs to PDB')
    common.add_argument('--output-jsonl', type=str, default='filtered.jsonl',
                        help='Output JSONL filename (default: filtered.jsonl)')
    common.add_argument('--ncpus', type=int, default=1,
                        help='Number of CPUs (for compatibility, not used)')
    
    # Backbone subparser (RFD/RFD3)
    backbone = subparsers.add_parser('backbone', parents=[common],
                                      help='Filter backbone structures')
    backbone.add_argument('--min-ss', type=int, help='Min total SS elements')
    backbone.add_argument('--max-ss', type=int, help='Max total SS elements')
    backbone.add_argument('--min-helices', type=int, help='Min helices')
    backbone.add_argument('--max-helices', type=int, help='Max helices')
    backbone.add_argument('--min-strands', type=int, help='Min strands')
    backbone.add_argument('--max-strands', type=int, help='Max strands')
    backbone.add_argument('--min-rog', type=float, help='Min RoG')
    backbone.add_argument('--max-rog', type=float, help='Max RoG')
    # Legacy compat args
    backbone.add_argument('--rfd-min-ss', type=int, dest='min_ss')
    backbone.add_argument('--rfd-max-ss', type=int, dest='max_ss')
    backbone.add_argument('--rfd-min-helices', type=int, dest='min_helices')
    backbone.add_argument('--rfd-max-helices', type=int, dest='max_helices')
    backbone.add_argument('--rfd-min-strands', type=int, dest='min_strands')
    backbone.add_argument('--rfd-max-strands', type=int, dest='max_strands')
    backbone.add_argument('--rfd-min-rog', type=float, dest='min_rog')
    backbone.add_argument('--rfd-max-rog', type=float, dest='max_rog')
    
    # Sequence subparser (MPNN/FAMPNN)
    sequence = subparsers.add_parser('sequence', parents=[common],
                                      help='Filter designed sequences')
    sequence.add_argument('--max-score', type=float, help='Max MPNN score')
    sequence.add_argument('--max-psce', type=float, help='Max FAMPNN PSCE')
    
    # Prediction subparser (AF2/Boltz/RF3)
    prediction = subparsers.add_parser('prediction', parents=[common],
                                        help='Filter structure predictions')
    prediction.add_argument('--min-plddt', type=float, help='Min pLDDT')
    prediction.add_argument('--min-ptm', type=float, help='Min pTM')
    prediction.add_argument('--max-pae', type=float, help='Max PAE')
    prediction.add_argument('--max-rmsd', type=float, help='Max overall RMSD')
    prediction.add_argument('--max-rmsd-binder', type=float, help='Max binder RMSD')
    # Legacy compat args
    prediction.add_argument('--rf3-min-plddt', type=float, dest='min_plddt')
    prediction.add_argument('--rf3-min-ptm', type=float, dest='min_ptm')
    prediction.add_argument('--rf3-max-pae', type=float, dest='max_pae')
    prediction.add_argument('--rf3-max-rmsd-overall', type=float, dest='max_rmsd')
    prediction.add_argument('--rf3-max-rmsd-binder', type=float, dest='max_rmsd_binder')
    
    # Stage receipts belong only to the existing RF3/RFD3 workflow filters.
    for stage_parser in (backbone, prediction):
        stage_parser.add_argument('--stage-receipt-dir')
        stage_parser.add_argument('--stage-id')
        stage_parser.add_argument('--job-id')
        stage_parser.add_argument('--task-id')
    args = parser.parse_args()
    receipt_dir = getattr(args, 'stage_receipt_dir', None)
    if receipt_dir and (args.core_protein_scientific_contract != 1 or not all(
        getattr(args, key, None) for key in ('stage_id', 'job_id', 'task_id')
    )):
        parser.error('Stage receipt requires revision 1 and explicit stage/job/task identity')
    
    # Parse thresholds
    thresholds = parse_thresholds(args, args.stage)
    
    # Select filter class
    filter_classes = {
        'backbone': BackboneFilter,
        'sequence': SequenceFilter,
        'prediction': PredictionFilter,
    }
    
    FilterClass = filter_classes[args.stage]
    
    # Run filter
    logger.info(f"Running {args.stage} filter")
    logger.info(f"Input: {args.input_dir}")
    logger.info(f"Output: {args.output_dir}")
    logger.info(f"Thresholds: {thresholds}")
    
    filter_instance = FilterClass(
        input_dir=Path(args.input_dir),
        output_dir=Path(args.output_dir),
        thresholds=thresholds,
        convert_to_pdb=args.convert_to_pdb,
        core_protein_scientific_contract=args.core_protein_scientific_contract,
    )
    
    if receipt_dir:
        from lib.filtering.stage_receipt import snapshot_inputs
        snapshot_inputs(filter_instance, Path(receipt_dir))
    results = filter_instance.run()
    
    # Write results JSONL to working directory (not output_dir) for Nextflow
    jsonl_path = Path(args.output_jsonl)
    with open(jsonl_path, 'w') as f:
        for r in results:
            structure_path = Path(r.get('file', ''))
            metadata_path = filter_instance.find_metadata_file(structure_path) if structure_path else None
            metadata = {} if args.core_protein_scientific_contract == 1 else (filter_instance.load_metadata(metadata_path) if metadata_path else {})
            ids = derive_ids(structure_path, metadata)

            if ids["fold_id"] is None:
                logger.warning(f"Missing fold_id for {structure_path.name}")

            record: Dict[str, Any] = {
                "description": ids["description"],
                "fold_id": ids["fold_id"],
                "seq_id": ids["seq_id"],
                "file": r.get("file"),
                "passed": r.get("passed"),
                "reason": r.get("reason"),
            }

            metrics = r.get("metrics") or {}
            if args.stage == 'backbone':
                record.update({
                    "rfd_helices": metrics.get("helices"),
                    "rfd_strands": metrics.get("strands"),
                    "rfd_total_ss": metrics.get("total_ss"),
                    "rfd_RoG": metrics.get("rog"),
                })
            elif args.stage == 'sequence':
                record.update({
                    "mpnn_score": metrics.get("score"),
                    "fampnn_psce": metrics.get("psce"),
                })
            elif args.stage == 'prediction':
                record.update({
                    "plddt": metrics.get("plddt"),
                    "ptm": metrics.get("ptm"),
                    "pae": metrics.get("pae"),
                    "rmsd": metrics.get("rmsd"),
                    "rmsd_binder": metrics.get("rmsd_binder"),
                })

            if args.core_protein_scientific_contract == 1:
                record.update({k: r.get(k) for k in ('core_protein_scientific_contract', 'candidate_id', 'disposition', 'criteria', 'source_sha256')})
                if 'descriptor_provenance' in r:
                    record['descriptor_provenance'] = r['descriptor_provenance']
                if 'candidate_failure' in r:
                    record['candidate_failure'] = r['candidate_failure']
            f.write(json.dumps(record, allow_nan=args.core_protein_scientific_contract != 1) + '\n')
    if receipt_dir:
        from lib.filtering.stage_receipt import publish_invocation
        publish_invocation(filter_instance, results, jsonl_path, Path(receipt_dir),
                           args.stage_id, args.job_id, args.task_id)
    logger.info(f"Results written to {jsonl_path}")
    
    # Exit with error if nothing passed
    passed = sum(1 for r in results if r['passed'])
    if passed == 0 and len(results) > 0:
        logger.warning("No structures passed filtering")


if __name__ == '__main__':
    main()
