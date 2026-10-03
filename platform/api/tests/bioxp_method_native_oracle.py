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
    count, applications = 0, 0
    for document in documents:
        parsed = compile_native_protocol(document).to_payload()
        assert parsed == document, 'Native parser changed exported method payload'
        for stage in parsed['stages']:
            for action in stage['actions']:
                assert action['source_occurrence_id']
                assert action['metadata']['bms_method']['step_id']
                count += 1
                params = action['params']
                if params.get('operation') in {'cavro_application', 'cavro_liquid_recipe'}:
                    from bioxp.pipette.cavro_application import compile_application
                    from bioxp.pipette.cavro_liquid import compile_liquid_recipe
                    compiled = (compile_application(params['application']) if params['operation'] == 'cavro_application'
                                else compile_liquid_recipe(params['recipe']))
                    assert not compiled['issues'], compiled['issues']
                    applications += 1
    print(json.dumps({'documents': len(documents), 'native_actions': count, 'native_applications': applications, 'exact_roundtrip': True}))


if __name__ == '__main__':
    main()
