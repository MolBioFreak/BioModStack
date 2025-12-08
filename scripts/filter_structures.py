#!/usr/bin/env python3
"""
Unified structure filtering script for ProteinDJ.

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
        structure = load_structure(structure_path)
        if structure is None:
            return {}
        
        metrics = calculate_backbone_metrics(structure)
        # Add any metrics from JSON metadata
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
        return extract_confidence_metrics(metadata)


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


def main():
    parser = argparse.ArgumentParser(
        description='Unified structure filtering for ProteinDJ'
    )
    subparsers = parser.add_subparsers(dest='stage', required=True)
    
    # Common arguments
    common = argparse.ArgumentParser(add_help=False)
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
    
    args = parser.parse_args()
    
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
    )
    
    results = filter_instance.run()
    
    # Write results JSONL to working directory (not output_dir) for Nextflow
    jsonl_path = Path(args.output_jsonl)
    with open(jsonl_path, 'w') as f:
        for r in results:
            f.write(json.dumps(r) + '\n')
    logger.info(f"Results written to {jsonl_path}")
    
    # Exit with error if nothing passed
    passed = sum(1 for r in results if r['passed'])
    if passed == 0 and len(results) > 0:
        logger.warning("No structures passed filtering")


if __name__ == '__main__':
    main()
