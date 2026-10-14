"""Separate native producer/consumer qualification under unchanged hardware guard."""
import json
from pathlib import Path
import sys
import tests.z_stop_offline_guard  # noqa: F401, before every native import
from bioxp.protocols.compiler import compile_native_protocol
from bioxp.pipette.cavro_application import compile_application
from bioxp.pipette.cavro_liquid import compile_liquid_recipe
from bioxp.calibration_workflow import CALIBRATION_ACTION_ADAPTER
from bioxp.calibration_measurements import MeasurementForm
from bioxp.calibration_analysis import analyze

root = Path(sys.argv[1])
out = Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
rows = []
for filename, expected in [('documents.json', 87), ('run-documents.json', 87), ('recovery-documents.json', 4)]:
    documents = json.loads((root / filename).read_text())
    assert len(documents) == expected
    for index, document in enumerate(documents):
        rows.append((filename + ':' + str(index), document))
for path in sorted(root.glob('*.json')):
    value = json.loads(path.read_text())
    if isinstance(value, dict) and isinstance(value.get('result'), dict) and value['result'].get('document'):
        rows.append((path.stem, value['result']['document']))
results = []
for name, document in rows:
    parsed = compile_native_protocol(document).to_payload()
    assert parsed == document, name + ': native changed immutable document'
    applications = []
    for stage in parsed['stages']:
        for action in stage['actions']:
            params = action['params']
            if action['kind'] == 'calibration':
                CALIBRATION_ACTION_ADAPTER.validate_python(params)
            if action['kind'] == 'pause_review' and 'measurement_form' in params:
                MeasurementForm.model_validate(params['measurement_form'])
            if params.get('operation') == 'profile_pickup':
                from bioxp.pipette.cavro_geometry import ProfilePickup
                profile = ProfilePickup.model_validate(params)
                assert profile.profile.nominal_volume_ul == params['profile']['nominal_volume_ul']
            if params.get('operation') in ('cavro_application', 'cavro_liquid_recipe'):
                plan = compile_application(params['application']) if 'application' in params else compile_liquid_recipe(params['recipe'])
                assert not plan['issues'], (name, plan['issues'])
                applications.append(plan)
                if params.get('recipe', {}).get('selected_correction'):
                    ledger = plan['application']['liquid_settings']['selected_correction']
                    assert ledger['application_count'] == 1
                    assert ledger['selected'] == params['recipe']['selected_correction']
                    assert ledger['target_liquid_ul'] == params['recipe']['target_liquid_ul']
                    assert ledger['measured_liquid_ul'] is None
                    if name.startswith('actual-analysis-correction-'):
                        from decimal import Decimal
                        producer = document['metadata']['bms_method']['dependencies']['liquid_classes'][0]['measurement_provenance']['result']
                        assert Decimal(str(ledger['commanded_aspiration_ul'])) == Decimal(producer['evaluated'][0]['commanded_ul'])
                        aspirations = [op for op in plan['application']['operations'] if op['operation'] == 'aspirate']
                        assert len(aspirations) == 1 and aspirations[0]['volume_ul'] == ledger['commanded_aspiration_ul']
    results.append({'case': name, 'exact_roundtrip': True, 'applications': applications})
(out / 'native-received.json').write_text(json.dumps(results, indent=2) + '\n')
producer = []
for model in ('exact_point_offset', 'affine_inverse_ols'):
    request = {'kind': 'pipette', 'model': model, 'context': {'fixture': 'synthetic software metrology'},
        'samples': [{'point_id': 'p1', 'target_ul': '20', 'commanded_ul': '22.5', 'observation': {'kind': 'volume', 'measured_ul': '19.75'}}],
        'evaluate_targets_ul': ['20'], 'verification_samples': []}
    if model == 'affine_inverse_ols':
        request['samples'].append({'point_id': 'p2', 'target_ul': '40', 'commanded_ul': '40', 'observation': {'kind': 'volume', 'measured_ul': '38'}})
    result = analyze(request)
    assert result['ok'] and result['correction'] is not None
    producer.append({'request': request, 'result': result})
(out / 'analysis-producers.json').write_text(json.dumps(producer, indent=2) + '\n')
print(json.dumps({'documents_received': len(results), 'original': 87, 'immutable_runs': 87, 'recovery': 4,
    'additional': len(results) - 178, 'exact_roundtrip': True, 'analysis_producers': len(producer)}))
