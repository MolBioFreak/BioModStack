#!/usr/bin/env python3
"""Publish remote parent source + canonical bundle synchronously, without HTTP."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path

from publish_frustrampnn_bundle import _publish_source, publish
from services.frustrampnn.manifests import load_result_manifest, validate_result_manifest


def publish_remote(*, source_bundle, allowed_root, destination, marker):
    if os.environ.get('BMS_REMOTE_EXECUTION') != '1':
        raise ValueError('trusted remote execution envelope required')
    payloads = validate_result_manifest(source_bundle, load_result_manifest(source_bundle))
    request = json.loads(payloads['workflow_component_request_v3.json'])
    source = payloads['normalized_input.pdb']
    if request['source_artifact']['sha256'] != hashlib.sha256(source).hexdigest():
        raise ValueError('remote parent source must bind canonical normalized PDB bytes')
    _publish_source(payload=source, allowed_root=allowed_root,
                    relative_path=request['source_artifact']['relative_path'])
    return publish(source_bundle=source_bundle, allowed_root=allowed_root,
                   destination=destination, marker=marker)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source-bundle', 'allowed-root', 'destination', 'marker'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    publish_remote(**vars(args))


if __name__ == '__main__':
    main()
