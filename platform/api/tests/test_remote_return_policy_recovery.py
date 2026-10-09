"""Persisted return authorization and crash recovery; no provider/science calls."""
import asyncio
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks

from database import Job, ExecutionTarget
from services.remote_execution import executor as ex
from test_remote_lifecycle_gaps import store
from test_remote_manual_result_pull import ready, success
from test_remote_result_generation import package


async def policy(store, value):
    async with store() as session:
        job = await session.get(Job, "job")
        job.params = dict(job.params or {}, remote_result_policy=value)
        await session.commit()
