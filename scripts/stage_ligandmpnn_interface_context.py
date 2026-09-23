#!/usr/bin/env python3
"""Materialize one selected LigandMPNN context request without changing its science.

The parent owns selection/round identity. The invocation owns only private staged
paths; the original request and source are never rewritten in place.
"""
import argparse
import json
from pathlib import Path

from run_ligandmpnn_interface_context import digest, validate


def stage(request_path: Path, source_path: Path, output_path: Path) -> dict:
    request = json.loads(request_path.read_text())
    if digest(source_path) != request.get('source_sha256'):
        raise ValueError('selected source digest does not match request')
    effective = {**request, 'structure_path': str(source_path.resolve())}
    validate(effective)
    output_path.write_text(json.dumps(effective, indent=2) + '\n')
    return effective


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request')
    parser.add_argument('source')
    parser.add_argument('output')
    args = parser.parse_args()
    stage(Path(args.request), Path(args.source), Path(args.output))


if __name__ == '__main__':
    main()
