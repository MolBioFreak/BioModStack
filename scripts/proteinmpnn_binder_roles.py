#!/usr/bin/env python3
"""Compatibility entrypoint; author-role transport has one implementation."""
import argparse
import base64
import json
from pathlib import Path
import sys

if __package__:
    from . import proteinmpnn_native_binding as binding
else:
    import proteinmpnn_native_binding as binding


def run(native_script, request, argv):
    entry = Path(native_script).resolve()
    sys.path.insert(0, str(entry.parent))
    sys.argv = [str(entry), *argv]
    exec(compile(binding.instrument_source(entry.read_text()), str(entry), 'exec'),
         {'__name__': '__main__', '__file__': str(entry),
          '_bms_install': lambda namespace: binding.install(namespace, request)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-script', default='/dl_binder_design/mpnn_fr/dl_interface_design_multi.py')
    parser.add_argument('--request-base64', required=True)
    parser.add_argument('native_args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    argv = args.native_args[1:] if args.native_args[:1] == ['--'] else args.native_args
    run(args.native_script, json.loads(base64.b64decode(args.request_base64, validate=True)), argv)


if __name__ == '__main__':
    main()
