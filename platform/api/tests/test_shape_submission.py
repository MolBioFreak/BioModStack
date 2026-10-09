from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker


CUBE_OBJ = b"""\
v -1 -1 -1
v 1 -1 -1
v 1 1 -1
v -1 1 -1
v -1 -1 1
v 1 -1 1
v 1 1 1
v -1 1 1
f 1 3 2
f 1 4 3
f 5 6 7
f 5 7 8
f 1 2 6
f 1 6 5
f 2 3 7
f 2 7 6
f 3 4 8
f 3 8 7
f 4 1 5
f 4 5 8
"""


async def _database(tmp_path: Path):
    database = importlib.import_module("database")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'shape-request.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(database.Base.metadata.create_all)
    return database, engine, sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
