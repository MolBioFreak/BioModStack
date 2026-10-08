from __future__ import annotations

from array import array
import hashlib
import math
import time
from typing import Any

import pytest
import rfc8785


class _FingerprintRecord:
    def __init__(self, tags: list[tuple[str, Any, str]]):
        self._tags = tags
        self.get_tags_calls = 0
        self.query_name = 'réc\"ord\\\n\u0001'
        self.flag = 0
        self.reference_name = 'plásmid'
        self.reference_start = 10
        self.mapping_quality = 60
        self.cigarstring = '4M'
        self.next_reference_name = None
        self.next_reference_start = -1
        self.template_length = 0
        self.query_sequence = 'ACGT'
        self.query_qualities = array('B', [40, 39, 38, 37])
        self.reference_end = 14
        self.is_reverse = False

    def get_tags(self, *, with_value_type: bool = False):
        assert with_value_type is True
        self.get_tags_calls += 1
        return list(self._tags)

    def to_string(self) -> str:
        raise AssertionError('v5 catalog construction must not expand a SAM record')


def _oracle_payload(record: _FingerprintRecord) -> dict[str, Any]:
    from services.ngs_alignment_presentation_v5 import _canonical_tag_value

    tags = [
        [str(tag), str(sam_type), _canonical_tag_value(value)]
        for tag, value, sam_type in record._tags
    ]
    tags.sort(key=lambda item: (item[0], item[1], rfc8785.dumps(item[2])))
    return {
        'query_name': record.query_name,
        'flag': int(record.flag),
        'reference_name': record.reference_name,
        'reference_start': int(record.reference_start),
        'mapping_quality': int(record.mapping_quality),
        'cigar': record.cigarstring,
        'mate_reference_name': record.next_reference_name,
        'mate_reference_start': None,
        'template_length': int(record.template_length),
        'sequence_sha256': hashlib.sha256(record.query_sequence.encode('ascii')).hexdigest(),
        'quality_sha256': hashlib.sha256(bytes(record.query_qualities)).hexdigest(),
        'optional_tags': tags,
    }


@pytest.mark.parametrize(
    'value',
    [
        0.0,
        -0.0,
        1e-7,
        1e-6,
        1e20,
        1e21,
        333333333.33333329,
        'é\"\\\n\b\f\r\t\u0001',
        None,
        9007199254740991,
        [0, -1, 127, 65535, 4294967295],
        {'z': 0.0, 'é': '雪', 'a': -0.0},
    ],
)
def test_specialized_canonical_json_matches_rfc8785_scalar_and_container_boundaries(
    value: Any,
) -> None:
    from services.ngs_alignment_presentation_v5 import _canonical_json_bytes

    assert _canonical_json_bytes(value) == rfc8785.dumps(value)


@pytest.mark.parametrize('typecode', ['b', 'B', 'h', 'H', 'i', 'I', 'f'])
def test_fingerprint_bytes_and_hash_match_rfc8785_for_every_b_array_subtype(
    typecode: str,
) -> None:
    from services.ngs_alignment_presentation_v5 import (
        alignment_record_fingerprint,
        alignment_record_fingerprint_bytes,
    )

    values = [1, -2, 3] if typecode in {'b', 'h', 'i'} else [1, 2, 3]
    if typecode == 'f':
        values = [0.0, -0.0, 1.25]
    record = _FingerprintRecord([('XA', array(typecode, values), 'B')])
    oracle = rfc8785.dumps(_oracle_payload(record))

    assert alignment_record_fingerprint_bytes(record) == oracle
    assert alignment_record_fingerprint(record) == hashlib.sha256(oracle).hexdigest()


def test_fingerprint_canonicalization_preserves_duplicates_bytes_order_and_tuple_sorting() -> None:
    from services.ngs_alignment_presentation_v5 import alignment_record_fingerprint_bytes

    tags = [
        ('ZZ', b'\x00\xff', 'H'),
        ('AA', array('b', [2, 1]), 'B'),
        ('AA', array('b', [1, 2]), 'B'),
        ('AA', array('b', [1, 2]), 'B'),
        ('FS', 0.0, 'f'),
    ]
    first = _FingerprintRecord(tags)
    reordered = _FingerprintRecord(list(reversed(tags)))
    changed_order = _FingerprintRecord([
        ('ZZ', b'\x00\xff', 'H'),
        ('AA', array('b', [2, 1]), 'B'),
        ('AA', array('b', [2, 1]), 'B'),
        ('AA', array('b', [1, 2]), 'B'),
        ('FS', 0.0, 'f'),
    ])
    oracle = rfc8785.dumps(_oracle_payload(first))

    assert alignment_record_fingerprint_bytes(first) == oracle
    assert alignment_record_fingerprint_bytes(reordered) == oracle
    assert alignment_record_fingerprint_bytes(changed_order) != oracle
    assert oracle.count(b'[\"AA\",\"B\"') == 3
    assert b'{\"hex\":\"00ff\"}' in oracle


@pytest.mark.parametrize('bad', [math.nan, math.inf, -math.inf, object(), 9007199254740992])
def test_fingerprint_rejects_nonfinite_unencodable_and_unsafe_integer_values(bad: Any) -> None:
    from services.ngs_alignment_presentation_v5 import alignment_record_fingerprint_bytes

    with pytest.raises((TypeError, ValueError)):
        alignment_record_fingerprint_bytes(_FingerprintRecord([('XX', bad, 'f')]))


def test_one_typed_tag_decode_feeds_fingerprint_nm_and_dorado_without_sam_expansion() -> None:
    from services.ngs_alignment_presentation_v5 import _source_record_projection

    record = _FingerprintRecord([
        ('NM', 1, 'i'),
        ('mv', array('b', [5, 1, 0, 1]), 'B'),
        ('ts', 10, 'i'),
        ('ns', 110, 'i'),
    ])

    fingerprint, catalog = _source_record_projection(record, ordinary_unmapped=False)

    assert record.get_tags_calls == 1
    assert len(fingerprint) == 64
    assert catalog is not None
    assert catalog['edit_distance'] == 1
    assert catalog['reference_substitution_count'] == 1
    assert catalog['dorado_tag_parse_valid'] is True
    assert catalog['dorado_tag_move_stride_samples'] == 5
    assert catalog['dorado_tag_emitted_bases'] == 2
    assert catalog['dorado_tag_start_sample'] == 10
    assert catalog['dorado_tag_end_sample'] == 110


@pytest.mark.parametrize(
    ('moves', 'expected_valid', 'expected_emitted'),
    [
        (array('b', [5, 1, 0, 1, 127]), True, 129),
        (array('b', [5, -1, 1]), False, None),
        (array('b', [-1, 1, 1]), False, None),
    ],
)
def test_dorado_signed_byte_domain_and_emitted_count_are_exact(
    moves: array,
    expected_valid: bool,
    expected_emitted: int | None,
) -> None:
    from services.ngs_alignment_presentation_v5 import _dorado_projection

    projection = _dorado_projection([
        ('mv', moves, 'B'),
        ('ts', 10, 'i'),
        ('ns', 110, 'i'),
    ])

    assert projection['dorado_tag_parse_valid'] is expected_valid
    assert projection['dorado_tag_emitted_bases'] == expected_emitted


def test_dorado_duplicate_required_tags_are_rejected() -> None:
    from services.ngs_alignment_presentation_v5 import _dorado_projection

    projection = _dorado_projection([
        ('mv', array('b', [5, 1]), 'B'),
        ('mv', array('b', [5, 1]), 'B'),
        ('ts', 10, 'i'),
        ('ns', 110, 'i'),
    ])

    assert projection['dorado_tag_parse_valid'] is False


@pytest.mark.parametrize(
    'tags',
    [
        [('NM', 1, 'i'), ('NM', 1, 'i')],
        [('NM', 1, 'I')],
        [('NM', -1, 'i')],
        [('NM', True, 'i')],
    ],
)
def test_typed_nm_projection_preserves_exactly_one_well_typed_nonnegative_rule(
    tags: list[tuple[str, Any, str]],
) -> None:
    from services.ngs_alignment_presentation_v5 import _source_record_projection

    record = _FingerprintRecord(tags)
    _fingerprint, catalog = _source_record_projection(record, ordinary_unmapped=False)

    assert catalog is not None
    assert catalog['edit_distance'] is None
    assert catalog['reference_substitution_count'] is None


def test_optional_tag_canonical_value_is_serialized_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from services import ngs_alignment_presentation_v5 as module

    record = _FingerprintRecord([('mv', array('b', [5, *([1, 0] * 5_000)]), 'B')])
    large_value_encodes = 0
    original = module.json.dumps

    def counted(value: Any, *args: Any, **kwargs: Any) -> str:
        nonlocal large_value_encodes
        if isinstance(value, (list, tuple)) and len(value) > 5_000:
            large_value_encodes += 1
        return original(value, *args, **kwargs)

    monkeypatch.setattr(module.json, 'dumps', counted)
    assert module.alignment_record_fingerprint_bytes(record) == rfc8785.dumps(
        _oracle_payload(record)
    )
    assert large_value_encodes == 1


def test_accepted_shape_cached_fingerprint_and_dorado_projection_are_bounded() -> None:
    from services import ngs_alignment_presentation_v5 as module

    record = _FingerprintRecord([
        ('NM', 1, 'i'),
        ('mv', array('b', [5, *[index & 1 for index in range(250_000)]]), 'B'),
        ('ts', 10, 'i'),
        ('ns', 1_250_010, 'i'),
    ])
    oracle = rfc8785.dumps(_oracle_payload(record))
    calls = 0
    original = module.rfc8785.dumps

    def counted(value: Any) -> bytes:
        nonlocal calls
        calls += 1
        return original(value)

    module.rfc8785.dumps = counted
    try:
        started = time.perf_counter()
        fingerprint, catalog = module._source_record_projection(
            record,
            ordinary_unmapped=False,
        )
        optimized_seconds = time.perf_counter() - started
    finally:
        module.rfc8785.dumps = original
    started = time.perf_counter()
    measured_oracle = rfc8785.dumps(_oracle_payload(record))
    oracle_seconds = time.perf_counter() - started

    assert bytes.fromhex(fingerprint) == hashlib.sha256(oracle).digest()
    assert measured_oracle == oracle
    assert record.get_tags_calls == 1
    assert catalog is not None
    assert catalog['dorado_tag_parse_valid'] is True
    assert catalog['dorado_tag_emitted_bases'] == 125_000
    assert calls <= 4
    assert optimized_seconds < oracle_seconds * 0.9


def _naive_coverage(reference_length: int, bin_width: int, blocks: list[tuple[int, int]]) -> list[int]:
    coverage = [0] * math.ceil(reference_length / bin_width)
    for block_start, block_end in blocks:
        if block_end <= block_start:
            continue
        for bin_index in range(block_start // bin_width, (block_end - 1) // bin_width + 1):
            left = max(block_start, bin_index * bin_width)
            right = min(block_end, (bin_index + 1) * bin_width)
            coverage[bin_index] += max(0, right - left)
    return coverage


@pytest.mark.parametrize(
    ('reference_length', 'bin_width', 'blocks'),
    [
        (1, 1, []),
        (1, 1, [(0, 1), (0, 1)]),
        (10, 3, [(0, 10)]),
        (10, 3, [(1, 2), (2, 9), (3, 6), (6, 10)]),
        (17, 4, [(0, 4), (4, 8), (3, 5), (7, 17), (7, 17)]),
        (101, 7, [(0, 1), (6, 8), (13, 99), (100, 101)]),
    ],
)
def test_range_add_coverage_matches_retained_naive_oracle_table(
    reference_length: int,
    bin_width: int,
    blocks: list[tuple[int, int]],
) -> None:
    from services.ngs_alignment_presentation_v5 import _coverage_for_blocks

    assert _coverage_for_blocks(reference_length, bin_width, blocks) == _naive_coverage(
        reference_length, bin_width, blocks
    )


def test_range_add_coverage_matches_naive_oracle_for_adversarial_random_intervals() -> None:
    import random

    from services.ngs_alignment_presentation_v5 import _coverage_for_blocks

    rng = random.Random(0xB105)
    for reference_length in (1, 2, 3, 7, 31, 257):
        for bin_width in range(1, reference_length + 2):
            blocks = []
            for _ in range(80):
                start = rng.randrange(reference_length + 1)
                end = rng.randrange(start, reference_length + 1)
                blocks.append((start, end))
            assert _coverage_for_blocks(reference_length, bin_width, blocks) == _naive_coverage(
                reference_length, bin_width, blocks
            )


def test_range_add_block_work_is_constant_in_number_of_covered_bins() -> None:
    from services.ngs_alignment_presentation_v5 import _add_coverage_block

    class CountingList(list[int]):
        writes = 0

        def __setitem__(self, key: int, value: int) -> None:
            self.writes += 1
            super().__setitem__(key, value)

    bin_count = 1_000_000
    boundary = CountingList([0] * bin_count)
    difference = CountingList([0] * (bin_count + 1))

    _add_coverage_block(boundary, difference, 1, bin_count * 2 - 1, 2)

    assert boundary.writes + difference.writes <= 4
