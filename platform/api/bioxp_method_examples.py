"""Intentionally unbound whole-method skeletons, not validated wet-lab recipes."""


def ref(identifier):
    return {'expr': {'version': 1, 'op': 'param', 'id': identifier}}


def examples():
    definitions = {
        'cfps': ('Cell-free protein synthesis', [
            ('prepare', 'checkpoint', 'Prepare authored lysate, energy mix, amino acids and DNA; place vessels', 'operator'),
            ('assemble', 'transfer', 'Assemble explicitly authored reaction components', 'robot'),
            ('mix', 'mix', 'Mix reaction', 'robot'),
            ('incubate', 'thermal_hold', 'Incubate expression reaction with authored attainment/dwell semantics', 'integration_needed'),
            ('agitate', 'checkpoint', 'If selected chemistry requires shaking, perform on external shaker; record return', 'operator'),
            ('collect', 'transfer', 'Collect expressed material into authored final vessel', 'robot'),
            ('finish', 'checkpoint', 'Label/store product; assess expression with external assay', 'operator')]),
        'purification': ('Protein purification after CFPS', [
            ('prepare', 'checkpoint', 'Prepare authored resin/buffers and expressed material', 'operator'),
            ('bind', 'transfer', 'Combine material and binding reagent', 'robot'),
            ('mix', 'mix', 'Mix binding preparation', 'robot'),
            ('incubate', 'wait', 'Authored binding incubation', 'integration_needed'),
            ('separate', 'checkpoint', 'Perform resin-specific external separation or explicitly place on compatible fixed magnetic station; no invented actuator', 'operator'),
            ('remove', 'transfer', 'Remove authored supernatant volume to waste', 'robot'),
            ('wash', 'transfer', 'Add authored wash buffer', 'robot'),
            ('wash_separate', 'checkpoint', 'Perform wash resuspension/separation and return material; repeat per authored chemistry', 'operator'),
            ('elute', 'transfer', 'Add authored elution reagent', 'robot'),
            ('elution_hold', 'wait', 'Authored elution incubation', 'integration_needed'),
            ('final_separate', 'checkpoint', 'Separate resin before collection', 'operator'),
            ('collect', 'transfer', 'Collect eluate into authored destination', 'robot'),
            ('finish', 'checkpoint', 'Label/store and perform external purity/yield assay', 'operator')]),
        'gibson': ('Gibson assembly', [
            ('prepare', 'checkpoint', 'Prepare quantified fragments and chosen assembly mixture', 'operator'),
            ('assemble', 'transfer', 'Assemble using authored stoichiometry and volumes', 'robot'),
            ('mix', 'mix', 'Mix assembly reaction', 'robot'),
            ('incubate', 'thermal_hold', 'Authored assembly incubation and final hold', 'integration_needed'),
            ('collect', 'transfer', 'Collect completed assembly', 'robot'),
            ('finish', 'checkpoint', 'Downstream transformation/verification is operator-performed; not implied by assembly', 'operator')]),
        'golden_gate': ('Golden Gate assembly', [
            ('prepare', 'checkpoint', 'Prepare authored fragments, restriction enzyme, ligase and buffer', 'operator'),
            ('assemble', 'transfer', 'Assemble reaction at authored concentrations', 'robot'),
            ('mix', 'mix', 'Mix assembly reaction', 'robot'),
            ('cycle', 'thermal_profile', 'Run authored restriction/ligation cycles, terminal steps and hold', 'integration_needed'),
            ('collect', 'transfer', 'Collect assembled material', 'robot'),
            ('finish', 'checkpoint', 'Operator performs transformation, storage and external verification', 'operator')]),
        'pcr': ('PCR', [
            ('prepare', 'checkpoint', 'Prepare chosen template, primers, polymerase and buffer', 'operator'),
            ('assemble', 'transfer', 'Combine authored reaction components', 'robot'),
            ('mix', 'mix', 'Mix PCR reaction', 'robot'),
            ('seal', 'checkpoint', 'Seal reaction vessels using an explicitly compatible consumable', 'operator'),
            ('cycle', 'thermal_profile', 'Authored initial denaturation, repeated cycles, extension and final hold', 'integration_needed'),
            ('unseal', 'checkpoint', 'Unseal or transfer material as authored; no automatic pipette piercing assumption', 'operator'),
            ('collect', 'transfer', 'Collect amplified material', 'robot'),
            ('finish', 'checkpoint', 'Operator performs product QC and storage', 'operator')]),
    }
    for acid in ('dna', 'rna'):
        definitions[acid + '_purification'] = (acid.upper() + ' purification', [
            ('prepare', 'checkpoint', 'Prepare sample and chemistry-specific lysis; external centrifugation/homogenization if required', 'operator'),
            ('bind', 'transfer', 'Add authored binding reagent and bead/resin quantity', 'robot'),
            ('mix', 'mix', 'Mix binding suspension', 'robot'),
            ('binding_hold', 'wait', 'Authored binding time', 'integration_needed'),
            ('separate', 'checkpoint', 'Place on compatible fixed magnet or perform external separation; record custody', 'operator'),
            ('remove', 'transfer', 'Remove authored supernatant to waste without assuming unknown fill is zero', 'robot'),
            ('wash', 'transfer', 'Add chemistry-specific wash (not unspecified manufacturer ethanol by analogy)', 'robot'),
            ('wash_separate', 'checkpoint', 'Resuspend/separate and remove wash per authored repeated wash procedure', 'operator'),
            ('dry', 'wait', 'Authored drying interval; no predicted evaporation', 'integration_needed'),
            ('elute', 'transfer', 'Add authored elution buffer', 'robot'),
            ('elution_hold', 'wait', 'Authored elution time', 'integration_needed'),
            ('final_separate', 'checkpoint', 'Separate beads/resin before collection', 'operator'),
            ('collect', 'transfer', 'Collect purified nucleic acid', 'robot'),
            ('finish', 'checkpoint', 'Operator performs concentration/integrity QC and storage', 'operator')])
    output = []
    for identifier, (name, stages) in definitions.items():
        parameters, steps, coverage = [], [], []
        for sid, action, description, owner in stages:
            if action == 'checkpoint':
                inputs = {'message': description}
            else:
                parameters.append({'id': sid + '_inputs', 'label': description, 'type': 'object'})
                inputs = ref(sid + '_inputs')
            if action == 'mix':
                children = []
                for suffix, child_action in (('position', 'move'), ('lower', 'lower'), ('stroke', 'mix'), ('lift', 'lift')):
                    child_id = sid if suffix == 'stroke' else sid + '_' + suffix
                    parameter_id = sid + '_inputs' if suffix == 'stroke' else child_id + '_inputs'
                    if suffix != 'stroke':
                        parameters.append({'id': parameter_id, 'label': description + ': ' + suffix, 'type': 'object'})
                        coverage.append({'step_id': child_id, 'stage_owner': 'robot', 'physical_qualification': 'not demonstrated'})
                    children.append({'step_id': child_id, 'type': 'action', 'action': child_action, 'inputs': ref(parameter_id)})
                steps.append({'step_id': sid + '_compound', 'type': 'group', 'label': description, 'steps': children})
            else:
                steps.append({'step_id': sid, 'type': 'action', 'label': description, 'action': action, 'inputs': inputs})
            coverage.append({'step_id': sid, 'stage_owner': 'robot' if owner == 'integration_needed' else owner, 'physical_qualification': 'not demonstrated'})
        output.append({'id': identifier, 'method': {'schema': 'bms.bioxp-method.v1', 'name': name,
            'description': 'Unbound full-process skeleton. Supply chosen chemistry, consumables, locations and all numeric inputs. No scientific defaults or yield claims.',
            'parameters': parameters, 'steps': steps, 'procedures': [], 'tip_policy': 'manual',
            'deck_plan': {'labware': [], 'materials': [], 'assignments': []}},
            'coverage': coverage, 'bindings': {}, 'status': 'intentionally_unbound'})
    # CFPS followed by its complete purification, not just a setup recipe.
    cfps, purification = output[0], output[1]
    from copy import deepcopy
    combined = deepcopy(cfps)
    combined['id'] = 'cfps_and_purification'
    combined['method']['name'] = 'CFPS followed by protein purification'
    combined['method']['steps'] = []
    combined['method']['parameters'] = []
    combined['coverage'] = []
    for source, prefix in ((cfps, 'expression'), (purification, 'purification')):
        group = {'step_id': prefix, 'type': 'group', 'steps': deepcopy(source['method']['steps'])}
        def prefix_refs(nodes):
            for step in nodes:
                if step['type'] == 'group':
                    prefix_refs(step['steps'])
                elif 'expr' in step['inputs']:
                    step['inputs']['expr']['id'] = prefix + '_' + step['inputs']['expr']['id']
        prefix_refs(group['steps'])
        for parameter in deepcopy(source['method']['parameters']):
            parameter['id'] = prefix + '_' + parameter['id']
            combined['method']['parameters'].append(parameter)
        combined['method']['steps'].append(group)
        combined['coverage'].extend({**entry, 'group': prefix} for entry in source['coverage'])
    output.append(combined)
    return output


def magnetic_variant(entry):
    """Explicit placement scaffold; all chemistry and native settings are authored.

    LOC_MS is the chosen variant's structural target, not a magnet actuator.
    Whole-input references retain the existing Bindings owner. Incomplete defaults
    on magnetic placements contain only that target, never object/mode/science.
    """
    from copy import deepcopy
    parameters, coverage = [], []

    def action(sid, kind, label, magnetic=False):
        parameter = {'id': sid + '_inputs', 'label': label, 'type': 'object'}
        if kind == 'checkpoint':
            inputs = {'message': label}
        else:
            if magnetic:
                parameter['default'] = {'target_location': 'LOC_MS'}
            parameters.append(parameter)
            inputs = ref(parameter['id'])
        coverage.append({'step_id': sid, 'stage_owner': 'operator' if kind == 'checkpoint' else 'robot',
                         'physical_qualification': 'not demonstrated'})
        return {'step_id': sid, 'type': 'action', 'action': kind, 'label': label, 'inputs': inputs}

    def group(sid, label, children, node_type='group', **extra):
        coverage.append({'step_id': sid, 'stage_owner': 'robot', 'physical_qualification': 'not demonstrated'})
        return {'step_id': sid, 'type': node_type, 'label': label, 'steps': children, **extra}

    def mix(sid, label):
        return group(sid + '_compound', label, [
            action(sid + '_' + suffix, kind, label + ': ' + suffix)
            for suffix, kind in (('position', 'move'), ('lower', 'lower'), ('stroke', 'mix'), ('lift', 'lift'))])

    steps = [action('magnetic_prepare', 'checkpoint',
        'Prepare selected magnetic chemistry and compatible labware; select native plate identity and off-magnet destination; external lysis/preparation and tip handling remain operator-authored'),
        group('magnetic_bind', 'Bind material', [
            action('binding_add', 'transfer', 'Add authored binding reagent'),
            mix('binding_mix', 'Mix binding suspension'),
            action('binding_hold', 'wait', 'Authored binding interval')]),
        group('magnetic_separate', 'Separate and remove supernatant', [
            action('binding_on_magnet', 'plate_move', 'Place selected plate on magnetic station', True),
            action('binding_settle', 'wait', 'Authored magnetic settling interval'),
            action('supernatant_remove', 'transfer', 'Remove supernatant with authored aspiration heights/speeds; label alone does not establish pellet avoidance')])]
    parameters.append({'id': 'wash_count', 'label': 'Authored number of wash repetitions', 'type': 'integer'})
    steps.append(group('magnetic_washes', 'Wash repetitions', [
        action('wash_off_magnet', 'plate_move', 'Move selected plate to authored off-magnet destination'),
        action('wash_add', 'transfer', 'Add selected wash reagent and authored volume'),
        mix('wash_mix', 'Resuspend wash off magnet'),
        action('wash_on_magnet', 'plate_move', 'Return selected plate to magnetic station', True),
        action('wash_settle', 'wait', 'Authored wash settling interval'),
        action('wash_remove', 'transfer', 'Remove wash to authored waste destination with explicit native settings')],
        node_type='repeat', count=ref('wash_count')))
    steps.extend([
        action('magnetic_dry', 'wait', 'Authored drying interval; disable/remove if not part of selected chemistry; no evaporation prediction'),
        group('magnetic_elute', 'Elute off magnet', [
            action('elution_off_magnet', 'plate_move', 'Move selected plate to authored off-magnet elution destination'),
            action('elution_add', 'transfer', 'Add authored elution reagent'),
            mix('elution_mix', 'Mix elution suspension off magnet'),
            action('elution_hold', 'wait', 'Authored elution interval')]),
        group('magnetic_collect', 'Final separation and collection', [
            action('final_on_magnet', 'plate_move', 'Place selected plate on magnetic station for collection', True),
            action('final_settle', 'wait', 'Authored final settling interval'),
            action('eluate_collect', 'transfer', 'Collect eluate into authored final vessel')]),
        action('magnetic_finish', 'checkpoint', 'External yield/purity/integrity QC and storage remain operator-performed')])
    method = deepcopy(entry['method'])
    method.update(name=entry['method']['name'] + ' — on-deck magnetic scaffold',
        description='Explicit editable on-deck magnetic placement scaffold, not a validated recipe or ready-to-run preset. Author native object/labware mapping, off-magnet destinations, tip handling, wash count and every time/volume/settings input. No pellet avoidance or purification yield claim.',
        parameters=parameters, steps=steps)
    if entry['id'] == 'cfps_and_purification':
        expression = deepcopy(entry['method']['steps'][0])
        method['steps'] = [expression, group('magnetic_purification', 'On-deck magnetic protein purification', steps)]
        method['parameters'] = deepcopy([p for p in entry['method']['parameters'] if p['id'].startswith('expression_')]) + parameters
        coverage = [deepcopy(c) for c in entry['coverage'] if c.get('group') == 'expression'] + coverage
        coverage.append({'step_id': 'expression', 'stage_owner': 'robot', 'physical_qualification': 'not demonstrated'})
        # Include structural mix groups in the retained expression stage too.
        for node in expression['steps']:
            if node['type'] == 'group':
                coverage.append({'step_id': node['step_id'], 'stage_owner': 'robot', 'physical_qualification': 'not demonstrated'})
    return {'id': 'on_deck_magnetic', 'label': 'On-deck magnetic separation',
        'description': method['description'], 'method': method, 'bindings': {},
        'coverage': coverage, 'status': 'intentionally_unbound'}


def bound_examples():
    """Complete software fixtures, never presets or manufacturer process defaults.

    All numbers/locations are explicitly fixture-authored. Operator steps remain
    reached ordinary reviews; they do not pretend external equipment is present.
    """
    from copy import deepcopy
    output = examples()
    transfer = dict(source=dict(station='LOC_MS', location_id=0, wells=['A1']),
        destination=dict(station='LOC_TC', location_id=2, wells=['A1']), channels=[0, 1, 2, 3],
        volume_ul='10', aspirate_speed='30', dispense_speed='40', source_position_flag=1,
        destination_position_flag=2, source_lift_height_steps=100, destination_lift_height_steps=100)
    fixture_inputs = {
        'transfer': transfer,
        'move': {'location_id': 2, 'well': 'A1', 'position_flag': 0},
        'lower': {'location_id': 2},
        'lift': {'location_id': 2, 'height_steps': 100},
        'mix': {'channels': [0, 1, 2, 3], 'volume_ul': '5', 'aspirate_speed': '30', 'dispense_speed': '40', 'cycles': 2},
        'wait': {'seconds': 1},
        'thermal_hold': {'bank': 'nest', 'target_temp_c': 30, 'duration_s': 1, 'start': 'dispatch'},
        'thermal_profile': {'segments': [
            {'bank': 'nest', 'target_temp_c': 30, 'duration_s': 1, 'start': 'dispatch'},
            {'bank': 'nest', 'target_temp_c': 35, 'duration_s': 1, 'start': 'dispatch'}], 'repeat': 2},
    }
    for entry in output:
        def bind_nodes(nodes):
            for node in nodes:
                if node['type'] == 'group':
                    bind_nodes(node['steps'])
                elif 'expr' in node['inputs']:
                    value = deepcopy(fixture_inputs[node['action']])
                    if node['action'] == 'transfer' and node['step_id'] in ('collect', 'remove'):
                        value['source'] = {'station': 'LOC_TC', 'location_id': 2, 'wells': ['A1']}
                        value['destination'] = {'station': 'LOC_OC', 'location_id': 1,
                            'wells': ['A12' if node['step_id'] == 'remove' else 'A1']}
                        value['source_position_flag'] = 2
                    entry['bindings'][node['inputs']['expr']['id']] = value
        bind_nodes(entry['method']['steps'])
        entry['status'] = 'bound_software_fixture'
        entry['fixture_provenance'] = 'Explicit BMS software-test choices; not a chemistry recommendation, source default, or scientific/physical qualification'
        entry['method']['deck_plan'] = {'labware': [
            {'id': 'fixture-source', 'station': 'LOC_MS', 'profile_id': 'fixture-four-head', 'name': 'Fixture source'},
            {'id': 'fixture-destination', 'station': 'LOC_TC', 'profile_id': 'fixture-four-head', 'name': 'Fixture reaction'},
            {'id': 'fixture-output', 'station': 'LOC_OC', 'profile_id': 'fixture-four-head', 'name': 'Fixture collection/waste wells'}],
            'materials': [{'id': 'fixture-material', 'name': 'Unqualified fixture material', 'kind': 'reagent'}],
            'assignments': [{'id': f'src-{well}', 'labware_id': 'fixture-source', 'well': well,
                'material_id': 'fixture-material', 'volume_ul': '1000'} for well in ('A1', 'C1', 'E1', 'G1')]}
        entry['dependencies'] = {'labware_profiles': [{'id': 'fixture-four-head', 'revision': 1,
            'native_addressing': {'row_increment': 2, 'column_increment': 0, 'reference_channel': 0},
            'provenance': 'Explicit fixture geometry assumption, not calibration'}]}
        for item in entry['coverage']:
            if item['stage_owner'] == 'integration_needed':
                item['stage_owner'] = 'robot'
        entry['method']['description'] += ' Bound companion is a software fixture only.'
    return output
