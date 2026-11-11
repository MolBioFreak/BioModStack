"""Opt-in junction rules preserve exact target and frozen authored intent."""
import io
import json
from zipfile import ZipFile

import pytest
from pydantic import ValidationError
from services.assembly.golden_gate_suggest import SuggestRequest, suggest_golden_gate
from services.assembly.golden_gate_workflow_types import SplitTask, WorkflowResult, SaveDesignRequest
from services.assembly.golden_gate_workflow import run_workflow, freeze_selection
from test_golden_gate_builder_finish import request, target


def test_rule_contract_is_opt_in_and_validated():
    default = request().model_dump()
    assert default['max_homopolymer_run'] is None
    assert default['homopolymer_junction_buffer'] == 0
    proposal = suggest_golden_gate(request(fragment_count=2, avoid_repeats=False)).proposals[0]
    split = SplitTask.model_validate(proposal.split_request)
    assert split.max_homopolymer_run is None
    assert split.homopolymer_junction_buffer == 0
    for cls, data in [(SuggestRequest, default), (SplitTask, split.model_dump())]:
        assert cls.model_validate({**data, 'max_homopolymer_run': 1}).max_homopolymer_run == 1
        for invalid in [{'max_homopolymer_run': 0}, {'homopolymer_junction_buffer': -1}]:
            with pytest.raises(ValidationError):
                cls.model_validate({**data, **invalid})


@pytest.mark.parametrize('circular,sequence,tract', [
    (False, 'CGTAAAAACGTC', set(range(3, 8))),
    (True, 'AAACGTCGTAAA', {9, 10, 11, 0, 1, 2}),
    (False, 'AAACGTCGTAAA', set()),
    (True, 'AAAAAAAAAAAA', set(range(12))),
    (True, 'NAAACGTCTTTN', set()),
])
@pytest.mark.parametrize('buffer', [0, 2, 20])
def test_full_junction_footprint_avoids_only_long_tracts_and_buffer(circular, sequence, tract, buffer):
    from services.assembly.golden_gate_workflow import _homopolymer_junction_rules
    from services.assembly.feature_types import Material
    material = Material(sequence=sequence, topology='circular' if circular else 'linear')
    allowed, notes = _homopolymer_junction_rules(material, 4, 4, buffer)
    n = len(sequence)
    blocked = set()
    for p in tract:
        for d in range(-buffer, buffer + 1):
            if circular:
                blocked.add((p+d) % n)
            elif 0 <= p+d < n:
                blocked.add(p+d)
    expected = {p for p in range(n if circular else n-3)
                if not ({(p+d) % n for d in range(4)} & blocked)}
    assert allowed == expected
    assert bool(notes) == bool(tract)
    if notes:
        assert 'unchanged' in ' '.join(notes).lower()
    unrestricted, no_notes = _homopolymer_junction_rules(material, 4, None, buffer)
    assert unrestricted is None and not no_notes


def tract_target():
    s = target()
    return s[:596] + 'CGTA' + 'A'*8 + 'C' + s[609:]


@pytest.mark.parametrize('exact_window', [False, True])
def test_authored_conflicting_cut_has_honest_diagnostic_without_moving(exact_window):
    baseline = suggest_golden_gate(request(tract_target(), fragment_count=2,
        fixed_boundaries=[0, 597], avoid_repeats=False))
    assert baseline.proposals, baseline.model_dump()
    split = SplitTask.model_validate({**baseline.proposals[0].split_request,
        'max_homopolymer_run': 4, 'homopolymer_junction_buffer': 0})
    if exact_window:
        split = split.model_copy(update={'fixed_positions': []})
    replay = run_workflow(split)
    assert not replay.solutions
    assert replay.requested == split
    message = ' '.join(replay.diagnostics).lower()
    assert 'homopolymer' in message and '597' in message and 'conflict' in message


def test_automatic_rules_use_native_mask_and_preserve_physical_sequence():
    s = tract_target()
    baseline = suggest_golden_gate(request(s, fragment_count=2, avoid_repeats=False))
    p = baseline.proposals[0]
    split = SplitTask.model_validate({**p.split_request, 'max_homopolymer_run': 4,
        'homopolymer_junction_buffer': 2,
        'windows': [{'start': 590, 'end': 620}, {'start': 0, 'end': 40}],
        'fixed_positions': []})
    # The caller's advisory domain is intersected, not replaced.
    domain = {597, 612, 7, 9}
    replay = run_workflow(split, allowed_positions=domain)
    assert replay.solutions, replay.model_dump()
    for choice in replay.search_result['solutions']:
        cuts = [c['position'] for c in choice['cuts']]
        assert set(cuts) <= domain and 597 not in cuts
        assert 612 in cuts
    assert replay.solutions[0].design.solutions[0].sequence == s
    assert any('unchanged' in note for note in replay.diagnostics)


@pytest.mark.parametrize('topology', ['circular', 'linear'])
def test_suggest_carries_rules_and_reports_unchanged_tracts(topology):
    s = tract_target()
    if topology == 'circular':
        s = 'AAA' + s[3:-3] + 'AAA'
    payload = request(s, fragment_count=2, avoid_repeats=True,
        max_homopolymer_run=4, homopolymer_junction_buffer=3,
        terminal_right_fusion='CTGA').model_dump(mode='json')
    payload['target']['source']['topology'] = topology
    result = suggest_golden_gate(SuggestRequest.model_validate(payload))
    assert result.proposals, result.model_dump()
    from services.assembly.golden_gate_workflow import _homopolymer_junction_rules
    from services.assembly.feature_types import Material
    allowed, _ = _homopolymer_junction_rules(Material(sequence=s, topology=topology), 4, 4, 3)
    for proposal in result.proposals:
        assert proposal.split_request['max_homopolymer_run'] == 4
        assert proposal.split_request['homopolymer_junction_buffer'] == 3
        assert set(proposal.cuts) <= allowed
        assert any('homopolymer' in note.lower() and 'unchanged' in note.lower() for note in proposal.notes)
        assert proposal.candidate.design.solutions[0].sequence == s
        replay = run_workflow(SplitTask.model_validate(proposal.split_request))
        assert replay.solutions[0].design.solutions[0].sequence == s


def test_suggest_conflicting_exact_rule_diagnostic_and_null_is_legacy():
    base = dict(fragment_count=2, fixed_boundaries=[0, 597], avoid_repeats=False)
    legacy = suggest_golden_gate(request(tract_target(), **base))
    null = suggest_golden_gate(request(tract_target(), **base,
        max_homopolymer_run=None, homopolymer_junction_buffer=100))
    assert legacy.proposals and null.proposals
    assert legacy.proposals[0].cuts == null.proposals[0].cuts == [0, 597]
    assert legacy.proposals[0].candidate == null.proposals[0].candidate
    conflict = suggest_golden_gate(request(tract_target(), **base,
        max_homopolymer_run=4, homopolymer_junction_buffer=0))
    assert not conflict.proposals
    assert 'homopolymer' in conflict.enzymes[0].status.lower()
    assert '597' in conflict.enzymes[0].status
    assert 'conflict' in conflict.enzymes[0].status.lower()


@pytest.mark.asyncio
async def test_http_replay_save_reopen_export_retains_frozen_rules(tmp_path):
    from test_golden_gate_workflow_receiving import client_store
    base = '/api/molbio/assembly/golden-gate/design'
    async with client_store(tmp_path) as (client, _):
        suggested = await client.post('/api/molbio/assembly/golden-gate/suggest', json=request(
            tract_target(), fragment_count=2, max_homopolymer_run=4,
            homopolymer_junction_buffer=2).model_dump(mode='json'))
        assert suggested.status_code == 200, suggested.text
        authored = suggested.json()['proposals'][0]['split_request']
        assert authored['max_homopolymer_run'] == 4
        assert authored['homopolymer_junction_buffer'] == 2
        replay = await client.post(base, json=authored)
        assert replay.status_code == 200, replay.text
        result = WorkflowResult.model_validate(replay.json())
        assert result.requested.model_dump(mode='json') == authored
        assert result.solutions[0].design.solutions[0].sequence == tract_target()
        frozen = freeze_selection(result, result.selected_solution_id)
        assert frozen.authored_request == result.requested
        saved = await client.post(base+'/save', json=SaveDesignRequest(selection=frozen,
            name='Homopolymer rules', idempotency_key='homopolymer-rules').model_dump(mode='json'))
        assert saved.status_code == 200, saved.text
        operation_id = saved.json()['operation_id']
        reopened = await client.get(base+'/'+operation_id)
        assert reopened.status_code == 200, reopened.text
        assert reopened.json() == saved.json()
        assert reopened.json()['selection']['authored_request'] == authored
        assert reopened.json()['result']['requested'] == authored
        exported = await client.get(base+'/'+operation_id+'/export')
        assert exported.status_code == 200, exported.text
        with ZipFile(io.BytesIO(exported.content)) as z:
            document = json.loads(z.read('workflow.json'))
        assert document['result']['requested'] == authored
        imported = await client.post(base+'/import', json=document)
        assert imported.status_code == 200, imported.text
        assert imported.json()['requested'] == authored


def test_unsatisfiable_automatic_rule_is_reported_not_relaxed():
    baseline = suggest_golden_gate(request(tract_target(), fragment_count=2, avoid_repeats=False)).proposals[0]
    split = SplitTask.model_validate({**baseline.split_request, 'max_homopolymer_run': 4,
        'homopolymer_junction_buffer': 1200,
        'windows': [{'start': 0, 'end': 100}, {'start': 500, 'end': 700}], 'fixed_positions': []})
    result = run_workflow(split)
    assert not result.solutions
    assert 'homopolymer junction rule' in ' '.join(result.diagnostics).lower()
    assert 'no junction set' in ' '.join(result.diagnostics).lower()


def test_rule_aware_orders_digest_and_reassemble_in_independent_physical_oracle():
    from Bio.Restriction import BsmBI
    from pydna.dseq import Dseq
    result = suggest_golden_gate(request(tract_target(), fragment_count=2,
        max_homopolymer_run=4, homopolymer_junction_buffer=2))
    assert result.proposals, result.model_dump()
    design = result.proposals[0].candidate.design
    retained = []
    for prep, digest in zip(design.preparations, design.digests):
        material = next(m for m in design.materials if m.id == prep.prepared_material_id)
        physical = digest.fragments[digest.retained_fragment_index]
        oracle = [d for d in Dseq(material.sequence).cut(BsmBI)
                  if str(d.watson).upper() == physical.top_strand_sequence]
        assert len(oracle) == 1
        retained.append(oracle[0])
    product = retained[0]
    for fragment in retained[1:]:
        product = product + fragment
    sequence = str(product.looped().watson).upper()
    assert len(sequence) == len(tract_target()) and sequence in tract_target()*2
    assert design.solutions[0].sequence == tract_target()


def test_circular_origin_short_terminal_runs_are_one_long_tract():
    s = target()
    s = 'AAA' + s[3:-3] + 'AAA'
    legacy = suggest_golden_gate(request(s, fragment_count=2,
        fixed_boundaries=[0, 600], avoid_repeats=False))
    assert legacy.proposals, legacy.model_dump()
    split = SplitTask.model_validate({**legacy.proposals[0].split_request, 'max_homopolymer_run': 4})
    result = run_workflow(split)
    assert not result.solutions
    assert result.requested.fixed_positions == [0, 600]
    assert any('conflict' in note.lower() and '[0]' in note for note in result.diagnostics)
