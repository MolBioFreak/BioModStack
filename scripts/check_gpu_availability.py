#!/usr/bin/env python3
"""
Check GPU availability for MSA computation.
Returns a free GPU ID or 'none' if all GPUs are busy.

Usage:
    python check_gpu_availability.py [--threshold 80]
    
Returns:
    Prints a discovered GPU ID or 'none' to stdout
"""
import subprocess
import sys
import argparse


def get_gpu_utilization():
    """Query nvidia-smi for GPU utilization percentages."""
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=index,utilization.gpu,memory.used,memory.total',
             '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return []
        
        gpus = []
        for line in result.stdout.strip().split('\n'):
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split(',')]
            if len(parts) >= 4:
                gpu_id = int(parts[0])
                utilization = int(parts[1])
                mem_used = int(parts[2])
                mem_total = int(parts[3])
                mem_percent = (mem_used / mem_total * 100) if mem_total > 0 else 100
                gpus.append({
                    'id': gpu_id,
                    'utilization': utilization,
                    'memory_percent': mem_percent
                })
        return gpus
    except Exception as e:
        print(f"Error querying GPUs: {e}", file=sys.stderr)
        return []


def find_free_gpu(threshold=80):
    """
    Find a GPU with utilization below threshold.
    
    Args:
        threshold: Max utilization percent to consider GPU 'free'
        
    Returns:
        GPU ID (int) or None if no free GPU
    """
    gpus = get_gpu_utilization()
    
    # Sort by utilization (prefer least busy)
    gpus.sort(key=lambda g: g['utilization'])
    
    for gpu in gpus:
        # Consider free if both compute and memory utilization are low
        if gpu['utilization'] < threshold and gpu['memory_percent'] < threshold:
            return gpu['id']
    
    return None


def main():
    parser = argparse.ArgumentParser(description="Check for free GPU")
    parser.add_argument('--threshold', type=int, default=80,
                        help="Max utilization %% to consider GPU free (default: 80)")
    args = parser.parse_args()
    
    free_gpu = find_free_gpu(args.threshold)
    
    if free_gpu is not None:
        print(free_gpu)
    else:
        print("none")


if __name__ == "__main__":
    main()
