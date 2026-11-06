#!/usr/bin/env python3

import os
import json
import argparse
from pathlib import Path
import logging
import numpy as np
import re

def setup_logging():
    """Set up logging configuration"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('mpnn_prep.log')
        ]
    )
    return logging.getLogger()

def read_json_file(json_path):
    """Read JSON file and return data"""
    with open(json_path, 'r') as f:
        return json.load(f)

def get_fixed_residues(pdb_path, json_path=None):
    """
    Determine which residues should be fixed based on JSON metadata or B-factors
    Returns a list of tuples (chain, residue_number)
    """
    logger = logging.getLogger()
    fixed_residues = []
    
    # Try to get fixed residues from JSON first
    if json_path and os.path.exists(json_path):
        logger.info(f"Reading JSON file: {json_path}")
        try:
            json_data = read_json_file(json_path)
            
            # Preserve native mask polarity: True means fixed
            if 'rfd_inpaint_seq' in json_data:
                inpaint_array = json_data['rfd_inpaint_seq']
                logger.info(f"Found rfd_inpaint_seq with {len(inpaint_array)} elements")
                
                # Map inpaint_seq to PDB residues
                fixed_residues = map_inpaint_to_residues(pdb_path, inpaint_array)
                return fixed_residues
            else:
                logger.warning(f"No rfd_inpaint_seq found in JSON")
        except Exception as e:
            logger.error(f"Error processing JSON: {e}")
    
    # Fallback to B-factor method
    logger.info(f"Using B-factor method for {os.path.basename(pdb_path)}")
    return get_fixed_from_bfactor(pdb_path)

def map_inpaint_to_residues(pdb_path, inpaint_array):
    """Map inpaint_seq array to PDB residue numbers"""
    logger = logging.getLogger()
    
    # Get ordered list of residues from PDB
    residues = []
    current_res = None
    
    with open(pdb_path, 'r') as f:
        for line in f:
            if line.startswith('ATOM'):
                chain = line[21]
                res_num = int(line[22:26].strip())
                res_id = (chain, res_num, line[26].strip())
                
                if res_id != current_res:
                    residues.append(res_id)
                    current_res = res_id
    
    # Find residues to keep fixed (where inpaint_seq is true)
    fixed_residues = []
    for i, res_id in enumerate(residues):
        if i < len(inpaint_array) and inpaint_array[i]:
            fixed_residues.append(res_id)
    
    logger.info(f"Identified {len(fixed_residues)} fixed residues from JSON data")
    return fixed_residues

def get_fixed_from_bfactor(pdb_path):
    """Identify fixed residues using B-factor values (1.00 indicates fixed)"""
    logger = logging.getLogger()
    
    residues = {}
    
    with open(pdb_path, 'r') as f:
        for line in f:
            if line.startswith('ATOM'):
                chain = line[21]
                res_num = int(line[22:26].strip())
                b_factor = float(line[60:66].strip())
                
                key = (chain, res_num, line[26].strip())
                if key not in residues:
                    residues[key] = []
                residues[key].append(b_factor)
    
    # Residues with all atoms having B-factor of 1.00 are fixed
    fixed_residues = []
    for res_id, b_factors in residues.items():
        if all(b == 1.0 for b in b_factors):
            fixed_residues.append(res_id)
    
    logger.info(f"Identified {len(fixed_residues)} fixed residues using B-factor method")
    return fixed_residues

def modify_pdb_file(input_pdb, output_pdb, fixed_residues):
    """Add FIXED remarks to PDB file"""
    logger = logging.getLogger()
    
    with open(input_pdb, 'r') as in_file, open(output_pdb, 'w') as out_file:
        # Write FIXED remarks at the top of the file
        identities = list(dict.fromkeys(
            (line[21], int(line[22:26]), line[26].strip())
            for line in Path(input_pdb).read_text().splitlines() if line.startswith('ATOM')))
        fixed_residues = set(fixed_residues)
        for line in Path(input_pdb).read_text().splitlines():
            match = re.fullmatch(r'REMARK\s+PDBinfo-LABEL:\s*(\d+)\s+FIXED\s*', line)
            if match and 1 <= int(match.group(1)) <= len(identities):
                fixed_residues.add(identities[int(match.group(1)) - 1])
        fixed_residues = [r for r in identities if r in fixed_residues]
        out_file.write('REMARK BMS_MPNN_FIXED ' + json.dumps([list(r) for r in fixed_residues]) + '\n')
        for identity in fixed_residues:
            out_file.write(f"REMARK PDBinfo-LABEL: {identities.index(identity) + 1} FIXED\n")
        
        # Write the rest of the PDB content
        for line in in_file:
            out_file.write(line)
    
    logger.info(f"Added {len(fixed_residues)} FIXED remarks to {os.path.basename(output_pdb)}")

def validate_generic_input(pdb_path, request=None):
    """Use the same explicit author-role contract at admission and preparation."""
    if __package__:
        from .proteinmpnn_native_binding import role_contract, pdb_domain
    else:
        from proteinmpnn_native_binding import role_contract, pdb_domain
    pdb_path = Path(pdb_path)
    data = pdb_path.read_bytes()
    role_contract(pdb_path, request, data=data)
    return pdb_domain(pdb_path, text=data.decode())


def process_files(input_dir, out_dir, generic=False, request=None):
    """Process all PDB files in the input directory"""
    logger = logging.getLogger()
    
    # Create output directory
    os.makedirs(out_dir, exist_ok=True)
    
    # Find all PDB files
    pdb_files = list(Path(input_dir).glob('*.pdb'))
    logger.info(f"Found {len(pdb_files)} PDB files to process")
    
    total_fixed = 0
    for pdb_path in pdb_files:
        if generic:
            if __package__:
                from .proteinmpnn_native_binding import prepare
            else:
                from proteinmpnn_native_binding import prepare
            prepare(pdb_path, Path(out_dir) / pdb_path.name, request)
            continue
        # Look for corresponding JSON file
        json_path = pdb_path.with_suffix('.json')
        if not json_path.exists():
            logger.warning(f"No JSON file found for {pdb_path.name}")
            json_path = None
        
        # Get fixed residues
        fixed_residues = get_fixed_residues(pdb_path, json_path)
        total_fixed += len(fixed_residues)
        
        # Create modified PDB file
        output_path = Path(out_dir) / pdb_path.name
        modify_pdb_file(pdb_path, output_path, fixed_residues)
    
    logger.info(f"Processing complete. Added FIXED remarks for {total_fixed} residues across {len(pdb_files)} files.")

def _retain_closed_result(source, destination):
    """Retain a completed native leaf; replace names, never shared bytes."""
    import errno
    import os
    import tempfile
    import shutil
    source, destination = Path(source), Path(destination)
    fd, name = tempfile.mkstemp(prefix=".result-", dir=destination.parent)
    os.close(fd)
    temporary = Path(name)
    temporary.unlink()
    try:
        try:
            os.link(source.resolve(strict=True), temporary)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            shutil.copyfile(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def canonical_results(input_dir, out_dir):
    """Bind native metrics by exact payload.design, retaining their bytes.

    Existing consumers disagree about JSON filename prefixes. Never guess
    identity from a prefix or a substring. The caller
    retains the entire original output directory separately, including JSON
    that is not per-candidate sequence metadata.
    """
    import shutil
    source, output = Path(input_dir), Path(out_dir)
    pdbs = {p.stem: p for p in source.glob('*.pdb')}
    if not pdbs:
        raise ValueError('native ProteinMPNN produced no structures')
    metadata = {}
    for path in source.glob('*.json'):
        payload = json.loads(path.read_text())
        if not isinstance(payload, dict) or not {'design', 'sequence', 'score'} <= payload.keys():
            continue
        name = payload['design']
        if not isinstance(name, str) or name not in pdbs:
            raise ValueError('native ProteinMPNN metadata names an absent structure')
        if name in metadata:
            raise ValueError('ambiguous native ProteinMPNN candidate metadata')
        metadata[name] = path
    if metadata.keys() != pdbs.keys():
        raise ValueError('native ProteinMPNN structure/metadata membership mismatch')
    output.mkdir(parents=True, exist_ok=True)
    for name, pdb in pdbs.items():
        _retain_closed_result(pdb, output / pdb.name)
        shutil.copyfile(metadata[name], output / (name + '.json'))


def main():
    logger = setup_logging()
    
    parser = argparse.ArgumentParser(description='Prepare PDB files for ProteinMPNN by adding FIXED remarks')
    parser.add_argument('--input_dir', required=True, help='Directory containing PDB and JSON files')
    parser.add_argument('--canonical_results', action='store_true', help='Copy exact payload-bound native PDB/JSON candidate pairs')
    parser.add_argument('--generic', action='store_true', help='Preserve only explicit native FIXED authority and all input bytes')
    parser.add_argument('--request_base64', help='Model-owned chain/residue role settings')
    parser.add_argument('--out_dir', default='mpnn_input', help='Output directory for modified PDB files')
    
    args = parser.parse_args()
    
    logger.info(f"Starting prep_mpnn_designs.py")
    logger.info(f"Input directory: {args.input_dir}")
    logger.info(f"Output directory: {args.out_dir}")
    
    if args.canonical_results:
        canonical_results(args.input_dir, args.out_dir)
    else:
        import base64
        request = json.loads(base64.b64decode(args.request_base64, validate=True)) if args.request_base64 is not None else None
        process_files(args.input_dir, args.out_dir, generic=args.generic, request=request)
    
    logger.info("Script completed successfully")

if __name__ == "__main__":
    main()
