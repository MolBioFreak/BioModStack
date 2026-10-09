"""Real SQLite acceptance for the supported first-install migration lifecycle."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

API = Path(__file__).resolve().parents[1]


def environment(tmp_path):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("BMS_") and key != "DATABASE_URL"}
    env.update(HOME=str(tmp_path), BMS_HOME=str(API.parents[1]),
               BMS_DATA=str(tmp_path), BMS_DB_PATH=str(tmp_path / "core.db"))
    return env


def run(tmp_path, code=None):
    args = [sys.executable, "-c", code] if code else [sys.executable, "run_migrations.py"]
    return subprocess.run(args, cwd=API, env=environment(tmp_path),
                          capture_output=True, text=True, timeout=90)


ATTEST = '''
import asyncio
from sqlalchemy import select
from database import Base, engine, init_db
async def check():
    await init_db()
    async with engine.connect() as conn:
        for table in Base.metadata.tables.values():
            await conn.execute(select(table).limit(1))
    await engine.dispose()
asyncio.run(check())
'''
