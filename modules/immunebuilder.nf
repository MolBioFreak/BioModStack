process IMMUNEBUILDER {
    tag "${meta.id}"
    label 'process_medium'
    container 'apptainer/antibody_tools.sif'

    input:
    tuple val(meta), path(fasta)

    output:
    tuple val(meta), path("*.pdb"), emit: structure
    path "immunebuilder.log"

    script:
    """
    # Create robust python wrapper
    cat <<EOF > run_ib.py
    import sys
    import os
    from ImmuneBuilder import ABodyBuilder2
    from Bio import SeqIO
    
    fasta_file = "${fasta}"
    
    # Initialize predictor
    predictor = ABodyBuilder2()
    
    # Parse all sequences
    records = list(SeqIO.parse(fasta_file, "fasta"))
    
    # AntiFold outputs one sequence per design per chain? 
    # Or concatenated?
    # Standard format: If we provided a PDB with H and L, AntiFold redesigns them.
    # It usually outputs >description_chain_ID
    
    # We will group by ID prefix if possible, or process linearly.
    # For now, simplistic approach: process each record as a potential nanobody (VHH) 
    # OR if we find pairs, fold them.
    
    # HEURISTIC:
    # 1. Group by seed name?
    # 2. If 'H' and 'L' in header, pair them.
    
    grouped = {}
    
    for r in records:
        header = r.description
        seq = str(r.seq)
        
        # Try to parse H/L from header
        # Example AntiFold header: ">sample_0 chain_H"
        
        base_id = r.id
        chain_type = "H" # Default to Heavy/VHH
        
        if "chain_L" in header or "_L" in r.id or "Light" in header:
            chain_type = "L"
            base_id = r.id.replace("_L", "").replace("chain_L", "").strip("_ ")
        elif "chain_H" in header or "_H" in r.id or "Heavy" in header:
            chain_type = "H"
            base_id = r.id.replace("_H", "").replace("chain_H", "").strip("_ ")
            
        if base_id not in grouped:
            grouped[base_id] = {"H": "", "L": ""}
            
        grouped[base_id][chain_type] = seq

    print(f"Found {len(grouped)} design candidates to fold.")
    
    for base_id, chains in grouped.items():
        h_seq = chains["H"]
        l_seq = chains["L"]
        
        if not h_seq and not l_seq:
            continue
            
        print(f"Folding {base_id}...")
        try:
            # Predict structure
            # ABodyBuilder2 expects a dict with 'H' and 'L' keys
            sequences = {}
            if h_seq: sequences['H'] = h_seq
            if l_seq: sequences['L'] = l_seq
            
            out = predictor.predict(sequences)
            
            # Save PDB
            # Clean filename
            safe_id = base_id.replace("/", "_").replace(" ", "_")
            out_path = f"{safe_id}.pdb"
            out.save(out_path)
            print(f"Saved {out_path}")
            
        except Exception as e:
            print(f"Failed to fold {base_id}: {e}")

EOF
    
    python3 run_ib.py > immunebuilder.log 2>&1
    """
}
