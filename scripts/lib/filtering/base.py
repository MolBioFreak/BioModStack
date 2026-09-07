"""
Base class for structure filtering.

Provides common functionality for all filter types.
"""

import json
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import logging

logger = logging.getLogger(__name__)


class StructureFilter(ABC):
    """
    Base class for filtering protein structures.
    
    Subclasses implement stage-specific metric extraction.
    """
    
    def __init__(
        self,
        input_dir: Path,
        output_dir: Path,
        thresholds: Dict[str, Tuple[Optional[float], Optional[float]]] = None,
        convert_to_pdb: bool = False,
        core_protein_scientific_contract=None,
    ):
        """
        Initialize filter.
        
        Args:
            input_dir: Directory containing input structures
            output_dir: Directory for filtered outputs
            thresholds: Dict of metric_name -> (min_val, max_val)
            convert_to_pdb: If True, convert CIF outputs to PDB
        """
        self.core_protein_scientific_contract = core_protein_scientific_contract
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.thresholds = thresholds or {}
        self.convert_to_pdb = convert_to_pdb
        
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def find_structure_files(self) -> List[Path]:
        """Find all structure files in input directory."""
        patterns = ['*.pdb', '*.cif', '*.cif.gz']
        files = []
        for pattern in patterns:
            files.extend(self.input_dir.glob(pattern))
        return sorted(files)
    
    def find_metadata_file(self, structure_path: Path) -> Optional[Path]:
        """Find JSON metadata file corresponding to a structure."""
        stem = structure_path.stem
        # Handle .cif.gz -> remove both extensions
        if stem.endswith('.cif'):
            stem = stem[:-4]
        
        if self.core_protein_scientific_contract == 1:
            candidates = [structure_path.parent / f"{stem}_summary_confidences.json", structure_path.parent / f"{stem}.json"]
            return next((p for p in candidates if p.exists()), None)

        # Try various patterns
        candidates = [
            structure_path.with_suffix('.json'),
            self.input_dir / f"{stem}.json",
            structure_path.parent / f"{stem}.json",
        ]
        
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None
    
    def load_metadata(self, json_path: Path) -> dict:
        """Load metadata from JSON file."""
        try:
            if self.core_protein_scientific_contract == 1:
                import hashlib
                raw = json_path.read_bytes()
                self.source_sha256 = hashlib.sha256(raw).hexdigest()
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError("Metadata must be an object")
                return data
            with open(json_path) as f:
                return json.load(f)
        except Exception as e:
            if self.core_protein_scientific_contract == 1:
                raise
            logger.warning(f"Could not load metadata {json_path}: {e}")
            return {}
    
    def evaluate_thresholds(self, metrics, candidate_id):
        from .evidence import evaluate
        return evaluate([(name, *bounds) for name, bounds in self.thresholds.items()], metrics, candidate_id, plddt_units=metrics.get("plddt_units"))

    def check_thresholds(self, metrics: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """
        Check if metrics pass all thresholds.
        
        Args:
            metrics: Dict of metric values
            
        Returns:
            Tuple of (passed, failure_reason)
        """
        if self.core_protein_scientific_contract == 1:
            result = self.evaluate_thresholds(metrics, "")
            passed = result["disposition"] == "passed"
            return passed, None if passed else result["disposition"]
        for metric_name, (min_val, max_val) in self.thresholds.items():
            value = metrics.get(metric_name)
            
            if value is None:
                continue  # Skip missing metrics
            
            if min_val is not None and value < min_val:
                return False, f"{metric_name}={value:.2f} < min={min_val}"
            
            if max_val is not None and value > max_val:
                return False, f"{metric_name}={value:.2f} > max={max_val}"
        
        return True, None
    
    def copy_passing_files(
        self,
        structure_path: Path,
        metadata_path: Optional[Path] = None
    ) -> Path:
        """
        Copy passing structure (and metadata) to output directory.
        
        Optionally converts CIF to PDB.
        
        Returns:
            Path to output structure file
        """
        if self.convert_to_pdb and structure_path.suffix in ['.cif', '.gz']:
            # Convert to PDB
            from .converters import cif_to_pdb
            stem = structure_path.stem
            if stem.endswith('.cif'):
                stem = stem[:-4]
            output_path = self.output_dir / f"{stem}.pdb"
            cif_to_pdb(structure_path, output_path)
        else:
            # Direct copy
            output_path = self.output_dir / structure_path.name
            shutil.copy2(structure_path, output_path)
        
        # Copy metadata if present
        if metadata_path and metadata_path.exists():
            shutil.copy2(metadata_path, self.output_dir / metadata_path.name)
        
        return output_path
    
    @abstractmethod
    def extract_metrics(self, structure_path: Path, metadata: dict) -> Dict[str, Any]:
        """
        Extract metrics from a structure file.
        
        Subclasses implement stage-specific extraction.
        
        Args:
            structure_path: Path to structure file
            metadata: Dict from JSON metadata
            
        Returns:
            Dict of metric values
        """
        pass
    
    def run(self) -> List[dict]:
        """
        Run the filter on all structures.
        
        Returns:
            List of result dicts with pass/fail status
        """
        results = []
        structures = self.find_structure_files()
        
        logger.info(f"Found {len(structures)} structures to filter")
        
        for structure_path in structures:
            result = {
                'file': str(structure_path),
                'passed': True,
                'reason': None,
                'metrics': {}
            }
            
            try:
                # Load metadata
                metadata_path = self.find_metadata_file(structure_path)
                metadata = self.load_metadata(metadata_path) if metadata_path else {}
                if self.core_protein_scientific_contract == 1:
                    identity = structure_path.name.removesuffix('.gz').removesuffix('.cif').removesuffix('.pdb')
                    if metadata.get('design_id') is not None and metadata['design_id'] != identity:
                        raise ValueError('foreign_metadata_identity')
                    result['source_sha256'] = self.source_sha256 if metadata_path else None
                
                # Extract metrics
                metrics = self.extract_metrics(structure_path, metadata)
                if self.core_protein_scientific_contract == 1:
                    provenance = metrics.pop('_descriptor_provenance', None)
                    if provenance is not None:
                        result['descriptor_provenance'] = provenance
                result['metrics'] = metrics
                if self.core_protein_scientific_contract == 1:
                    result.update(self.evaluate_thresholds(metrics, structure_path.name))
                
                # Check thresholds
                passed, reason = self.check_thresholds(metrics)
                result['passed'] = passed
                result['reason'] = reason
                
                if passed:
                    published_path = self.copy_passing_files(structure_path, metadata_path)
                    if self.core_protein_scientific_contract == 1:
                        result['published_file'] = str(published_path)
                    logger.info(f"PASS: {structure_path.name}")
                else:
                    logger.info(f"FAIL: {structure_path.name} - {reason}")
                    
            except Exception as e:
                result['passed'] = False
                result['reason'] = str(e)
                if self.core_protein_scientific_contract == 1:
                    result.update(core_protein_scientific_contract=1, candidate_id=structure_path.name, disposition='invalid_evidence')
                    result['candidate_failure'] = {'code': 'candidate_evidence_failure', 'detail': str(e) or type(e).__name__}
                    failure = self.evaluate_thresholds({}, structure_path.name)
                    for criterion in failure['criteria']:
                        criterion['disposition'] = 'invalid_evidence'
                        criterion['evidence'].update(state='invalid', value=None, reason_code='candidate_evidence_failure')
                    result['criteria'] = failure['criteria']
                logger.error(f"ERROR: {structure_path.name} - {e}")
            
            if self.core_protein_scientific_contract == 1:
                from .evidence import metric_evidence
                result['metrics'] = {k: metric_evidence(k, v, result['metrics'].get('plddt_units'))['value'] for k, v in result['metrics'].items()}
            results.append(result)
        
        # Summary
        passed = sum(1 for r in results if r['passed'])
        logger.info(f"Filtering complete: {passed}/{len(results)} passed")
        
        return results
    
    def write_results(self, results: List[dict], output_file: str = 'filtered.jsonl'):
        """Write results to JSONL file."""
        output_path = self.output_dir / output_file
        with open(output_path, 'w') as f:
            for r in results:
                f.write(json.dumps(r, allow_nan=self.core_protein_scientific_contract != 1) + '\n')
