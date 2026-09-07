"""Record real pytest selection; loaded only by the acceptance driver."""
import json
import os
from pathlib import Path


def pytest_collection_finish(session):
    destination = Path(os.environ['BMS_SCIENTIFIC_INVENTORY'])
    destination.write_text(json.dumps([item.nodeid for item in session.items], indent=2) + '\n')
