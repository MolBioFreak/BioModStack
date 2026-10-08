
import asyncio
import os
import sys
from pathlib import Path

# Point to the correct DB used by the running service
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///platform/api/biomodstack.db"

# Add platform/api to python path
sys.path.append(os.path.join(os.getcwd(), 'platform', 'api'))

from database import async_session
from services.result_ingester import ingest_job_results

async def main():
    job_id = "0fb2cf0d-446e-4bf9-9831-f7d5d9ea88b6"
    output_dir = "pdj_results/poopoo_20251209_194654"
    
    print(f"Re-ingesting job {job_id} from {output_dir}")
    
    async with async_session() as session:
        count = await ingest_job_results(job_id, output_dir, session)
        print(f"Total designs ingested: {count}")

if __name__ == "__main__":
    asyncio.run(main())
