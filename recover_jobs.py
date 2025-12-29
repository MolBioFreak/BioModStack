
import asyncio
import os
import sys
from pathlib import Path
from datetime import datetime

# Point to the database
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///platform/api/biomodstack.db"

# Add api to path
sys.path.append(os.path.join(os.getcwd(), 'platform', 'api'))

from sqlalchemy import select, func
from database import async_session, Job, init_db
from schemas import JobStatus
from services.result_ingester import ingest_job_results
import uuid

async def recover():
    print("Starting job recovery from disk...")
    # Ensure DB tables exist
    await init_db()
    
    results_dir = Path("pdj_results").absolute()
    if not results_dir.exists():
        print("pdj_results directory not found!")
        return

    async with async_session() as session:
        # Get existing job output dirs to avoid duplicates
        existing_result = await session.execute(select(Job.output_dir))
        existing_dirs = {d for d in existing_result.scalars().all() if d}
        
        folders = [f for f in results_dir.iterdir() if f.is_dir()]
        print(f"Found {len(folders)} folders in pdj_results.")
        
        recovered_count = 0
        
        for folder in folders:
            if str(folder) in existing_dirs:
                continue
                
            print(f"Recovering {folder.name}...")
            
            # Simple heuristic for name/timestamp from folder name
            # format: jobname_YYYYMMDD_HHMMSS
            parts = folder.name.split('_')
            
            # Default values
            job_name = folder.name
            created_at = datetime.now()
            
            # Try to parse timestamp from the last two parts
            if len(parts) >= 2:
                try:
                    ts_str = parts[-2] + parts[-1] # YYYYMMDDHHMMSS
                    # Basic check if it looks like timestamp
                    if len(ts_str) >= 12 and ts_str.isdigit():
                         # We won't rigorously parse it, just rely on file mtime if needed, 
                         # or just use current time if parsing fails.
                         # Let's use file mtime as it's more reliable
                         stat = folder.stat()
                         created_at = datetime.fromtimestamp(stat.st_mtime)
                except:
                    pass
            
            # Identify model from params.json if exists, else guess
            model_id = "unknown"
            mode = "unknown"
            
            params_file = folder / "inputs" / "params.json"
            if params_file.exists():
                # We could parse this, but for now let's just default to 'recovered'
                # to get it in the UI. 
                # Actually, reading it is better.
                try:
                    import json
                    with open(params_file) as f:
                        params = json.load(f)
                        model_id = params.get('model_id', 'unknown')
                        mode = params.get('mode', 'inference')
                except:
                    pass
            
            # Create Job Record
            new_job = Job(
                id=str(uuid.uuid4()),
                name=job_name,
                status=JobStatus.COMPLETED.value,
                model_id=model_id,
                mode=mode,
                params={}, # Keep empty for now
                created_at=created_at,
                output_dir=str(folder)
            )
            
            session.add(new_job)
            await session.commit()
            
            # Ingest Results
            try:
                count = await ingest_job_results(new_job.id, str(folder), session)
                print(f"  -> Ingested {count} designs.")
                recovered_count += 1
            except Exception as e:
                print(f"  -> Failed ingestion: {e}")
        
        print(f"Recovery complete. Recovered {recovered_count} jobs.")

if __name__ == "__main__":
    asyncio.run(recover())
