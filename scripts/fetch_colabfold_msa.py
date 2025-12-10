#!/usr/bin/env python3
import time
import requests
import argparse
import sys
import os

def run_mmseqs2(sequence, job_name, out_dir):
    base_url = "https://api.colabfold.com/ticket/msa"
    
    # 1. Submit job
    print(f"Submitting sequence to ColabFold API...")
    try:
        res = requests.post(base_url, json={"q": f">1\n{sequence}", "mode": "env"})
        res.raise_for_status()
        out = res.json()
        job_id = out['id']
        print(f"Job submitted. ID: {job_id}")
    except Exception as e:
        print(f"Error submitting job: {e}")
        sys.exit(1)

    # 2. Poll status
    while True:
        try:
            time.sleep(5)
            status_res = requests.get(f"{base_url}/{job_id}")
            status_res.raise_for_status()
            status = status_res.json()
            
            if status['status'] == "COMPLETE":
                print("MSA generation complete.")
                break
            elif status['status'] == "ERROR":
                print("Error from ColabFold server.")
                sys.exit(1)
            elif status['status'] == "RUNNING" or status['status'] == "PENDING":
                print(f"Status: {status['status']}...")
            
        except Exception as e:
            print(f"Polling error: {e}")
            time.sleep(10)

    # 3. Download A3M
    # The API returns 'result' which is a list of objects. We want the a3m.
    # Actually, verify the response format.
    # Typically ColabFold API returns the result content directly or a list of MSAs.
    # Let's inspect the 'result' field.
    # For now, I'll attempt to construct the download URL or parse the result.
    
    # Standard ColabFold API returns "result" list in the status JSON if complete?
    # Or enables a download endpoint?
    # Actually, the 'result' key in the status json contains the MSA for single-sequence jobs or a list.
    
    # Let's try downloading from https://api.colabfold.com/result/download/{job_id}
    download_url = f"https://api.colabfold.com/result/download/{job_id}"
    print(f"Downloading results from {download_url}...")
    
    try:
        dl_res = requests.get(download_url)
        dl_res.raise_for_status()
        
        # Save as tar.gz
        tar_path = os.path.join(out_dir, f"{job_name}.tar.gz")
        with open(tar_path, "wb") as f:
            f.write(dl_res.content)
            
        print(f"Saved results to {tar_path}")
        
        # Extract A3M (we need to be careful, it might contain multiple files)
        # We'll just extract the a3m file.
        import tarfile
        with tarfile.open(tar_path, "r:gz") as tar:
            # Find the a3m file
            a3m_members = [m for m in tar.getmembers() if m.name.endswith(".a3m")]
            if not a3m_members:
                print("No A3M file found in archive.")
                sys.exit(1)
                
            # Extract the first one
            f = tar.extractfile(a3m_members[0])
            a3m_content = f.read().decode("utf-8")
            
            # Save to final path
            final_a3m = os.path.join(out_dir, f"{job_name}.a3m")
            with open(final_a3m, "w") as out:
                out.write(a3m_content)
                
            print(f"Extracted A3M to {final_a3m}")
            
    except Exception as e:
        print(f"Download/Extract error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sequence", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--out_dir", required=True)
    args = parser.parse_args()
    
    os.makedirs(args.out_dir, exist_ok=True)
    run_mmseqs2(args.sequence, args.name, args.out_dir)
