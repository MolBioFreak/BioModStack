"""Whole-target preparation finishing regressions, independent sequence/oracle expectations."""
import random
import pytest
from Bio.Seq import Seq
from services.assembly.golden_gate_suggest import SuggestRequest, suggest_golden_gate


def target(seed=41, n=1200):
    rng = random.Random(seed)
    motifs = ('CGTCTC', 'GGATG', 'CTGAAG')
    while True:
        sequence = ''.join(rng.choice('ACGT') for _ in range(n))
        circular = sequence + sequence[:6]
        labels_ok = all(len({min(sequence[p:p+w], str(Seq(sequence[p:p+w]).reverse_complement())) for p in (0, 600)}) == 2
                        and all(sequence[p:p+w] != str(Seq(sequence[p:p+w]).reverse_complement()) for p in (0, 600)) for w in (2, 4))
        if labels_ok and not any(m in circular or str(Seq(m).reverse_complement()) in circular for m in motifs):
            return sequence


def request(sequence=None, **kwargs):
    data = dict(target=dict(id='target', source=dict(kind='inline', sequence=sequence or target(), topology='circular')),
                enzymes=['BsmBI'], proposals=1)
    data.update(kwargs)
    return SuggestRequest.model_validate(data)


def test_target_size_is_carried_to_native_splitter():
    result = suggest_golden_gate(request(target_fragment_length=300, min_fragment_length=250, max_fragment_length=350))
    assert result.proposals, result.model_dump()
    proposal = result.proposals[0]
    assert len(proposal.cuts) == 4
    assert proposal.split_request['target_fragment_length'] == 300
    assert max(proposal.fragment_lengths) <= 350
    assert min(proposal.fragment_lengths) >= 250
    assert proposal.candidate.design.solutions[0].sequence == target()


@pytest.mark.parametrize('enzyme', ['FokI', 'AcuI'])
def test_catalog_geometry_preparation_has_independent_duplex_and_export_oracle(enzyme):
    import io
    from Bio import Restriction, SeqIO
    from pydna.dseq import Dseq
    from services.assembly.golden_gate_exports import build_design_exports
    result = suggest_golden_gate(request(enzymes=[enzyme], fragment_count=2, fixed_boundaries=[0, 600]))
    assert result.proposals, result.model_dump()
    p = result.proposals[0]
    assert p.cuts == [0, 600]
    design = p.candidate.design
    assert design.solutions[0].sequence == target()
    assert not design.solutions[0].occurrences
    native = getattr(Restriction, enzyme)
    retained = []
    for outcome, digest in zip(design.preparations, design.digests):
        prepared = next(m for m in design.materials if m.id == outcome.prepared_material_id)
        site, spacer, width = ('GGATG', 'ACGTACGTA', 4) if enzyme == 'FokI' else ('CTGAAG', 'ACGTACGTACGTAC', 2)
        i = int(outcome.part_id.rsplit('-', 1)[1]) - 1
        start, end = (0, 600) if i == 0 else (600, 0)
        body = target()[start+width:end] if end else target()[start+width:]
        left, right = target()[start:start+width], target()[end:end+width]
        expected_order = 'TT' + site + spacer + left + body + right + str(Seq('TT'+site+spacer).reverse_complement())
        assert prepared.sequence == expected_order
        assert len(native.search(Seq(prepared.sequence))) == 2
        physical = digest.fragments[digest.retained_fragment_index]
        oracle = [d for d in Dseq(prepared.sequence).cut(native) if str(d.watson).upper() == physical.top_strand_sequence]
        assert len(oracle) == 1
        d = oracle[0]
        assert str(d.crick).upper() == str(Seq(prepared.sequence[physical.bottom_start_boundary:physical.bottom_end_boundary]).reverse_complement())
        assert d.five_prime_end()[1].upper() == physical.left_end.overhang_sequence_5to3
        assert d.three_prime_end()[1].upper() == physical.right_end.overhang_sequence_5to3
        retained.append(d)
    product = retained[0]
    for d in retained[1:]:
        product = product + d
    oracle_product = str(product.looped().watson).upper()
    assert len(oracle_product) == len(target()) and oracle_product in target() * 2
    exports = build_design_exports(p.candidate.fixed_request, design)
    fasta = {str(r.seq) for name, data in exports.items() if name == 'intermediates.fasta' for r in SeqIO.parse(io.StringIO(data.decode()), 'fasta')}
    assert fasta == {m.sequence for m in design.materials if m.stage == 'prepared'}


def repeated_target(inverted=False, wrap=False):
    s = target(n=1800)
    repeat = s[100:300]
    copy = str(Seq(repeat).reverse_complement()) if inverted else repeat
    s = s[:700] + copy + s[900:]
    if wrap:
        s = s[150:] + s[:150]
    return s


@pytest.mark.parametrize('inverted,wrap', [(False, False), (True, False), (True, True)])
def test_automatic_repeat_junctions_are_clean_and_copies_contained(inverted, wrap):
    from services.assembly.repeat_breakpoints import RepeatAnalysis
    s = repeated_target(inverted, wrap)
    analysis = RepeatAnalysis(s, circular=True, overlap=4)
    assert analysis.families
    result = suggest_golden_gate(request(s, fragment_count=2, min_fragment_length=300, max_fragment_length=1200))
    assert result.proposals, result.model_dump()
    p = result.proposals[0]
    assert all(analysis.clean_cuts()[cut] for cut in p.cuts)
    for fam in analysis.families:
        owners = set()
        for a, b in fam.copies:
            for pos in range(a, b):
                owners.add(next(i for i, cut in enumerate(p.cuts) if (pos-cut) % len(s) < p.fragment_lengths[i]))
        assert len(owners) == 1
    assert p.notes and p.candidate.design.solutions[0].sequence == s
    from services.assembly.golden_gate_workflow_types import SplitTask
    from services.assembly.golden_gate_workflow import run_workflow
    replay = run_workflow(SplitTask.model_validate(p.split_request))
    assert replay.solutions[0].design.solutions[0].sequence == s
    assert [c['position'] for c in replay.search_result['solutions'][0]['cuts']] == p.cuts


def test_exact_user_cuts_survive_repeat_compromise():
    s = repeated_target()
    result = suggest_golden_gate(request(s, fragment_count=2, fixed_boundaries=[0, 800]))
    assert result.proposals, result.model_dump()
    p = result.proposals[0]
    assert p.cuts == [0, 800]
    assert any('repeat' in note.lower() for note in p.notes)
    assert p.candidate.design.solutions[0].sequence == s


@pytest.mark.parametrize('enzyme', ['MmeI', 'TspGWI', 'BseRI', 'HgaI', 'AarI'])
def test_previously_excluded_catalog_geometries_and_concrete_orders(enzyme):
    import re
    from Bio import Restriction
    native = getattr(Restriction, enzyme)
    s = target(seed=52)
    # This case is about preparation; choose independent site-free target bytes.
    rng = random.Random(17)
    while native.search(Seq(s), linear=False):
        s = ''.join(rng.choice('ACGT') for _ in range(1200))
    result = suggest_golden_gate(request(s, enzymes=[enzyme], fragment_count=2))
    assert result.proposals, result.model_dump()
    p = result.proposals[0]
    assert p.candidate.design.solutions[0].sequence == s
    assert not p.candidate.design.solutions[0].occurrences
    for material in p.candidate.design.materials:
        if material.stage == 'prepared':
            assert re.fullmatch('[ACGT]+', material.sequence)
            assert len(native.search(Seq(material.sequence))) == 2


def test_single_prepared_fragment_can_circularize_through_manual_owner():
    from services.assembly.golden_gate import resolve_golden_gate_enzyme, simulate_golden_gate
    from services.assembly.golden_gate_design import digest_fragment_to_assembly
    from services.restriction_catalog import catalog_authority
    result = suggest_golden_gate(request(fragment_count=1, fixed_boundaries=[0]))
    assert result.proposals, result.model_dump()
    p = result.proposals[0]
    c = catalog_authority.require()
    enzyme = resolve_golden_gate_enzyme(enzyme_id='BsmBI', catalog_id=c.catalog_id, expected_catalog_sha256=c.content_sha256)
    d = p.candidate.design.digests[0]
    fragment = digest_fragment_to_assembly(d.fragments[d.retained_fragment_index], part=p.candidate.fixed_request.parts[0])
    product = simulate_golden_gate([fragment], enzyme=enzyme, circular=True)
    assert product.sequence == target()


def test_bsmi_has_truthful_site_removal_explanation():
    result = suggest_golden_gate(request(enzymes=['BsmI']))
    assert not result.proposals
    assert 'recognition site' in result.enzymes[0].status.lower()
    assert '7/5' in result.enzymes[0].status
    assert '3' in result.enzymes[0].status and '2' in result.enzymes[0].status


@pytest.mark.parametrize('enzyme', ['BsmBI', 'AcuI'])
def test_linear_exact_target_preserves_terminal_bases(enzyme):
    s = target()
    req = request(enzymes=[enzyme], fragment_count=2, fixed_boundaries=[600], terminal_right_fusion='CTGA' if enzyme == 'BsmBI' else 'AG')
    data = req.model_dump()
    data['target']['source']['topology'] = 'linear'
    result = suggest_golden_gate(SuggestRequest.model_validate(data))
    assert result.proposals, result.model_dump()
    assert result.proposals[0].cuts == [600]
    assert result.proposals[0].candidate.design.solutions[0].sequence == s
    if enzyme == 'AcuI':
        assert any('external left' in note for note in result.proposals[0].notes)


@pytest.mark.parametrize('enzyme', ['AbaSI', 'EcoO157SI', 'MspJI', 'YkrI'])
def test_default_flanks_do_not_introduce_additional_recognition_sites(enzyme):
    # Geometry-only, unmodified sequence simulation is not a substrate/activity claim.
    rng = random.Random(28)
    s = ''.join(rng.choice('AT') for _ in range(240))
    result = suggest_golden_gate(request(s, enzymes=[enzyme], fragment_count=1, fixed_boundaries=[0]))
    assert result.proposals, result.model_dump()
    assert result.proposals[0].candidate.design.solutions[0].sequence == s


@pytest.mark.parametrize('enzyme', ['HauII', 'SgeI'])
def test_symmetric_motif_with_one_directional_event_is_not_a_false_raw_adapter(enzyme):
    from services.restriction_catalog import catalog_authority
    r = catalog_authority.require().by_id[enzyme]
    assert r.recognition.site_iupac == str(Seq(r.recognition.site_iupac).reverse_complement())
    result = suggest_golden_gate(request(enzymes=[enzyme]))
    assert not result.proposals
    assert 'strand-symmetric' in result.enzymes[0].status
    assert 'inward cleavage' in result.enzymes[0].status


def test_iupac_pcr_oligo_metadata_describes_actual_order_bytes():
    from Bio.Restriction import MmeI
    rng = random.Random(52)
    s = target()
    while MmeI.search(Seq(s), linear=False):
        s = ''.join(rng.choice('ACGT') for _ in range(1200))
    result = suggest_golden_gate(request(s, enzymes=['MmeI'], preparation_kind='pcr', fragment_count=2))
    assert result.proposals, result.model_dump()
    for primer in result.proposals[0].candidate.design.primers:
        prefix = primer.clamp + primer.recognition_site + primer.spacer + primer.fusion
        assert set(prefix) <= set('ACGT')
        assert primer.full_sequence == prefix + primer.annealing_sequence


@pytest.mark.parametrize('name', ['FokI', 'AcuI'])
def test_upstream_geometry_is_reoriented_not_rejected(name):
    from dataclasses import replace
    from services.assembly.golden_gate import resolve_golden_gate_enzyme
    from services.assembly.golden_gate_design import preparation_geometry
    from services.restriction_catalog import catalog_authority
    c = catalog_authority.require()
    enzyme = resolve_golden_gate_enzyme(enzyme_id=name, catalog_id=c.catalog_id, expected_catalog_sha256=c.content_sha256)
    event = enzyme.record.cleavage.events[0]
    n = len(enzyme.site)
    upstream_event = event.model_copy(update=dict(top_offset=n-event.bottom_offset, bottom_offset=n-event.top_offset))
    upstream = replace(enzyme, site=str(Seq(enzyme.site).reverse_complement()),
        record=enzyme.record.model_copy(update={'cleavage': enzyme.record.cleavage.model_copy(update={'events': (upstream_event,)})}))
    assert preparation_geometry(upstream) == preparation_geometry(enzyme)


@pytest.mark.parametrize('enzyme', ['BsmBI', 'PaqCI'])
def test_ten_kilobase_target_four_synthesis_orders_are_exact(enzyme):
    from Bio import Restriction
    motifs = ('CGTCTC', 'GAGACG', 'CACCTGC', 'GCAGGTG')
    rng = random.Random(321)
    s = ''
    while len(s) < 10000:
        base = rng.choice('ACGT')
        if not any((s[-7:] + base).endswith(m) for m in motifs):
            s += base
    assert not getattr(Restriction, enzyme).search(Seq(s), linear=False)
    result = suggest_golden_gate(request(s, enzymes=[enzyme], target_fragment_length=2500, min_fragment_length=2000, max_fragment_length=3000))
    assert result.proposals, result.model_dump()
    p = result.proposals[0]
    assert len(p.cuts) == 4
    assert p.candidate.design.solutions[0].sequence == s
    assert len([m for m in p.candidate.design.materials if m.stage == 'prepared']) == 4
    assert all(2000 <= n <= 3000 for n in p.fragment_lengths)


@pytest.mark.asyncio
async def test_http_fixed_synthesis_design_and_prepared_fasta_export(tmp_path):
    import io
    from zipfile import ZipFile
    from Bio import SeqIO
    from test_golden_gate_workflow_receiving import client_store
    async with client_store(tmp_path) as (client, _):
        payload = request(fragment_count=2, fixed_boundaries=[0, 600], target_fragment_length=600, avoid_repeats=True).model_dump(mode='json')
        response = await client.post('/api/molbio/assembly/golden-gate/suggest', json=payload)
        assert response.status_code == 200, response.text
        p = response.json()['proposals'][0]
        assert p['cuts'] == [0, 600]
        replay = await client.post('/api/molbio/assembly/golden-gate/design', json=p['split_request'])
        assert replay.status_code == 200, replay.text
        candidate = replay.json()['solutions'][0]
        assert candidate['design']['solutions'][0]['sequence'] == target()
        archive = await client.post('/api/molbio/assembly/golden-gate/design/export', json=replay.json())
        assert archive.status_code == 200, archive.text
        with ZipFile(io.BytesIO(archive.content)) as z:
            fasta = {str(r.seq) for r in SeqIO.parse(io.StringIO(z.read('intermediates.fasta').decode()), 'fasta')}
        assert fasta == {m['sequence'] for m in candidate['design']['materials'] if m['stage'] == 'prepared'}
        bad = payload.copy()
        bad['target'] = {'id': 'target', 'source': {'kind': 'inline', 'sequence': target()[:400] + 'CGTCTC' + target()[406:], 'topology': 'circular'}}
        refusal = await client.post('/api/molbio/assembly/golden-gate/suggest', json=bad)
        assert refusal.status_code == 200, refusal.text
        assert not refusal.json()['proposals']
        assert 'internal recognition site' in refusal.json()['enzymes'][0]['status']


def test_three_prime_supplied_parts_preserve_intended_origin_and_long_spacer():
    from test_golden_gate_design_core import A, B
    parts = [dict(source=dict(id=name, source=dict(kind='inline', sequence=seq, topology='linear')))
             for name, seq in [('a', A), ('b', B)]]
    r = suggest_golden_gate(SuggestRequest.model_validate(dict(parts=parts, enzymes=['MmeI'],
        standard_overhangs=['AA', 'AC'], proposals=1)))
    assert r.proposals, r.model_dump()
    p = r.proposals[0]
    assert p.final_sequence == 'AA' + A + 'AC' + B
    assert p.candidate.design.solutions[0].exact_target_match is True


@pytest.mark.parametrize('goal,minimum,maximum,expected_count', [(600, 250, 350, 4), (200, 300, 500, 4)])
def test_target_size_is_a_goal_not_a_conflicting_fragment_count(goal, minimum, maximum, expected_count):
    result = suggest_golden_gate(request(target_fragment_length=goal, min_fragment_length=minimum, max_fragment_length=maximum))
    assert result.proposals, result.model_dump()
    p = result.proposals[0]
    assert len(p.fragment_lengths) == expected_count
    assert all(minimum <= length <= maximum for length in p.fragment_lengths)
    assert p.candidate.design.solutions[0].sequence == target()


@pytest.mark.asyncio
async def test_raw_options_are_catalog_derived_with_substrate_qualification():
    from routers.molbio_ops import golden_gate_options
    from services.restriction_catalog import catalog_authority
    result = await golden_gate_options()
    catalog = catalog_authority.require()
    names = set(result['raw_design']['raw_enzyme_adapters'])
    assert names == {r.canonical_name for r in catalog.records if r.golden_gate_compatible}
    assert {'AcuI', 'MmeI', 'FokI', 'HgaI'} <= names
    assert not {'BsmI', 'HauII', 'SgeI'} & names
    assert any('substrate' in line.lower() for line in result['raw_design']['limitations'])
