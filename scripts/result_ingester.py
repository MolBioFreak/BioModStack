#!/usr/bin/env python3
"""
Result Ingester CLI - Command-line wrapper for result ingestion.

Used by Nextflow workflows to trigger result ingestion from completed jobs.
Wraps the async service in platform/api/services/result_ingester.py.
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Add platform/api to path for imports
API_DIR = Path(__file__).parent.parent / "platform" / "api"
sys.path.insert(0, str(API_DIR))


async def main():
    parser = argparse.ArgumentParser(description="Ingest job results into database")
    parser.add_argument("--job_id", required=True, help="Job ID to associate designs with")
    parser.add_argument("--results_dir", required=True, help="Path to job output directory")
    parser.add_argument("--api_url", default="http://localhost:8000", help="API base URL (unused, for compat)")
    parser.add_argument("--epitope_residues", default=None, help="Comma-separated epitope residues (e.g., A111,A112)")
    args = parser.parse_args()
    
    # Import after path setup
    from database import async_session
    from services.result_ingester import ingest_job_results
    
    # Parse epitope residues if provided
    epitope_residues = None
    if args.epitope_residues:
        epitope_residues = [r.strip() for r in args.epitope_residues.split(",")]
    
    print(f"[Ingester CLI] Ingesting results for job {args.job_id}")
    print(f"[Ingester CLI] Results directory: {args.results_dir}")
    
    async with async_session() as session:
        try:
            count = await ingest_job_results(
                job_id=args.job_id,
                output_dir=args.results_dir,
                session=session,
                epitope_residues=epitope_residues
            )
            print(f"[Ingester CLI] Successfully ingested {count} designs")
        except Exception as e:
            print(f"[Ingester CLI] Error during ingestion: {e}")
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
