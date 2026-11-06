"""Synthetic native direction markers must not sever or invent residue identity."""
import json

import pytest

from services.family_msa.read_model import alignment_document


def source(sequence='ACGTRYSWKMBDHVN'):
    return {'row_id': 'a', 'header': 'duplicate label', 'sequence': sequence,
            'source_member_id': 'saved-revision', 'occurrence_id': 'first',
            'source_positions': list(range(len(sequence))), 'transformation_provenance': 'synthetic input'}


@pytest.mark.parametrize(('molecule', 'original', 'aligned'), [
    ('dna', 'ACGTRYSWKMBDHVN', 'nbdhvk-mwsryacgt'),
    ('rna', 'ACGURYSWKMBDHVN', 'nbdhvk-mwsryacgu'),
])
def test_explicit_direction_maps_all_iupac_symbols_and_reverses_positions(molecule, original, aligned):
    row = alignment_document(result_id='result', fasta=f'>_R_a\n{aligned}\n', roster=[source(original)],
                             molecule=molecule, mafft_direction='adjustdirectionaccurately').rows[0]
    assert (row.row_id, row.aligned, row.source_orientation) == ('a', aligned, 'reverse_complement')
    assert row.source_positions == [14, 13, 12, 11, 10, 9, None, 8, 7, 6, 5, 4, 3, 2, 1, 0]
    assert (row.source_member_id, row.occurrence_id) == ('saved-revision', 'first')


def test_direction_composes_with_explicit_native_focus_map_and_noncontiguous_source():
    original = dict(source('AC-G'), source_positions=[4, 8, None, 19])
    row = alignment_document(result_id='result', fasta='>_R__focus_a\nc-gt\n', roster=[original],
                             row_map={'_focus_a': 'a'}, molecule='dna', mafft_direction='adjustdirection').rows[0]
    assert row.source_positions == [19, None, 8, 4]


@pytest.mark.parametrize(('molecule', 'direction'), [('dna', None), ('dna', 'unrecognized'), ('protein', 'adjustdirection')])
def test_native_prefix_alone_does_not_authorize_reverse_identity(molecule, direction):
    with pytest.raises(ValueError, match='identity'):
        alignment_document(result_id='result', fasta='>_R_a\nCGT\n', roster=[source('ACG')],
                           molecule=molecule, mafft_direction=direction)


def test_ambiguous_native_prefix_cannot_select_between_two_source_rows():
    roster = [source('ACG'), dict(source('CGT'), row_id='_R_a')]
    with pytest.raises(ValueError, match='ambiguous'):
        alignment_document(result_id='result', fasta='>_R_a\nCGT\n', roster=roster,
                           molecule='dna', mafft_direction='adjustdirection')
    ordinary = alignment_document(result_id='result', fasta='>_R_a\nCGT\n', roster=roster)
    assert ordinary.rows[0].row_id == '_R_a'
    assert ordinary.rows[0].source_orientation == 'forward'


def test_palindromic_reverse_still_reverses_coordinates_and_mismatch_stays_unknown():
    row = alignment_document(result_id='result', fasta='>_R_a\nACGT\n', roster=[source('ACGT')],
                             molecule='dna', mafft_direction='adjustdirection').rows[0]
    assert row.source_positions == [3, 2, 1, 0]
    assert row.source_orientation == 'reverse_complement'
    unknown = alignment_document(result_id='result', fasta='>_R_a\nAAAA\n', roster=[source('ACGT')],
                                 molecule='dna', mafft_direction='adjustdirection').rows[0]
    assert unknown.source_positions is None
    assert unknown.source_orientation is None


@pytest.mark.parametrize('row_map', [[], {'a': 1}])
def test_invalid_native_row_maps_fail_as_data_errors(row_map):
    with pytest.raises(ValueError, match='row map'):
        alignment_document(result_id='result', fasta='>a\nACG\n', roster=[source('ACG')], row_map=row_map)


@pytest.mark.asyncio
async def test_verified_receipt_direction_reaches_document_and_comparison_without_recalculating():
    from routers.family_msa_results import comparison_roster, document
    from bms_alignment.formats import coordinates
    entries = [{'id': 'alignment', 'role': 'alignment'}, {'id': 'receipt', 'role': 'receipt'}]
    receipt = {'requested': {'roster': {'molecule': 'dna', 'rows': [source('ACG')]},
                             'settings': {'engine': 'mafft', 'direction': 'adjustdirectionaccurately'}}}
    blobs = {'alignment': b'>_R_a\nc-gt\n', 'receipt': json.dumps(receipt).encode()}

    async def read(key):
        return blobs[key]

    projected = await document(entries, read, 'alignment')
    compared = await comparison_roster(entries, read, 'alignment')
    assert projected['rows'][0]['source_positions'] == [2, None, 1, 0]
    assert coordinates(compared.rows[0]) == projected['rows'][0]['source_positions']
    assert compared.rows[0].sequence == projected['rows'][0]['aligned']


@pytest.mark.asyncio
@pytest.mark.parametrize('fasta', [
    '>a\nACG\n',  # Missing row without requested subsetting.
    '>a\nAAA\n>b\nACT\n',  # Mutated residues despite an intact artifact checksum.
    '>a\nACG\n>b\nACT\n>b\nACT\n',
    '>a\nACG\n>b\nACT\n>unknown\nACG\n',
    '>a\nACG-\n>b\nACT\n',
])
async def test_changed_native_documents_open_with_an_output_check_finding(fasta):
    from fastapi import HTTPException
    from routers.family_msa_results import document
    rows = [source('ACG'), dict(source('ACT'), row_id='b')]
    receipt = {'requested': {'roster': {'molecule': 'dna', 'rows': rows}, 'settings': {'engine': 'mafft'}}}
    blobs = {'alignment': fasta.encode(), 'receipt': json.dumps(receipt).encode()}

    async def read(key):
        return blobs[key]

    try:
        projected = await document([{'id': 'alignment', 'role': 'alignment'}, {'id': 'receipt', 'role': 'receipt'}], read, 'alignment')
    except HTTPException as failure:
        # Unparseable rows (duplicate/unknown/ragged) still cannot be displayed.
        assert failure.status_code == 409
        return
    assert projected['output_check']['status'] != 'valid'
    assert projected['output_check']['errors']


@pytest.mark.asyncio
async def test_native_subsetting_requires_explicit_setting_and_retains_exact_row_content():
    from routers.family_msa_results import document
    rows = [source('ACG'), dict(source('ACT'), row_id='b')]
    receipt = {'requested': {'roster': {'molecule': 'dna', 'rows': rows},
                             'settings': {'engine': 'mafft', 'sparsepickup': 1}}}
    blobs = {'alignment': b'>a\nACG\n', 'receipt': json.dumps(receipt).encode()}

    async def read(key):
        return blobs[key]

    projected = await document([{'id': 'alignment', 'role': 'alignment'}, {'id': 'receipt', 'role': 'receipt'}], read, 'alignment')
    assert [(row['row_id'], row['aligned']) for row in projected['rows']] == [('a', 'ACG')]
