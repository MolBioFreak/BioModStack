"""Pure native producer and differential receiver; run only in guarded image."""
import json
import sys
from pathlib import Path
import tests.z_stop_offline_guard  # noqa: F401
from bioxp.pipette.cavro_commands import (compile_device_command, compile_device_program,
    device_command_schema, device_command_catalog, device_program_schema)
from bioxp.pipette.cavro_application import compile_application
from bioxp.protocols.compiler import compile_native_protocol

mode, source, output = sys.argv[1:]
if mode == 'export':
    from tests.test_cavro_device_commands import COMMAND_CASES, CONFIGS, REPORT_SELECTORS
    catalog = device_command_catalog()
    payloads = [row[0] for row in COMMAND_CASES]
    # Explicit test vectors plus every actual manufacturer's published selector.
    payloads += [{'command': 'configuration', 'setting': setting} for setting, _ in CONFIGS]
    payloads += [{'command': 'report', 'selector': selector} for selector in REPORT_SELECTORS]
    payloads += [{'command': 'lld_parameter', 'parameter': row['parameter'], 'value': row['minimum']} for row in catalog['lld_parameters']]
    payloads += [{'command': 'event_subscription', 'event': event, 'mode': mode}
        for event in ['pressure', 'tip_loss', 'digital_rising', 'digital_falling', 'clld_rising', 'clld_falling', 'lld_success']
        for mode in ['disabled', 'always', 'during_lld']]
    payloads += [{'command': 'digital_output', 'level': 'low'}]
    payloads = list({json.dumps(p, sort_keys=True): p for p in payloads}.values())
    expected = set(device_command_schema()['discriminator']['mapping'])
    assert {p['command'] for p in payloads} == expected
    rows = [{'id': 'command-' + str(i), 'operation': 'device_command', 'field': 'command', 'request': p,
        'compiled': compile_device_command(p)} for i, p in enumerate(payloads)]
    for program_mode in ['append', 'execute']:
        program = {'mode': program_mode, 'commands': [{'command': 'loop_start'}, {'command': 'delay', 'milliseconds': 20},
            {'command': 'loop_end', 'repetitions': 2}, {'command': 'diagnostic', 'test': 'pressure_sensor'}]}
        rows.append({'id': 'program-' + program_mode, 'operation': 'device_program', 'field': 'program', 'request': program,
            'compiled': compile_device_program(program)})
    result = {'command_types': sorted(expected), 'command_schema': device_command_schema(),
        'program_schema': device_program_schema(), 'catalog': catalog, 'rows': rows}
else:
    producer = json.loads((Path(source) / 'device-producer.json').read_text())
    results = []
    for row in producer['rows']:
        compiled = json.loads((Path(source) / ('delivery-device-' + row['id'] + '.json')).read_text())['result']['document']
        assert compile_native_protocol(compiled).to_payload() == compiled
        application = compiled['stages'][0]['actions'][0]['params']['application']
        plan = compile_application(application)
        assert not plan['issues'], plan
        assert plan['operations'][0]['compiled_command'] == row['compiled'], row['id']
        results.append({'id': row['id'], 'exact_document': True, 'exact_native_command': True})
    result = {'rows': results, 'count': len(results), 'command_types': producer['command_types']}
Path(output).write_text(json.dumps(result, indent=2))
print(json.dumps({'mode': mode, 'count': len(result['rows']), 'command_types': len(result['command_types'])}))
