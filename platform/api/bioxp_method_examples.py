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
            steps.append({'step_id': sid, 'type': 'action', 'label': description, 'action': action, 'inputs': inputs})
            coverage.append({'step_id': sid, 'stage_owner': owner, 'physical_qualification': 'not demonstrated'})
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
        for step in group['steps']:
            if 'expr' in step['inputs']:
                step['inputs']['expr']['id'] = prefix + '_' + step['inputs']['expr']['id']
        for parameter in deepcopy(source['method']['parameters']):
            parameter['id'] = prefix + '_' + parameter['id']
            combined['method']['parameters'].append(parameter)
        combined['method']['steps'].append(group)
        combined['coverage'].extend({**entry, 'group': prefix} for entry in source['coverage'])
    output.append(combined)
    return output
