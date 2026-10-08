"""Separate-process native receiving of compiler repair exports (never hardware).

Run with the pinned native src and original offline guard on PYTHONPATH.
API pytest intentionally does not import native dependencies.
"""
import json
from pathlib import Path
import sys


def main():
    import tests.z_stop_offline_guard  # noqa: F401; precedes every application import
    from bioxp.protocols.compiler import compile_native_protocol
    from bioxp.pipette.cavro_liquid import Recipe, compile_liquid_recipe
    from bioxp.pipette.cavro_application import ApplicationRequest, compile_application
    results = []
    for line in Path(sys.argv[1]).read_text().splitlines():
        case = json.loads(line)
        doc = case['result']['document']
        if doc is None:
            continue
        assert compile_native_protocol(doc).to_payload() == doc
        recipes = 0
        for stage in doc['stages']:
            for action in stage['actions']:
                if action['params'].get('operation') != 'cavro_liquid_recipe':
                    continue
                raw = action['params']['recipe']
                Recipe.model_validate(raw)
                compiled = compile_liquid_recipe(raw)
                assert not compiled['issues'], compiled['issues']
                app = compiled['application']
                ApplicationRequest.model_validate(app)
                assert not compile_application(app)['issues']
                operations = app['operations']
                if case['name'].startswith('mapped-'):
                    assert [op['operation'] for op in operations] == ['settings', 'aspirate', 'delay', 'dispense']
                    assert operations[0]['values'] == {'slope': [3, 4], 'start_speed_ul_s': 12, 'cutoff_speed_ul_s': 15}
                    assert operations[1]['speed_ul_s'] == 47
                    assert operations[2]['duration_ms'] == 3
                if case['name'] in ('multi-liquid_recipe', 'multi-distribute'):
                    assert [op['operation'] for op in operations] == ['aspirate', 'delay'] + ['dispense'] * 6
                    assert [op['volume_ul'] for op in operations if op['operation'] == 'dispense'] == [2] * 3 + [4] * 3
                    assert operations[0]['volume_ul'] == 24
                    assert operations[0]['target_liquid_ul'] == 21
                if case['name'] == 'multi-air-excess':
                    assert sum(op['operation'] == 'reaspirate' for op in operations) == 3
                    assert [op['volume_ul'] for op in operations if op['operation'] == 'reaspirate'] == [1.5] * 3
                recipes += 1
        results.append({'case': case['name'], 'document_roundtrip': True, 'recipe_application_receiving': recipes})
    print(json.dumps({'native_modules': {'recipe': sys.modules[Recipe.__module__].__file__,
        'application': sys.modules[ApplicationRequest.__module__].__file__},
        'documents': len(results), 'recipes': sum(r['recipe_application_receiving'] for r in results), 'cases': results}, indent=2))


if __name__ == '__main__':
    main()
