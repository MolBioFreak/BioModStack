from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

import routers.jobs as jobs


def test_manual_mutagenesis_iteration_job_preserves_boltz_runtime_params(monkeypatch) -> None:
    monkeypatch.setattr(
        jobs,
        "_generate_manual_mutagenesis_variants",
        lambda source_job, designs, config: [{"name": "variant-a", "sequence": "ACDE"}],
    )

    source_job = SimpleNamespace(
        id="source-job",
        name="manual_source",
        parent_job_id="root-job",
        pinned_gpu=0,
        params={
            "boltz_use_msa": True,
            "boltz_sampling_steps": 1000,
            "boltz_recycling_steps": 10,
            "boltz_num_samples": 8,
            "boltz_diffusion_samples": 8,
            "boltz_max_parallel_samples": 1,
            "boltz_use_potentials": True,
            "boltz_step_scale": 1.2,
            "boltz_method": "md",
            "boltz_predict_affinity": True,
            "boltz_sampling_steps_affinity": 300,
            "boltz_diffusion_samples_affinity": 7,
            "boltz_affinity_mw_correction": True,
            "boltz_anchor_target": True,
            "boltz_anchor_strict": True,
            "boltz_target_geometry_mode": "conditioned",
            "boltz_extra_config": "--write_full_pae",
            "msa_preset": "maximum",
        },
    )

    launch_request, variant_count, message_note = jobs._build_manual_mutagenesis_iteration_job(
        source_job=source_job,
        designs=[SimpleNamespace(id="design-1")],
        config=jobs.ManualMutagenesisConfig(mutation_sets=["A1G"], predictor="boltz2", msa_provider="local"),
        name_suffix=None,
        param_overrides={},
    )

    assert variant_count == 1
    assert message_note == ""
    assert launch_request.model_id == "boltz2"
    for key, expected in {
        "boltz_use_msa": True,
        "boltz_sampling_steps": 1000,
        "boltz_recycling_steps": 10,
        "boltz_num_samples": 8,
        "boltz_diffusion_samples": 8,
        "boltz_max_parallel_samples": 1,
        "boltz_use_potentials": True,
        "boltz_step_scale": 1.2,
        "boltz_method": "md",
        "boltz_predict_affinity": True,
        "boltz_sampling_steps_affinity": 300,
        "boltz_diffusion_samples_affinity": 7,
        "boltz_affinity_mw_correction": True,
        "boltz_anchor_target": True,
        "boltz_anchor_strict": True,
        "boltz_target_geometry_mode": "conditioned",
        "boltz_extra_config": "--write_full_pae",
        "msa_preset": "maximum",
    }.items():
        assert launch_request.params[key] == expected


def test_cdr_indel_iteration_job_preserves_boltz_runtime_params(monkeypatch, tmp_path: Path) -> None:
    pdb_path = tmp_path / "source.pdb"
    pdb_path.write_text("HEADER stub\n", encoding="utf-8")

    monkeypatch.setattr(jobs, "_resolve_loop_region_map", lambda root_job: {"H1": (1, 2)})
    monkeypatch.setattr(jobs, "_resolve_design_structure_path", lambda path: Path(path))
    monkeypatch.setattr(
        jobs,
        "_extract_chain_records_from_pdb",
        lambda path: {"A": [{"aa": "A"}, {"aa": "C"}, {"aa": "D"}]},
    )
    monkeypatch.setattr(jobs, "_resolve_loop_target_chain", lambda root_job, loop_ids, available_chain_ids: "A")
    monkeypatch.setattr(jobs, "_build_mutation_regions", lambda binder_records, region_map, loop_ids: {"H1": (1, 2)})
    monkeypatch.setattr(jobs, "_detect_loop_sequence_regions", lambda design_path, binder_chain_id, loop_ids: {"H1": (1, 2)})
    monkeypatch.setattr(
        jobs,
        "_generate_cdr_indel_variants",
        lambda base_sequence, base_name, regions, config: [
            {"name": f"{base_name}_ins", "sequence": "VVV", "mutation": {"type": "insertion", "summary": "ins"}}
        ],
    )

    root_job = SimpleNamespace(
        id="root-job",
        name="root",
        pinned_gpu=0,
        params={
            "boltz_use_msa": True,
            "boltz_sampling_steps": 1000,
            "boltz_recycling_steps": 10,
            "boltz_num_samples": 4,
            "boltz_diffusion_samples": 4,
            "boltz_max_parallel_samples": 1,
            "boltz_use_potentials": True,
            "boltz_step_scale": 1.4,
            "boltz_method": "md",
            "boltz_predict_affinity": True,
            "boltz_sampling_steps_affinity": 200,
            "boltz_diffusion_samples_affinity": 5,
            "boltz_affinity_mw_correction": True,
            "boltz_anchor_target": True,
            "boltz_anchor_strict": False,
            "boltz_target_geometry_mode": "conditioned",
            "boltz_extra_config": "--write_full_pde",
            "msa_preset": "maximum",
        },
    )
    source_job = SimpleNamespace(id="source-job")
    design = SimpleNamespace(id="design-1", name="design_1", pdb_path=str(pdb_path))

    launch_request, variant_count, message_note = jobs._build_cdr_indel_iteration_job(
        root_job=root_job,
        source_job=source_job,
        designs=[design],
        config=jobs.AntibodyCdrIndelConfig(loop_ids=["H1"], predictor="boltz2", msa_provider="local", variants_per_design=1),
        name_suffix=None,
        param_overrides={},
    )

    assert variant_count == 1
    assert message_note == ""
    assert launch_request.model_id == "boltz2"
    for key, expected in {
        "boltz_use_msa": True,
        "boltz_sampling_steps": 1000,
        "boltz_recycling_steps": 10,
        "boltz_num_samples": 4,
        "boltz_diffusion_samples": 4,
        "boltz_max_parallel_samples": 1,
        "boltz_use_potentials": True,
        "boltz_step_scale": 1.4,
        "boltz_method": "md",
        "boltz_predict_affinity": True,
        "boltz_sampling_steps_affinity": 200,
        "boltz_diffusion_samples_affinity": 5,
        "boltz_affinity_mw_correction": True,
        "boltz_anchor_target": True,
        "boltz_anchor_strict": False,
        "boltz_target_geometry_mode": "conditioned",
        "boltz_extra_config": "--write_full_pde",
        "msa_preset": "maximum",
    }.items():
        assert launch_request.params[key] == expected
