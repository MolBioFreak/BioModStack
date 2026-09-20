"""Isolated tests: no API database, host paths, or scientific tool substitution."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
