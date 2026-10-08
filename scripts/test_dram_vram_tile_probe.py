from __future__ import annotations

import sys

from dram_vram_tile_probe import (
    GpuWorkerSpec,
    _detect_gpu_specs,
    assign_tiles_weighted,
    estimate_pair_tile_bytes,
    plan_square_tiles,
)


def test_estimate_pair_tile_bytes_counts_all_state_copies() -> None:
    assert estimate_pair_tile_bytes(tile_tokens=256, channels=128, dtype_bytes=2, state_copies=2) == (
        256 * 256 * 128 * 2 * 2
    )


def test_plan_square_tiles_covers_tail_ranges_without_overlap() -> None:
    tiles = plan_square_tiles(sequence_length=1000, tile_tokens=384)

    assert len(tiles) == 9
    assert tiles[0].row_range == (0, 384)
    assert tiles[0].col_range == (0, 384)
    assert tiles[-1].row_range == (768, 1000)
    assert tiles[-1].col_range == (768, 1000)
    assert {(tile.row_range, tile.col_range) for tile in tiles} == {
        ((0, 384), (0, 384)),
        ((0, 384), (384, 768)),
        ((0, 384), (768, 1000)),
        ((384, 768), (0, 384)),
        ((384, 768), (384, 768)),
        ((384, 768), (768, 1000)),
        ((768, 1000), (0, 384)),
        ((768, 1000), (384, 768)),
        ((768, 1000), (768, 1000)),
    }


def test_weighted_assignment_gives_larger_worker_more_tiles() -> None:
    tiles = plan_square_tiles(sequence_length=1024, tile_tokens=256)
    workers = [
        GpuWorkerSpec(gpu_id=0, name="big", max_vram_gb=32.0, weight=3.0),
        GpuWorkerSpec(gpu_id=1, name="small", max_vram_gb=16.0, weight=1.0),
    ]

    assignments = assign_tiles_weighted(tiles, workers)

    assert set(assignments) == {0, 1}
    assert len(assignments[0]) > len(assignments[1])
    assert len(assignments[0]) + len(assignments[1]) == len(tiles)


def test_detect_gpu_specs_prefers_torch_cuda_ordinals(monkeypatch) -> None:
    class FakeProps:
        total_memory = 24 * 1024**3

    class FakeCuda:
        @staticmethod
        def is_available() -> bool:
            return True

        @staticmethod
        def device_count() -> int:
            return 2

        @staticmethod
        def get_device_name(index: int) -> str:
            return ["cuda-ordinal-0", "cuda-ordinal-1"][index]

        @staticmethod
        def get_device_properties(index: int) -> FakeProps:
            return FakeProps()

    class FakeTorch:
        cuda = FakeCuda()

    monkeypatch.setitem(sys.modules, "torch", FakeTorch())

    specs = _detect_gpu_specs([1])

    assert specs == [GpuWorkerSpec(gpu_id=1, name="cuda-ordinal-1", max_vram_gb=24.0, weight=1.5)]
