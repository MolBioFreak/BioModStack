"""Additional current typed UI/HTTP docs through actual native parser and full compiler."""
import json
import sys
from pathlib import Path
import tests.z_stop_offline_guard  # unchanged native hardware guard
from bioxp.protocols.compiler import compile_native_protocol
from bioxp.pipette.cavro_application import compile_application
from bioxp.pipette.cavro_liquid import compile_liquid_recipe

source, output = map(Path, sys.argv[1:3])
rows = []
for path in sorted(source.glob('geometry-*.json')):
    data = json.loads(path.read_text())
    if not isinstance(data.get('result'), dict) or not data['result'].get('document'):
        continue
    document = data['result']['document']
    parsed = compile_native_protocol(document).to_payload()
    assert parsed == document, path.name
    plans = []
    for stage in parsed['stages']:
        for action in stage['actions']:
            params = action['params']
            if params.get('operation') == 'cavro_liquid_recipe':
                plan = compile_liquid_recipe(params['recipe'])
            elif params.get('operation') == 'cavro_application':
                plan = compile_application(params['application'])
            else:
                continue
            assert not plan['issues'], (path.name, plan['issues'])
            # Compare every native instruction/stage/array against the direct
            # source-owned compiler, not selected field-presence assertions.
            raw = data['request']['method']['steps'][0]['inputs']
            # The two deliberate UI edits target integer-only native fields.
            # Assert raw spelling, then construct the independent typed operand;
            # no native Number / geometry operand is converted or dropped.
            if path.stem.startswith(('geometry-ui-', 'geometry-browser-')) and 'recipe' in raw:
                from copy import deepcopy
                raw = deepcopy(raw)
                reference = raw['recipe']['before_liquid'][1]['reference']
                assert reference['fallback_position_steps'] == '999'
                reference['fallback_position_steps'] = 999
            if path.stem.startswith(('geometry-ui-', 'geometry-browser-')) and 'application' in raw:
                from copy import deepcopy
                raw = deepcopy(raw)
                reference = raw['application']['operations'][0]['calibration']['pickup']['reference']
                assert reference['fallback_position_steps'] == '999'
                reference['fallback_position_steps'] = 999
            direct = compile_liquid_recipe(raw['recipe']) if 'recipe' in raw else compile_application(raw['application'])
            if plan != direct:
                output.with_name(path.stem + '-difference.json').write_text(json.dumps({'received': plan, 'direct': direct}, indent=2))
            assert plan == direct, path.name
            plans.append(plan)
    rows.append({'case': path.name, 'exact_document': parsed, 'full_native_plans': plans, 'direct_plan_equal': True})
assert rows
output.write_text(json.dumps(rows, indent=2) + '\n')
print(json.dumps({'documents': len(rows), 'exact_documents_and_full_plans': True}))
