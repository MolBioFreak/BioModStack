"""Pure native POD5 methylation barrier; no derived stores or scientific reruns.

Pinned modkit 0.6.4 cd85862f71d3bfc289f12adc1052a2e574c95e0f:
writers.rs:266-318 bedMethyl, :809-883 TSV (not the table format).
The existing workflow owns all scientific choices and stage conditionality.
"""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any

import pysam
from services import verified_native_reads as native
import rfc8785

from services import ngs_alignment_sessions
from services.job_result_roots import resolve_persisted_job_result_root
from services.ont_ngs_completion import OntNgsCompletionError
from services.ont_ngs_contract import DORADO_LOCK_PATH
from services.ont_ngs_native_completion import (
    _digest, _identity, _require, _resolve_terminal_output,
    _validate_basecall, _validate_reference_alignment, _validate_native_reference,
)

_MODKIT_OUTPUTS = {
    'modkit_pileup': ('modified_base_input.bam', 'modified_base_input.bam.bai',
                      'modified_base_tag_check.log', 'methylation.bed', 'pileup.log'),
    'modkit_summary': ('modkit_summary.tsv', 'summary.log'),
}


def _kv(text: str, separator: str, *, prefix: str = '') -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        if prefix and not line.startswith(prefix):
            continue
        key, sep, value = line.partition(separator)
        _require(bool(sep) and bool(key) and key not in values, 'invalid or duplicate modkit receipt/TSV field')
        values[key] = value
    return values


def _uint(value: str) -> int:
    _require(re.fullmatch(r'[0-9]+', value) is not None, 'invalid modkit integer')
    return int(value)


def _summary(text: str, record_count: int, available: dict[str, set[str]]) -> int:
    _require(text.endswith('\n'), 'truncated modkit summary')
    values = _kv(text, '\t')
    bases = values['mod_bases'].split(',') if values['mod_bases'] else []
    _require(len(set(bases)) == len(bases) and set(bases) <= set(available), 'invalid modkit summary bases')
    used = _uint(values['total_reads_used'])
    _require(used <= record_count, 'modkit summary read count exceeds input')
    expected = {'mod_bases', 'total_reads_used'}
    # reads_with_mod_calls is independent of the passing-call map. Bases with
    # no passing calls may still have count_reads_X, including zero outcomes.
    for key in values:
        if re.fullmatch(r'count_reads_[ACGT]', key):
            _require(key[-1] in available and _uint(values[key]) <= used, 'modkit per-base reads exceed reads used or have foreign base')
            expected.add(key)
    for base in bases:
        total = _uint(values[f'{base}_total_mod_calls'])
        failed = _uint(values[f'{base}_total_fail_mod_calls'])
        expected.update((f'{base}_total_mod_calls', f'{base}_total_fail_mod_calls'))
        counts = fail_counts = 0
        labels = []
        for key in values:
            match = re.fullmatch(base + r'_pass_calls_(unmodified|modified_(?:[a-zA-Z]|[0-9]+))', key)
            if match:
                _require(match[1] == 'unmodified' or match[1].removeprefix('modified_') in available[base],
                         'modkit summary code absent from native BAM')
                labels.append(match[1])
        _require(bool(labels), 'modkit summary base has no call states')
        for label in labels:
            count_key = f'{base}_pass_calls_{label}'
            frac_key = f'{base}_pass_frac_{label}'
            fail_key = f'{base}_fail_calls_{label}'
            count = _uint(values[count_key])
            fail_counts += _uint(values[fail_key])
            frac = float(values[frac_key])
            _require((total == 0 and math.isnan(frac)) or
                     (total > 0 and math.isfinite(frac) and 0 <= frac <= 1
                      and math.isclose(frac, count / total, rel_tol=1e-12, abs_tol=1e-12)),
                     'modkit summary fraction disagrees with counts')
            counts += count
            expected.update((count_key, frac_key, fail_key))
        # TSV iterates passing states only; filtered-only states need not have
        # individual rows (writers.rs:826-873), so do not require equality here.
        _require(counts == total and fail_counts <= failed, 'modkit summary totals disagree')
    _require(set(values) == expected, 'unknown modkit summary fields')
    return used


def _bed(text: str, contigs: dict, codes: set[str]) -> int:
    _require(not text or text.endswith('\n'), 'truncated bedMethyl')
    seen = set()
    for line in text.splitlines():
        fields = line.split('\t')
        _require(len(fields) == 18, 'invalid native bedMethyl width')
        chrom, code, strand = fields[0], fields[3], fields[5]
        _require(chrom in contigs and code in codes
                 and strand in {'+', '-'}, 'invalid bedMethyl contig/code/strand')
        numbers = {i: _uint(fields[i]) for i in (1, 2, 4, 6, 7, 9, 11, 12, 13, 14, 15, 16, 17)}
        start, end, coverage = numbers[1], numbers[2], numbers[9]
        _require(end == start + 1 and end <= contigs[chrom][0]
                 and numbers[6] == start and numbers[7] == end and fields[8] == '255,0,0'
                 and numbers[4] == coverage and coverage > 0
                 and coverage == numbers[11] + numbers[12] + numbers[13], 'invalid bedMethyl coordinates/coverage')
        percent = float(fields[10])
        _require(math.isfinite(percent) and 0 <= percent <= 100
                 and abs(percent - numbers[11] / coverage * 100) <= 0.0051,
                 'bedMethyl percent disagrees with counts')
        key = (chrom, start, code, strand)
        _require(key not in seen, 'duplicate bedMethyl position/code/strand')
        seen.add(key)
    return len(seen)


def _validate_modkit(root: Path, persisted: Path, job: Any, alignment: dict) -> dict:
    names = []
    stages = job.provenance['stage_terminal_states']
    for stage, outputs in _MODKIT_OUTPUTS.items():
        terminal = stages[stage]
        _require(terminal['status'] == 'complete' and isinstance(terminal['outputs'], list), 'modkit stage is not complete')
        expected = tuple('methylation/' + name for name in outputs)
        _require(tuple(_resolve_terminal_output(value, root, persisted, stage=stage)[1]
                       for value in terminal['outputs']) == expected, 'modkit terminal product contract mismatch')
        names.extend(expected)
    with ExitStack() as stack:
        handles = {name: stack.enter_context(ngs_alignment_sessions._open_regular_file_no_symlinks(root / name)) for name in names}
        ids = {name: (_digest(handle), os.fstat(handle.fileno()).st_size) for name, handle in handles.items()}
        identity = lambda name: ids['methylation/' + name][0]
        text = lambda name: handles['methylation/' + name].read().decode('utf-8')
        # ValidateModifiedBaseBam copies both files without changing records.
        # The native alignment barrier already decoded the BAM and its index.
        _require(identity('modified_base_input.bam') == _identity(root / 'align/aligned.bam')[0]
                 and identity('modified_base_input.bam.bai') == _identity(root / 'align/aligned.bam.bai')[0],
                 'modkit input differs from validated native alignment')
        # Share semantic admission with the actual producer, including legal
        # empty/no-informative tags. No Dorado model whitelist applies to BAM.
        import runpy
        admission = runpy.run_path(str(DORADO_LOCK_PATH.parents[2] / "scripts/validate_modified_base_bam.py"))
        handle = handles['methylation/modified_base_input.bam']
        with native.alignment(handle) as bam:
            expected_tags, available = admission["inspect_bam"](bam)
        from services.ont_ngs_native_completion import _validate_producer
        tag_text = text('modified_base_tag_check.log')
        _validate_producer(tag_text, ("modules/ngs/modkit_pileup.nf", "scripts/validate_modified_base_bam.py"),
                           ("samtools", "python"))
        tag_receipt = _kv("\n".join(line for line in tag_text.splitlines()
                                   if not line.startswith("bms_producer_")), '=')
        _require(tag_receipt == expected_tags
                 and expected_tags['total_records'] == str(alignment['record_count'])
                 and expected_tags['mapped_records'] == str(alignment['mapped_records']),
                 'modkit semantic tag receipt disagrees with validated input')
        threshold = job.params.get('modkit_filter_threshold')
        _require(threshold is None or (type(threshold) in {float, int} and math.isfinite(threshold) and 0 <= threshold <= 1),
                 'invalid modkit filter threshold')
        version = 'modkit ' + json.loads(DORADO_LOCK_PATH.read_bytes())['scientific_tools']['modkit']['version']
        for log, output in [('pileup.log', 'methylation.bed'), ('summary.log', 'modkit_summary.tsv')]:
            log_text = text(log)
            module = "modules/ngs/modkit_pileup.nf" if log == "pileup.log" else "modules/ngs/modkit_summary.nf"
            _validate_producer(log_text, (module,), ("modkit",))
            receipt = _kv("\n".join(line for line in log_text.splitlines()
                                    if not line.startswith("bms_producer_")), '=', prefix='bms_')
            expected = {'bms_modkit_version': version, 'bms_input_sha256': identity('modified_base_input.bam'),
                        'bms_index_sha256': identity('modified_base_input.bam.bai'), 'bms_output_sha256': identity(output)}
            if log == 'pileup.log':
                expected['bms_reference_sha256'] = _identity(root / 'align/reference.fasta')[0]
                args = receipt.pop('bms_filter_args')
                if threshold is None:
                    _require(args == '', 'unrequested modkit threshold was passed')
                else:
                    match = re.fullmatch(r'--filter-threshold ([0-9]+(?:\.[0-9]+)?)', args)
                    _require(match is not None and float(match[1]) == threshold, 'modkit executed threshold differs from selected value')
            _require(receipt == expected, 'modkit execution receipt identity mismatch')
        with ngs_alignment_sessions._open_regular_file_no_symlinks(root / 'align/reference.fasta') as reference:
            contigs, _ = ngs_alignment_sessions._fasta_contigs_from_handle(reference)
        rows = _bed(text('methylation.bed'), contigs, set().union(*available.values()))
        used = _summary(text('modkit_summary.tsv'), alignment['record_count'], available)
        for name, handle in handles.items():
            _require((_digest(handle), os.fstat(handle.fileno()).st_size) == ids[name] and _identity(root / name) == ids[name],
                     'modkit product changed during validation')
    return {'state': 'validated', 'bed_rows': rows, 'summary_reads_used': used,
            'tag_admission': expected_tags,
            'filter_threshold': threshold, 'summary_threshold_policy': 'modkit_native_default',
            'artifacts': [{'path': name, 'sha256': ids[name][0], 'size_bytes': ids[name][1]} for name in names]}


def validate_native_methylation(job: Any) -> dict[str, Any]:
    descriptor = None
    try:
        persisted = resolve_persisted_job_result_root(job)
        descriptor = os.open(persisted, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        root = Path(f'/proc/self/fd/{descriptor}')
        external = job.params.get('ont_input_mode', job.params.get('input_mode')) == 'bam'
        if external:
            from services.ont_ngs_native_external_bam import validate_external_bam
            result, source_id, reference_id = validate_external_bam(root, persisted, job)
            if job.params.get('run_modkit', True) is False:
                _require(not set(job.provenance['stage_terminal_states']) & set(_MODKIT_OUTPUTS),
                         'disabled external modkit has execution stages')
                for names in _MODKIT_OUTPUTS.values():
                    for name in names:
                        path = root / 'methylation' / name
                        _require(not path.exists() and not path.is_symlink(), 'disabled external modkit has stale products')
                result['methylation'] = {'state': 'not_requested'}
            else:
                methylation = _validate_modkit(root, persisted, job, result['alignment'])
                result['artifacts'].extend(methylation.pop('artifacts'))
                result['methylation'] = methylation
            _require(_identity(Path(job.params['bam_path'])) == source_id, 'external source changed before publication')
            if reference_id is not None:
                _require(_identity(Path(job.params['reference_fasta'])) == reference_id,
                         'external reference changed before publication')
        else:
            result = _validate_basecall(root, persisted, job)
        if not external and job.params.get('run_modkit', True) is False:
            stages = job.provenance['stage_terminal_states']
            _require(not (set(stages) & {'modkit_pileup', 'modkit_summary', 'dorado_align'}),
                     'disabled POD5 modkit has unexpected execution stages')
            for names in _MODKIT_OUTPUTS.values():
                for name in names:
                    path = root / 'methylation' / name
                    _require(not path.exists() and not path.is_symlink(), 'disabled modkit has stale products')
            names = ('align/reference.fasta', 'align/reference.fasta.fai', 'align/reference_prepare.log')
            with ExitStack() as stack:
                handles = {name: stack.enter_context(ngs_alignment_sessions._open_regular_file_no_symlinks(root / name)) for name in names}
                ids = {name: (_digest(handle), os.fstat(handle.fileno()).st_size) for name, handle in handles.items()}
                _validate_native_reference(handles, job.params, ids['align/reference.fasta'])
                for name, handle in handles.items():
                    _require((_digest(handle), os.fstat(handle.fileno()).st_size) == ids[name] and _identity(root / name) == ids[name],
                             'native reference changed during validation')
            result['artifacts'].extend({'path': name, 'sha256': ids[name][0], 'size_bytes': ids[name][1]} for name in names)
            result.update(methylation={'state': 'not_requested'}, alignment_state='not_executed')
        elif not external:
            alignment = _validate_reference_alignment(root, persisted, job, result)
            methylation = _validate_modkit(root, persisted, job, alignment)
            result['artifacts'].extend(alignment.pop('artifacts'))
            result['artifacts'].extend(methylation.pop('artifacts'))
            result.update(alignment=alignment, methylation=methylation, alignment_state='validated')
        if not external and result.get("pairs_sha256") is not None:
            _require(_identity(Path(job.params["duplex_pairs"]))[0] == result["pairs_sha256"],
                     "duplex pairs changed before native methylation publication")
        result['result_kind'] = 'ont_native_methylation'
        for artifact in result['artifacts']:
            _require(_identity(root / artifact['path']) == (artifact['sha256'], artifact['size_bytes']),
                     'native methylation predecessor product changed before publication')
        result['artifact_set_sha256'] = hashlib.sha256(rfc8785.dumps(result['artifacts'])).hexdigest()
        return result
    except OntNgsCompletionError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ngs_alignment_sessions.AlignmentSessionError) as exc:
        raise OntNgsCompletionError('native methylation package is missing, corrupt, or inconsistent') from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
