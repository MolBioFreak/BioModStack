from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import sys


SCRIPT = Path(__file__).parents[3] / "scripts" / "shape_blueprint" / "run_shape_rfd3.py"
SEQUENCE_SCRIPT = Path(__file__).parents[3] / "scripts" / "shape_blueprint" / "run_shape_sequence.py"


def _module():
    spec = importlib.util.spec_from_file_location("run_shape_rfd3", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def _sequence_module():
    assert SEQUENCE_SCRIPT.exists(), "Shape sequence wrapper is absent"
    spec = importlib.util.spec_from_file_location("run_shape_sequence", SEQUENCE_SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def _write_backbone(path: Path) -> None:
    path.write_text(
        "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
        "ATOM      2  CA  GLY A   2       3.800   0.000   0.000  1.00 20.00           C\n"
        "END\n"
    )


def test_shape_rfd3_wrapper_binds_request_and_guidance_paths(tmp_path: Path) -> None:
    module = _module()
    point_data = b"points"
    sdf_data = b"sdf"
    request = {
        "schema": "bms_shape_design_request_v1",
        "request_id": "00000000-0000-4000-8000-000000000009",
        "geometry_sha256": "2" * 64,
        "point_pool_sha256": hashlib.sha256(point_data).hexdigest(),
        "sdf_sha256": hashlib.sha256(sdf_data).hexdigest(),
        "sdf_sign": "positive_inside",
        "sdf_grid_shape": [48, 48, 48],
        "target_length": 100,
        "num_backbones": 2,
        "seed": 9,
        "generator": "rfd3",
    }
    manifest = {
        "schema": "bms_shape_canonical_geometry_v1",
        "geometry_sha256": "2" * 64,
        "point_pool_sha256": hashlib.sha256(point_data).hexdigest(),
        "sdf_sha256": hashlib.sha256(sdf_data).hexdigest(),
        "sdf_sign": "positive_inside",
        "sdf_grid_shape": [48, 48, 48],
    }
    request_path = tmp_path / "request.json"
    manifest_path = tmp_path / "manifest.json"
    points_path = tmp_path / "points.f32le"
    sdf_path = tmp_path / "sdf.f32le"
    checkpoint = tmp_path / "model.ckpt"
    output = tmp_path / "out"
    receipt = tmp_path / "runtime.json"
    capture = tmp_path / "argv.json"
    request["request_sha256"] = hashlib.sha256(
        json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    request_path.write_text(json.dumps(request))
    manifest_path.write_text(json.dumps(manifest))
    points_path.write_bytes(point_data)
    sdf_path.write_bytes(sdf_data)
    checkpoint.write_bytes(b"checkpoint")
    fake = tmp_path / "fake-rfd3"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "args=sys.argv[1:]; pathlib.Path(os.environ['CAPTURE']).write_text(json.dumps(args))\n"
        "out=pathlib.Path(next(x.split('=',1)[1] for x in args if x.startswith('out_dir='))); out.mkdir(parents=True, exist_ok=True)\n"
        "n=int(next(x.split('=',1)[1] for x in args if x.startswith('n_batches=')))\n"
        "[(out/f'design_{i}.cif.gz').write_bytes(b'cif') for i in range(n)]\n"
        "[(out/f'design_{i}.json').write_text('{}') for i in range(n)]\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

    module.run_shape_rfd3(
        request_path=request_path,
        manifest_path=manifest_path,
        points_path=points_path,
        sdf_path=sdf_path,
        output_dir=output,
        receipt_path=receipt,
        executable=str(fake),
        checkpoint_path=checkpoint,
        environment={"CAPTURE": str(capture)},
    )

    args = json.loads(capture.read_text())
    assert "n_batches=2" in args
    assert "diffusion_batch_size=1" in args
    assert "seed=9" in args
    assert "inference_sampler.kind=shape" in args
    assert "inference_sampler.num_timesteps=200" in args
    assert "+inference_sampler.shape_step_size=0.1" in args
    assert f"+inference_sampler.shape_manifest_path={manifest_path.resolve()}" in args
    assert f"+inference_sampler.shape_points_path={points_path.resolve()}" in args
    assert f"+inference_sampler.shape_sdf_path={sdf_path.resolve()}" in args
    runtime = json.loads(receipt.read_text())
    assert runtime["status"] == "completed"
    assert runtime["output_backbone_count"] == 2
    assert runtime["num_timesteps"] == 200
    assert runtime["guidance_step_size"] == 0.1
    assert runtime["checkpoint_sha256"] == hashlib.sha256(b"checkpoint").hexdigest()
    generated = json.loads((output.parent / "shape_rfd3_input.json").read_text())
    assert generated == {"shape_blueprint": {"dialect": 2, "length": "100-100"}}


def test_shape_rfd3_wrapper_rejects_request_manifest_mismatch(tmp_path: Path) -> None:
    module = _module()
    request = tmp_path / "request.json"
    manifest = tmp_path / "manifest.json"
    request.write_text(json.dumps({"schema": "bms_shape_design_request_v1", "geometry_sha256": "a" * 64}))
    manifest.write_text(json.dumps({"geometry_sha256": "b" * 64}))
    with __import__("pytest").raises(ValueError, match="geometry identity"):
        module.validate_request(request, manifest)


def test_shape_proteinmpnn_lane_discards_native_and_uses_nonzero_seed(tmp_path: Path) -> None:
    module = _sequence_module()
    backbone = tmp_path / "shape_backbone_0001.pdb"
    _write_backbone(backbone)
    capture = tmp_path / "mpnn-args.json"
    fake = tmp_path / "fake-mpnn"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "a=sys.argv[1:]; pathlib.Path(os.environ['CAPTURE']).write_text(json.dumps(a))\n"
        "def v(k): return a[a.index(k)+1]\n"
        "out=pathlib.Path(v('--out_folder'))/'seqs'; out.mkdir(parents=True)\n"
        "stem=pathlib.Path(v('--pdb_path')).stem; n=int(v('--num_seq_per_target'))\n"
        "rows=[f'>{stem}, score=0.0, global_score=0.0\\nAG']\n"
        "rows += [f'>T=0.1, sample={i+1}, score={i+0.1}, global_score={i+0.2}, seq_recovery=0.5\\nAA' for i in range(n)]\n"
        "(out/f'{stem}.fa').write_text('\\n'.join(rows)+'\\n')\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    output = tmp_path / "mpnn-out"
    receipt = tmp_path / "mpnn-receipt.json"
    records = module.run_sequence_lane(
        engine="proteinmpnn",
        backbone_path=backbone,
        output_dir=output,
        receipt_path=receipt,
        count=2,
        seed=0,
        runner=str(fake),
        environment={"CAPTURE": str(capture)},
    )
    args = json.loads(capture.read_text())
    effective_seed = int(args[args.index("--seed") + 1])
    assert 1 <= effective_seed <= 998
    assert args[args.index("--batch_size") + 1] == "1"
    assert args[args.index("--model_name") + 1] == "v_48_020"
    assert [record["sequence"] for record in records] == ["AA", "AA"]
    assert [record["sample_index"] for record in records] == [1, 2]
    assert json.loads(receipt.read_text())["effective_seed"] == effective_seed
    assert (output / "source_backbone.pdb").read_bytes() == backbone.read_bytes()
    assert {record["source_backbone"] for record in records} == {"source_backbone.pdb"}


def test_shape_fampnn_lane_uses_seq_only_and_natural_sample_order(tmp_path: Path) -> None:
    module = _sequence_module()
    backbone = tmp_path / "shape_backbone_0001.pdb"
    _write_backbone(backbone)
    capture = tmp_path / "fampnn-args.json"
    fake = tmp_path / "fake-fampnn"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "a=sys.argv[1:]; pathlib.Path(os.environ['CAPTURE']).write_text(json.dumps(a))\n"
        "vals=dict(x.split('=',1) for x in a if '=' in x); out=pathlib.Path(vals['out_dir'])/'fastas'; out.mkdir(parents=True)\n"
        "stem=next(pathlib.Path(vals['pdb_dir']).glob('*.pdb')).stem; n=int(vals['num_seqs_per_pdb'])\n"
        "order=list(range(n))[::-1]\n"
        "[(out/f'{stem}_sample{i}.fasta').write_text(f'>{stem}_sample{i}\\nAA\\n') for i in order]\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    output = tmp_path / "fampnn-out"
    records = module.run_sequence_lane(
        engine="fampnn",
        backbone_path=backbone,
        output_dir=output,
        receipt_path=tmp_path / "fampnn-receipt.json",
        count=3,
        seed=7,
        runner=str(fake),
        environment={"CAPTURE": str(capture)},
    )
    args = json.loads(capture.read_text())
    assert "checkpoint_path=/app/fampnn/weights/fampnn_0_3.pt" in args
    assert "seq_only=true" in args
    assert "repack_last=false" in args
    assert "fixed_pos_verbose=false" in args
    assert [record["sample_index"] for record in records] == [0, 1, 2]
    assert (output / "source_backbone.pdb").read_bytes() == backbone.read_bytes()
