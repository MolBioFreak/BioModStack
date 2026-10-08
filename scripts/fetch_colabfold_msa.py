#!/usr/bin/env python3
"""
Fetch MSA from ColabFold API.
Based on the official ColabFold client implementation.
"""
import time
import requests
import argparse
import sys
import os
import tarfile

def run_mmseqs2(sequence, job_name, out_dir):
    host_url = "https://api.colabfold.com"
    
    # 1. Submit job
    print(f"Submitting sequence to ColabFold API...", flush=True)
    query = f">1\n{sequence}"
    
    error_count = 0
    while True:
        try:
            res = requests.post(f'{host_url}/ticket/msa', data={'q': query, 'mode': 'env'}, timeout=6.02)
            res.raise_for_status()
            out = res.json()
            break
        except requests.exceptions.Timeout:
            print("Timeout while submitting. Retrying...", flush=True)
            continue
        except Exception as e:
            error_count += 1
            print(f"Error submitting ({error_count}/5): {e}", flush=True)
            time.sleep(5)
            if error_count >= 5:
                raise
            continue
    
    job_id = out.get('id')
    status = out.get('status', 'PENDING')
    print(f"Job ID: {job_id}, Status: {status}", flush=True)
    
    # 2. Poll status (only if not already complete)
    while status not in ['COMPLETE', 'ERROR']:
        time.sleep(5)
        error_count = 0
        while True:
            try:
                res = requests.get(f'{host_url}/ticket/{job_id}', timeout=6.02)
                res.raise_for_status()
                out = res.json()
                break
            except requests.exceptions.Timeout:
                print("Timeout while polling. Retrying...", flush=True)
                continue
            except Exception as e:
                error_count += 1
                print(f"Polling error ({error_count}/5): {e}", flush=True)
                time.sleep(5)
                if error_count > 5:
                    raise
                continue
        
        status = out.get('status', 'ERROR')
        print(f"Status: {status}", flush=True)
    
    if status == 'ERROR':
        print("Error from ColabFold server.", flush=True)
        sys.exit(1)
    
    print("MSA generation complete.", flush=True)
    
    # 3. Download results
    download_url = f'{host_url}/result/download/{job_id}'
    print(f"Downloading from {download_url}...", flush=True)
    
    error_count = 0
    while True:
        try:
            res = requests.get(download_url, timeout=60)
            res.raise_for_status()
            break
        except requests.exceptions.Timeout:
            print("Timeout while downloading. Retrying...", flush=True)
            continue
        except Exception as e:
            error_count += 1
            print(f"Download error ({error_count}/5): {e}", flush=True)
            time.sleep(5)
            if error_count > 5:
                raise
            continue
    
    # Save tar.gz
    tar_path = os.path.join(out_dir, f"{job_name}.tar.gz")
    with open(tar_path, "wb") as f:
        f.write(res.content)
    print(f"Saved to {tar_path}", flush=True)
    
    # Extract a3m
    with tarfile.open(tar_path, "r:gz") as tar:
        a3m_members = [m for m in tar.getmembers() if m.name.endswith(".a3m")]
        if not a3m_members:
            print("No A3M file found in archive.", flush=True)
            sys.exit(1)
        
        f = tar.extractfile(a3m_members[0])
        # Strip null bytes that may be present from tar padding
        a3m_content = f.read().decode("utf-8").rstrip('\x00')
        
        final_a3m = os.path.join(out_dir, f"{job_name}.a3m")
        with open(final_a3m, "w") as out:
            out.write(a3m_content)
        
        print(f"Extracted A3M to {final_a3m}", flush=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sequence", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--out_dir", required=True)
    args = parser.parse_args()
    
    os.makedirs(args.out_dir, exist_ok=True)
    run_mmseqs2(args.sequence, args.name, args.out_dir)
