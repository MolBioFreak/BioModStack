"""Pure external prepared-BAM authority for reference-owning ONT workflows.

bam_prepare.nf owns sorting/MAPQ, mapped-read admission and mapped-contig
M5 (or trusted original-BAM/reference SHA plus preparation receipt) validation.
Original and prepared byte identities are distinct. No Dorado assumptions.
"""
from collections import Counter
from contextlib import ExitStack
import hashlib
import os
from pathlib import Path
import re

import pysam
from services import verified_native_reads as native

from services.ont_ngs_native_settings import seal_native_settings
from services import ngs_alignment_sessions as files
from services.ont_ngs_native_completion import _digest, _identity, _require, _resolve_terminal_output


def validate_realigned_external_bam(root, persisted, job):
    # Reference authority comes from an actual new DoradoAlign execution receipt,
    # not from asserting a relationship between the original BAM and the FASTA.
    from services.ont_ngs_native_completion import _validate_reference_alignment
    params = job.params
    _require(params.get("bam_force_realign") is True and params.get("reference_fasta"),
             "external realignment selection is missing")
    path = Path(params["bam_path"])
    _require(path.is_absolute(), "external realignment source must be absolute")
    with files._open_regular_file_no_symlinks(path) as handle:
        source_id = (_digest(handle), os.fstat(handle.fileno()).st_size)
        with native.alignment(handle, check_sq=False) as bam:
            _require(bam.is_bam, "external realignment requires BAM")
            count = sum(1 for _ in bam.fetch(until_eof=True))
        _require((_digest(handle), os.fstat(handle.fileno()).st_size) == source_id,
                 "external realignment input changed while parsing")
    declared = params.get("bam_source_sha256")
    _require(not declared or declared.lower() == source_id[0], "external realignment source declaration mismatch")
    alignment = _validate_reference_alignment(root, persisted, job,
        {"calls_bam_sha256": source_id[0], "read_count": count}, source_path=path)
    artifacts = alignment.pop("artifacts")
    _require(_identity(path) == source_id, "external realignment source changed")
    alignment["reference_identity"] = "executed_dorado_alignment_snapshot"
    return {"state": "validated", "partial": False, "workflow_id": params["ont_workflow_id"],
            "input_mode": "bam", "read_count": alignment["record_count"],
            "alignment": alignment, "artifacts": artifacts}, source_id, _identity(root / "align/reference.fasta")


def validate_external_bam(root, persisted, job):
    params = job.params
    stages = job.provenance['stage_terminal_states']
    _require(not set(stages) & {'dorado_basecall', 'dorado_align', 'dorado_demux'},
             'external BAM has unexpected Dorado stages')
    _require(not params.get('pod5_dir') and not params.get('fastq_path'), 'external BAM has conflicting primary inputs')
    enabled = params.get('run_modkit', True) if params['ont_workflow_id'] == 'ont_methylation_analysis' else False
    _require(type(enabled) is bool, 'invalid external modkit setting')
    reference = params.get('reference_fasta')
    _require(not enabled or bool(reference), 'enabled external methylation requires reference')
    min_mapq = params.get('bam_min_mapq', 0)
    _require(type(min_mapq) is int and 0 <= min_mapq <= 255, 'invalid external BAM MAPQ')
    names = ['align/aligned.bam', 'align/aligned.bam.bai', 'align/bam_prepare.log']
    if reference:
        names += ['align/reference.fasta', 'align/reference.fasta.fai']
    terminal = stages['bam_prepare']
    _require(terminal['status'] == 'complete' and isinstance(terminal['outputs'], list)
             and tuple(_resolve_terminal_output(value, root, persisted, stage='bam_prepare')[1]
                       for value in terminal['outputs']) == tuple(names), 'external BAM terminal products disagree')
    mapped_receipt_required = params['ont_workflow_id'] != 'ont_plasmid_qc'
    if reference:
        names += ['align/reference_prepare.log']
        if mapped_receipt_required:
            names += ['align/bam_mapped_check.log']
    else:
        for name in ('reference.fasta', 'reference.fasta.fai', 'reference_prepare.log'):
            path = root / 'align' / name
            _require(not path.exists() and not path.is_symlink(), 'unrequested external reference products')
    source_path = Path(params['bam_path'])
    _require(source_path.is_absolute(), 'external BAM source must be absolute')
    with ExitStack() as stack:
        handles = {name: stack.enter_context(files._open_regular_file_no_symlinks(root / name)) for name in names}
        ids = {name: (_digest(handle), os.fstat(handle.fileno()).st_size) for name, handle in handles.items()}
        source = stack.enter_context(files._open_regular_file_no_symlinks(source_path))
        source_id = (_digest(source), os.fstat(source.fileno()).st_size)
        declarations = {}
        for field in ('bam_source_sha256', 'bam_reference_sha256') if reference else ('bam_source_sha256',):
            value = params.get(field)
            _require(value is None or isinstance(value, str), 'invalid external declared SHA type')
            value = value.strip().lower() if value is not None else ''
            _require(not value or re.fullmatch('[0-9a-f]{64}', value) is not None,
                     'invalid external declared SHA format')
            declarations[field] = value
        declared = declarations['bam_source_sha256']
        _require(not declared or declared == source_id[0], 'external source SHA authority mismatch')
        expected = Counter()
        input_count = 0
        with native.alignment(source, check_sq=False) as bam:
            source_sq = bam.header.to_dict().get('SQ', [])
            sort_order = bam.header.to_dict().get('HD', {}).get('SO', 'unknown')
            for read in bam.fetch(until_eof=True):
                input_count += 1
                if read.mapping_quality >= min_mapq:
                    expected[read.to_string()] += 1
        contigs = {}
        reference_id = None
        if reference:
            selected = Path(reference)
            _require(selected.is_absolute(), 'external reference must be absolute')
            reference_id = ids['align/reference.fasta']
            _require(_identity(selected) == reference_id, 'external reference copy differs from selected input')
            ref = handles['align/reference.fasta']
            contigs, sequence = files._fasta_contigs_from_handle(ref)
            _require(bool(contigs) and bool(sequence)
                     and hashlib.sha256(sequence).hexdigest() == params['reference_sequence_sha256'],
                     'external reference sequence authority mismatch')
            with native.fasta(ref, handles['align/reference.fasta.fai']) as fasta:
                _require(tuple(fasta.references) == tuple(contigs) and all(
                    fasta.get_reference_length(name) == length
                    and hashlib.md5(fasta.fetch(name).upper().encode('ascii'), usedforsecurity=False).hexdigest() == md5
                    for name, (length, md5) in contigs.items()), 'external reference index mismatch')
        fdpath = lambda name: handles[name]
        observed, sequential, index_counts = Counter(), Counter(), Counter()
        mapped_contigs = set()
        no_coordinate = mapped = count = 0
        with native.alignment(fdpath('align/aligned.bam'), fdpath('align/aligned.bam.bai')) as bam:
            _require(bam.is_bam and bam.check_index() and bam.header.to_dict().get('SQ', []) == source_sq,
                     'external prepared BAM dictionary differs from source')
            for read in bam.fetch(until_eof=True):
                count += 1
                observed[read.to_string()] += 1
                if read.reference_id < 0:
                    no_coordinate += 1
                else:
                    index_counts[(read.reference_name, read.is_unmapped)] += 1
                if not read.is_unmapped:
                    _require(read.reference_start >= 0 and read.reference_end is not None
                             and read.reference_end <= bam.lengths[read.reference_id], 'invalid external mapped coordinates')
                    mapped += 1
                    mapped_contigs.add(read.reference_name)
                    sequential[read.to_string()] += 1
            indexed = Counter(read.to_string() for name in bam.references for read in bam.fetch(name) if not read.is_unmapped)
            _require(indexed == sequential and bam.nocoordinate == no_coordinate and all(
                item.mapped == index_counts[(item.contig, False)] and item.unmapped == index_counts[(item.contig, True)]
                for item in bam.get_index_statistics()), 'external BAM index disagrees with records')
        _require(observed == expected and mapped > 0, 'external prepared records differ from source/MAPQ or have no mapped reads')
        keys = {'source_sha256_before', 'source_sha256_after', 'source_immutable', 'bam_min_mapq',
                'input_records', 'output_records', 'mapped_records', 'input_sort_order',
                'preparation_schema', 'prepared_bam_sha256', 'prepared_bai_sha256'}
        from services.ont_ngs_native_completion import _validate_producer
        for relative in ('align/bam_prepare.log', 'align/bam_mapped_check.log', 'align/reference_prepare.log'):
            if relative in handles:
                log = handles[relative]
                _validate_producer(log.read().decode('utf-8'), ("modules/ngs/bam_prepare.nf",), ("samtools",))
                log.seek(0)
        receipt = {}
        for line in handles['align/bam_prepare.log'].read().decode('utf-8').splitlines():
            key, sep, value = line.partition('=')
            if sep and key in keys:
                _require(key not in receipt, 'duplicate external BAM receipt')
                receipt[key] = value
        _require(receipt == {'source_sha256_before': source_id[0], 'source_sha256_after': source_id[0],
            'source_immutable': 'true', 'preparation_schema': 'bms.ngs.bam-preparation.v1',
            'prepared_bam_sha256': ids['align/aligned.bam'][0],
            'prepared_bai_sha256': ids['align/aligned.bam.bai'][0],
            'bam_min_mapq': str(min_mapq), 'input_records': str(input_count),
            'output_records': str(count), 'mapped_records': str(mapped), 'input_sort_order': sort_order},
            'external BAM receipt differs from source/settings/records')
        reference_identity = 'not_requested'
        if reference:
            missing_m5 = False
            for sq in source_sq:
                if sq['SN'] not in mapped_contigs:
                    continue
                _require(sq['SN'] in contigs and sq['LN'] == contigs[sq['SN']][0], 'external mapped contig mismatch')
                if sq.get('M5'):
                    _require(sq['M5'].lower() == contigs[sq['SN']][1], 'external mapped M5 mismatch')
                else:
                    missing_m5 = True
            if missing_m5:
                # Admission reserves these declarations for trusted server callers.
                # Hashing a submitted BAM and selected FASTA alone does not establish
                # that this BAM was aligned to that reference. The receipt binds the
                # prepared bytes to the authorized ORIGINAL, not to an invented M5.
                _require(bool(declared) and declared == source_id[0]
                         and declarations['bam_reference_sha256'] == params['reference_sequence_sha256'],
                         'external no-M5 reference requires trusted original BAM and reference SHA authority')
                reference_identity = 'trusted_source_bam_and_reference_sha256'
            else:
                reference_identity = 'bam_sq_m5'
            if mapped_receipt_required:
                mapped_expected = {
                    'reference_identity': reference_identity,
                    'validated_reference_fasta_sha256': reference_id[0],
                    'preparation_schema': 'bms.ngs.bam-preparation.v1',
                    'preparation_receipt_sha256': ids['align/bam_prepare.log'][0],
                    'original_bam_sha256': source_id[0],
                    'prepared_bam_sha256': ids['align/aligned.bam'][0],
                    'prepared_bai_sha256': ids['align/aligned.bam.bai'][0],
                    'total_reads': str(count), 'mapped_reads': str(mapped),
                }
                if missing_m5:
                    mapped_expected.update(
                        validated_bam_sha256=ids['align/aligned.bam'][0],
                        validated_reference_sha256=params['reference_sequence_sha256'],
                    )
                mapped_keys = set(mapped_expected) | {'validated_bam_sha256', 'validated_reference_sha256'}
                mapped_receipt = {}
                for line in handles['align/bam_mapped_check.log'].read().decode('utf-8').splitlines():
                    key, sep, value = line.partition('=')
                    if sep and key in mapped_keys:
                        _require(key not in mapped_receipt, 'duplicate external mapped BAM receipt')
                        mapped_receipt[key] = value
                _require(mapped_receipt == mapped_expected,
                         'external mapped BAM receipt differs from original/prepared/reference authority')
        for name, handle in handles.items():
            _require((_digest(handle), os.fstat(handle.fileno()).st_size) == ids[name] and _identity(root / name) == ids[name],
                     'external BAM artifact changed during validation')
        _require((_digest(source), os.fstat(source.fileno()).st_size) == source_id and _identity(source_path) == source_id,
                 'external BAM source changed during validation')
    artifacts = [{'path': name, 'sha256': ids[name][0], 'size_bytes': ids[name][1]} for name in names]
    alignment = {'record_count': count, 'mapped_records': mapped, 'bam_min_mapq': min_mapq,
                 'source_bam_sha256': source_id[0],
                 'prepared_bam_sha256': ids['align/aligned.bam'][0],
                 'prepared_bai_sha256': ids['align/aligned.bam.bai'][0],
                 'preparation_receipt_sha256': ids['align/bam_prepare.log'][0],
                 'reference_identity': reference_identity,
                 'reference_sequence_sha256': params.get('reference_sequence_sha256') if reference else None}
    return {'state': 'validated', 'partial': False, 'input_mode': 'bam',
            'workflow_id': params['ont_workflow_id'], 'read_count': count,
            **seal_native_settings(params),
            'alignment': alignment, 'alignment_state': 'validated', 'artifacts': artifacts}, source_id, reference_id
