"""Compose the pinned official Hydra profiles without loading any model."""
from __future__ import annotations
from pathlib import Path
import shutil
import yaml
from scripts.lib.complexa_contract import PROFILES, Request


def compose_request(request: Request, upstream: Path, directory: Path, weights: Path,
                    *, prepared_source: dict | None = None, evaluation_directory: Path | None = None,
                    autoguidance_checkpoint: Path | None = None):
    from hydra import compose, initialize_config_dir
    from omegaconf import OmegaConf
    configs = directory / "configs"
    shutil.copytree(upstream / "configs", configs, dirs_exist_ok=True)
    profile, pair, branch = PROFILES[request.task]
    root = yaml.safe_load((configs / f"{profile}.yaml").read_text())
    if request.task.startswith("monomer_motif"):
        root["defaults"] = [f"pipeline/{branch}_generate@generation",
                            f"pipeline/{branch}_evaluate@_global_",
                            "pipeline/motif/motif_analyze@_global_", "_self_"]
    root.update(ckpt_path=str(weights), ckpt_name=pair + ".ckpt",
                autoencoder_ckpt_path=str(weights / (pair + "_ae.ckpt")),
                seed=request.seed, run_name="bms", gen_njobs=1, eval_njobs=1)
    (configs / "bms_complexa.yaml").write_text(yaml.safe_dump(root, sort_keys=False))
    with initialize_config_dir(version_base="1.3", config_dir=str(configs.resolve())):
        cfg = compose(config_name="bms_complexa")
    # Native composition remains the sole default/inheritance authority.
    OmegaConf.set_struct(cfg, False)
    g = cfg.generation
    g.task_name = request.task_name
    g.args = OmegaConf.merge(g.args, request.args.model_dump())
    if autoguidance_checkpoint is not None:
        g.args.ag_ckpt_path = str(autoguidance_checkpoint)
    g.model = OmegaConf.merge(g.model, request.model.model_dump())
    g.n_recycle = request.n_recycle
    ds = g.dataloader.dataset
    ds.nrepeat_per_sample = request.nrepeat_per_sample
    if request.batch_size is not None:
        g.dataloader.batch_size = request.batch_size
    if request.search is not None:
        search = request.search.model_dump()
        if search["max_batch_size"] is None:
            search["max_batch_size"] = "${..dataloader.batch_size}"
        g.search = search
    for algorithm in ("beam_search", "fk_steering"):
        if g.search.get(algorithm, {}).get("save_intermediate_states"):
            g.search[algorithm].trajectory_dir = str((directory / "trajectories" / algorithm).resolve())
    if request.refinement is not None:
        g.refinement = OmegaConf.merge(g.refinement, request.refinement.model_dump(exclude_unset=True))
    if request.filtering is not None:
        g.filter = OmegaConf.merge(g.filter, request.filtering.model_dump(exclude_unset=True))
    if request.evaluation is not None:
        cfg.metric = OmegaConf.merge(cfg.metric, request.evaluation.model_dump(exclude_unset=True))
    if request.analysis is not None:
        cfg.aggregation = OmegaConf.merge(cfg.aggregation, request.analysis.model_dump(exclude_unset=True))
    if request.reward is None:
        g.reward_model = None
    elif request.reward != "profile":
        reward_models, reward_weights = {}, {}
        classes = {"af2folding": ("alphafold2_reward.AF2RewardModel", "af2_weight"),
                   "rf3folding": ("rf3_reward.RF3RewardRunner", "rf3_weight"),
                   "tmol": ("tmol_reward.TmolRewardModel", "tmol_weight"),
                   "bioinformatics": ("bioinformatics_reward.BioinformaticsRewardModel", "bioinformatics_weight")}
        for name, (native_class, weight) in classes.items():
            spec = getattr(request.reward, name)
            if spec is not None:
                reward_models[name] = {"_target_": "proteinfoundation.rewards." + native_class, **spec.model_dump()}
                reward_weights[name] = getattr(request.reward, weight)
        if "af2folding" in reward_models:
            reward_models["af2folding"]["af_params_dir"] = "${oc.env:AF2_DIR}"
        if "rf3folding" in reward_models:
            reward_models["rf3folding"].update(ckpt_path="${oc.env:RF3_CKPT_PATH}", rf3_path="${oc.env:RF3_EXEC_PATH}")
        g.reward_model = {"_target_": "proteinfoundation.rewards.base_reward.CompositeRewardModel",
                          "reward_models": reward_models, "weights": reward_weights}
    indexed = request.task == "monomer_motif_indexed"
    if indexed:
        ds.nres = None
        if request.nsamples is not None:
            ds.conditional_features[0].nsamples = request.nsamples
    else:
        for native, value in (("nsamples", request.nsamples), ("low", request.min_length), ("high", request.max_length)):
            if value is not None:
                ds.nres[native] = value
    if request.task.startswith("monomer_motif"):
        for key, value in (("min_length", request.min_length), ("max_length", request.max_length)):
            if value is not None:
                ds.conditional_features[0][key] = value
    if request.source is not None:
        if prepared_source is None:
            raise ValueError("source requires the retained portable source binding")
        s = request.source
        path = prepared_source["native_path"]
        length = [request.min_length, request.max_length or request.min_length]
        common = {"task_name": request.task_name, "pdb_path": path}
        motif = {"_target_": "proteinfoundation.datasets.gen_dataset.MotifFeatures", **common,
                 "atom_selection_mode": s.atom_selection_mode, "motif_only": s.motif_only,
                 "min_length": request.min_length, "max_length": request.max_length,
                 "segment_order": s.segment_order, "padding": indexed}
        spec = prepared_source.get("motif_atom_spec")
        if spec:
            motif["motif_atom_spec"] = spec
        else:
            motif["contig_string"] = s.contig_string
        if indexed:
            motif["nsamples"] = request.nsamples if request.nsamples is not None else 100
        ligand = {"_target_": "proteinfoundation.datasets.gen_dataset.LigandFeatures", **common,
                  "ligand": prepared_source.get("ligand") or s.ligand, "ligand_only": s.ligand_only,
                  "SMILES": s.SMILES, "use_bonds_from_file": s.use_bonds_from_file}
        if request.task == "protein_binder":
            ds.conditional_features = [{"_target_": "proteinfoundation.datasets.gen_dataset.TargetFeatures", **common,
                "binder_gen_only": True, "input_spec": s.input_spec, "target_hotspots": s.target_hotspots,
                "binder_center": s.binder_center, "pdb_id": s.pdb_id}]
        elif request.task == "ligand_binder":
            ds.conditional_features = [ligand]
        elif request.task == "AME":
            ds.conditional_features = [motif, ligand]
        else:
            ds.conditional_features = [motif]
        # Evaluation consumes the same target dictionary, not a separately rebuilt target.
        target = {"target_path": path, "target_filename": Path(path).stem, "source": "bms",
                  "binder_length": length, "hotspot_residues": s.target_hotspots,
                  "target_input": s.input_spec, "pdb_id": s.pdb_id,
                  "ligand": ligand["ligand"], "ligand_only": s.ligand_only, "SMILES": s.SMILES,
                  "use_bonds_from_file": s.use_bonds_from_file, "contig_atoms": spec}
        if request.task.startswith("monomer_motif"):
            target = {"motif_pdb_path": path, "contig_string": s.contig_string,
                      "motif_min_length": request.min_length, "motif_max_length": request.max_length,
                      "motif_only": s.motif_only, "atom_selection_mode": s.atom_selection_mode,
                      "segment_order": s.segment_order}
            g.motif_dict_cfg[request.task_name] = target
            cfg.dataset.motif_dict_cfg[request.task_name] = target
        else:
            dictionary = "motif_target_dict_cfg" if request.task == "AME" else "target_dict_cfg"
            g[dictionary][request.task_name] = target
    if evaluation_directory is not None:
        cfg.input_mode = "pdb_dir"
        cfg.sample_storage_path = str(evaluation_directory)
        cfg.output_dir = "./evaluation_results/bms"
        if not request.task.startswith("monomer_motif"):
            dictionary = "motif_target_dict_cfg" if request.task == "AME" else "target_dict_cfg"
            cfg.dataset = {"task_name": request.task_name, dictionary: OmegaConf.to_container(g[dictionary], resolve=False)}
    # Keep optional env references lazy: generation-only does not require every oracle.
    OmegaConf.save(cfg, configs / "bms_complexa.yaml", resolve=False)
    return cfg


def selected_dependencies(request: Request, cfg) -> list[str]:
    """Selected inference dependencies, not a claim about Python import closure."""
    from omegaconf import OmegaConf
    deps = {PROFILES[request.task][1]}
    g = cfg.generation
    if request.operation in ("generate", "design"):
        reward = g.get("reward_model")
        if reward is not None:
            for name in reward.get("reward_models", {}):
                deps.add({"af2folding": "af2", "rf3folding": "rf3"}.get(name, name))
        if g.get("refinement", {}).get("algorithm") == "sequence_hallucination":
            deps.add("af2")
    if request.operation in ("design", "evaluate", "analysis"):
        m = OmegaConf.to_container(cfg.metric, resolve=False)
        if m.get("compute_binder_metrics") or m.get("compute_motif_binder_metrics"):
            method = m.get("binder_folding_method", "colabdesign")
            deps.add({"colabdesign": "af2", "rf3_latest": "rf3"}.get(method, method))
        if any(x != "self" for x in m.get("sequence_types", ["self"])):
            deps.add(m.get("inverse_folding_model", "protein_mpnn"))
        monomer = m.get("compute_monomer_metrics", False)
        motif = m.get("compute_motif_metrics", False)
        if monomer or motif:
            shared = m.get("monomer_folding_models", ["esmfold"])
            if m.get("compute_designability", monomer or motif):
                deps.add("protein_mpnn")
                deps.update(m.get("designability_folding_models", shared))
            if m.get("compute_codesignability", monomer or motif):
                deps.update(m.get("codesignability_folding_models", shared))
        if m.get("compute_esm_metrics"):
            deps.add("esm")
        for name in ("pdb", "afdb", "afdb_rep_v4", "afdb_rep_v4_geniefilters_maxlen512"):
            if m.get("compute_novelty_" + name):
                deps.add("novelty_" + name)
        for group in ("pre_refolding", "refolded"):
            enabled = m.get("compute_pre_refolding_metrics" if group == "pre_refolding" else "compute_refolded_structure_metrics")
            if enabled:
                deps.update(k for k, enabled in m.get(group, {}).items() if enabled)
        if request.operation in ("design", "analysis"):
            if cfg.get("aggregation", {}).get("compute_diversity"):
                deps.add("foldseek")
            if cfg.get("aggregation", {}).get("compute_mmseqs_diversity"):
                deps.add("mmseqs")
    return sorted(deps)
