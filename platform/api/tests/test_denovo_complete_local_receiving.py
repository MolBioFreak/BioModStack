"""Real producer/SQLite/Project receiving of inert partial RFD3 native evidence."""
import hashlib
import json
from pathlib import Path
import subprocess

import pytest
from sqlalchemy import event, select

from database import Job
from services.result_ingester import ingest_job_results
from tests.rfd3_native_fixture import write_native_result
from tests.test_core_protein_candidates import setup


@pytest.fixture(autouse=True)
def roots(tmp_path, monkeypatch):
    monkeypatch.setenv('BMS_DATA', str(tmp_path))
    monkeypatch.setenv('BMS_INPUTS', str(tmp_path / 'inputs'))
    monkeypatch.setenv('BMS_RESULTS_DIR', str(tmp_path / 'results'))










