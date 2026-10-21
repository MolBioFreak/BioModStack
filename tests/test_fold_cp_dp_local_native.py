"""CPU Gloo proof of native Fold-CP writer localization (no model imports)."""
import ast
import os
from pathlib import Path


def main():
    import torch
    import torch.distributed as dist
    from torch.distributed.device_mesh import init_device_mesh
    from torch.distributed.tensor import DTensor, Replicate, Shard, distribute_tensor
    dist.init_process_group('gloo')
    rank = dist.get_rank()
    for filename, shape in [('boltz2_1d.py', (2, 4)), ('boltz2.py', (2, 2, 2))]:
        path = Path(os.environ['PATCHED_CP_ROOT']) / 'src/boltz/distributed/model/models' / filename
        tree = ast.parse(path.read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_writer_dp_local')
        ns = {'DTensor': DTensor, 'Replicate': Replicate}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), ns)
        mesh = init_device_mesh('cpu', shape)
        dp = mesh.get_coordinate()[0]
        for dimensions in [(6, 8), (6, 8, 8), (2, 8)]:
            values = torch.arange(torch.tensor(dimensions).prod()).reshape(dimensions).float()
            placements = [Shard(0)] + [Replicate()] * (len(shape) - 2) + [Shard(1)]
            if len(shape) == 3 and len(dimensions) == 3:
                placements = [Shard(0), Shard(1), Shard(2)]
            distributed = distribute_tensor(values, mesh, placements)
            actual = ns['_writer_dp_local'](distributed)
            expected = values.chunk(2, dim=0)[dp]
            torch.testing.assert_close(actual, expected, rtol=0, atol=0)
            assert actual.shape == expected.shape
        if rank == 0:
            print(filename, 'DP2 CP4 all ranks: coords/masks/token/pair sample domains exact', flush=True)
    dist.destroy_process_group()


if __name__ == '__main__':
    main()
