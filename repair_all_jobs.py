
import asyncio
import os
import sys
from pathlib import Path

# Point to the correct DB used by the running service
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///platform/api/proteindj.db"

# Add platform/api to python path
sys.path.append(os.path.join(os.getcwd(), 'platform', 'api'))

from sqlalchemy import select, func
from database import async_session, Job, Design
from services.result_ingester import ingest_job_results
from schemas import JobStatus

async def main():
    print("Starting batch repair of job results...")
    
    async with async_session() as session:
        # potential candidates: completed jobs
        result = await session.execute(
            select(Job).where(Job.status == JobStatus.COMPLETED.value)
        )
        jobs = result.scalars().all()
        print(f"Found {len(jobs)} jobs in database.")
        
        repaired_count = 0
        total_ingested = 0
        
        for job in jobs:
            # Check design count
            design_count_result = await session.execute(
                select(func.count(Design.id)).where(Design.job_id == job.id)
            )
            count = design_count_result.scalar() or 0
            
            if count == 0:
                print(f"Checking job {job.name} ({job.id}) - Mode: {job.mode}, Model: {job.model_id}...")
                
                if not job.output_dir:
                    print(f"  Skipping: No output_dir")
                    continue
                    
                path = Path(job.output_dir)
                if not path.is_absolute():
                    # assuming relative to project root, which is where we are running from potentially?
                    # The ingester handles relative paths by prepending PROJECT_ROOT
                    pass
                
                try:
                    # Attempt ingestion
                    ingested = await ingest_job_results(job.id, job.output_dir, session)
                    if ingested > 0:
                        print(f"  SUCCESS: Ingested {ingested} designs.")
                        repaired_count += 1
                        total_ingested += ingested
                    else:
                        print(f"  No designs found to ingest.")
                except Exception as e:
                    print(f"  Error ingesting: {e}")
                    
        print(f"\nRepair complete. Fixed {repaired_count} jobs, total {total_ingested} designs restored.")

if __name__ == "__main__":
    asyncio.run(main())
