"""Run separately in the pinned robot checkout, never inside the API pytest process.

Usage: PYTHONPATH=<robot>:<robot>/src python this_file.py <export.json>
The export is produced by test_bioxp_method_compiler under the API offline guard.
"""
import json
from pathlib import Path
import sys
import tests.z_stop_offline_guard  # noqa: F401; must precede native imports
from bioxp.protocols.compiler import compile_native_protocol


def main():
    documents = json.loads(Path(sys.argv[1]).read_text())
    count = 0
    for document in documents:
        parsed = compile_native_protocol(document).to_payload()
        assert parsed == document, 'Native parser changed exported method payload'
        for stage in parsed['stages']:
            for action in stage['actions']:
                assert action['source_occurrence_id']
                assert action['metadata']['bms_method']['step_id']
                count += 1
    print(json.dumps({'documents': len(documents), 'native_actions': count, 'exact_roundtrip': True}))


if __name__ == '__main__':
    main()
