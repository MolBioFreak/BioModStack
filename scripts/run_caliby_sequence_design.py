#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from caliby_runtime import (
    collect_structure_paths,
    load_caliby_model,
    load_constraints_dataframe,
    maybe_clean_inputs,
    maybe_run_self_consistency,
    normalize_sampling_results,
    parse_omit_aas,
    parse_bool,
    preflight_caliby_runtime,
    remap_constraint_dataframe_to_cleaned_paths,
)


def clean_pdb_with_correspondence(pdb_path, out_dir):
    """Observe the native cleaner's actual chain relabeling and CIF writer.

    This callable is joblib-serializable: each worker loads the same native
    function, substituting only its serialization observer. Science is unchanged.
    """
    import ast
    import importlib
    import inspect
    import warnings
    import gemmi
    module = importlib.import_module('caliby.data.preprocessing.atomworks.clean_pdbs')
    tree = ast.parse(Path(module.__file__).read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'clean_pdb')
    namespace = dict(vars(module))
    writer = module.to_cif_string
    captured = {}
    def observe(array, *args, **kwargs):
        text = writer(array, *args, **kwargs)
        try:
            # These pairs are created by the native cleaner from the surviving
            # residue objects, before it assigns sequential chain labels.
            labels = inspect.currentframe().f_back.f_locals['pair_to_label']
            inverse = {label: pair[0] for pair, label in labels.items()}
            originals = structure_residues(pdb_path)
            if Path(pdb_path).suffix.lower() != '.pdb' or any(r[2] for r in originals):
                return text  # author/label or insertion-code loss is not inferred
            block = gemmi.cif.read_string(text).sole_block()
            table = block.find('_atom_site.', ['label_asym_id', 'label_seq_id', 'auth_asym_id', 'auth_seq_id'])
            mapping = {}
            for label_chain, label_num, auth_chain, auth_num in table:
                original = (inverse[auth_chain], int(auth_num), '')
                # PDB parsing preserves author res_id; CIF parsing uses label
                # res_id. Bind both at this writer, not by residue ordering.
                if original not in originals:
                    continue
                mapping[(label_chain, int(label_num), '')] = original
            captured.update(mapping=[dict(source=residue_id(v), output=residue_id(k))
                                     for k, v in mapping.items()])
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            warnings.warn(f'Caliby cleaning correspondence unavailable: {exc}')
        return text
    namespace['to_cif_string'] = observe
    exec(compile(ast.Module(body=[function], type_ignores=[]), module.__file__, 'exec'), namespace)
    result = namespace['clean_pdb'](pdb_path, out_dir)
    if 'mapping' in captured:
        try:
            Path(str(result) + '.source_mapping.json').write_text(json.dumps(captured['mapping']))
        except OSError as exc:
            warnings.warn(f'Caliby cleaning correspondence unavailable: {exc}')
    return result


def residue_id(value):
    return dict(chain_id=value[0], auth_seq_id=int(value[1]), insertion_code=value[2])


def structure_residues(path):
    import gemmi
    structure = gemmi.read_structure(str(path))
    return {(c.name, r.seqid.num, r.seqid.icode.strip()) for c in structure[0]
            for r in c if gemmi.find_tabulated_residue(r.name).is_amino_acid()}


def source_correspondence(original_paths, cleaned_paths):
    """Native clean_pdbs explicitly returns one path per input in input order."""
    import hashlib
    evidence = {}
    for original, cleaned in zip(original_paths, cleaned_paths):
        original, cleaned = Path(original), Path(cleaned)
        mapping = None
        sidecar = Path(str(cleaned) + '.source_mapping.json')
        try:
            if sidecar.exists():
                mapping = json.loads(sidecar.read_text())
            elif original.read_bytes() == cleaned.read_bytes():
                # No preprocessing occurred (also used by inert native fixtures).
                mapping = [dict(source=residue_id(r), output=residue_id(r))
                           for r in sorted(structure_residues(original))]
        except (OSError, ValueError, RuntimeError):
            mapping = None
        evidence[cleaned.stem] = dict(source_structure_sha256=hashlib.sha256(original.read_bytes()).hexdigest(),
                                      source_residue_mapping=mapping)
    return evidence


def annotate_native_outputs(manifest, results, binder_chains='', target_chains='', sources=None):
    """Bind every returned record to its own native designed CIF/PDB bytes."""
    import hashlib
    import gemmi
    binders = [c.strip() for c in binder_chains.split(',') if c.strip()]
    targets = [c.strip() for c in target_chains.split(',') if c.strip()]
    for item, native_path in zip(manifest, results['out_pdb']):
        source = Path(native_path)
        structure = gemmi.read_structure(str(source))
        sequences = {}
        mapping = []
        for chain in structure[0]:
            for residue in chain:
                info = gemmi.find_tabulated_residue(residue.name)
                if not info.is_amino_acid():
                    continue
                sequences[chain.name] = sequences.get(chain.name, '') + info.one_letter_code
                mapping.append(dict(chain_id=chain.name, author_number=residue.seqid.num,
                                    insertion_code=residue.seqid.icode.strip()))
        metadata_path = Path(item['metadata_path'])
        # Preserve the exact returned native document alongside the derivative
        # PDB. A relative identity survives Nextflow publication and remote return.
        native_bytes = source.read_bytes()
        relative_path = Path('native_outputs') / f"{item['design_id']}{source.suffix}"
        published = metadata_path.parent / relative_path
        published.parent.mkdir(parents=True, exist_ok=True)
        published.write_bytes(native_bytes)
        identity = dict(path=str(published), relative_path=relative_path.as_posix(),
                        source_path=str(source), sha256=hashlib.sha256(native_bytes).hexdigest(),
                        example_id=item['example_id'])
        record = json.loads(metadata_path.read_text())
        record.update(chain_sequences=sequences, native_output_structure=identity, residue_mapping=mapping)
        evidence = (sources or {}).get(str(item['example_id']), {})
        join = evidence.get('source_residue_mapping')
        # Native sidechain_pack copies orig_toks.chain_id/res_id to output
        # AtomArrays. Those tokens came from the cleaned CIF's label identifiers.
        emitted = {(r['chain_id'], r['author_number'], r['insertion_code']) for r in mapping}
        try:
            join = [r for r in join if (r['output']['chain_id'], r['output']['auth_seq_id'],
                                       r['output']['insertion_code']) in emitted] if join is not None else None
        except (KeyError, TypeError):
            join = None
        record.update(source_structure_sha256=evidence.get('source_structure_sha256'),
                      source_residue_mapping=join)
        if binders:
            record.update(input_binder_chains=binders, input_target_chains=targets)
            output_binders, output_targets = binders, targets
            if join is not None:
                def project(chains):
                    return list(dict.fromkeys(r['output']['chain_id'] for r in join
                                              if r['source']['chain_id'] in chains))
                output_binders, output_targets = project(binders), project(targets)
                record['chain_roles_namespace'] = 'output'
            record.update(binder_chains=output_binders, target_chains=output_targets,
                          designed_chain_sequences={c: sequences[c] for c in output_binders if c in sequences})
        metadata_path.write_text(json.dumps(record, indent=2))
        item['native_output_structure'] = identity
        item['chain_sequences'] = sequences


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Caliby sequence design on a directory of antibody/nanobody candidate structures.")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--model-name", default="soluble_caliby_v1")
    parser.add_argument("--num-seqs-per-pdb", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--num-workers", type=int, default=8)
    parser.add_argument("--clean-num-workers", type=int, default=2)
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--omit-aas", default="C")
    parser.add_argument("--pos-constraint-csv", default="")
    parser.add_argument("--sampling-overrides-json", default="")
    parser.add_argument("--run-self-consistency-eval", type=parse_bool, default=False)
    parser.add_argument("--self-consistency-num-models", type=int, default=5)
    parser.add_argument("--self-consistency-num-recycles", type=int, default=3)
    parser.add_argument("--self-consistency-use-multimer", type=parse_bool, default=False)
    parser.add_argument("--source-identity-json", default="")
    parser.add_argument("--binder-chains", default="")
    parser.add_argument("--target-chains", default="")
    args = parser.parse_args()

    input_dir = Path(args.input_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    raw_pdb_dir = output_dir
    raw_meta_dir = output_dir
    raw_pdb_dir.mkdir(parents=True, exist_ok=True)
    raw_meta_dir.mkdir(parents=True, exist_ok=True)

    pdb_paths = collect_structure_paths(input_dir)
    if not pdb_paths:
        raise ValueError("Caliby sequence design requires at least one selected structure")
    if args.num_seqs_per_pdb < 1 or args.batch_size < 1 or args.num_workers < 1 or args.clean_num_workers < 1:
        raise ValueError("Caliby sample counts and worker counts must be positive")
    preflight_caliby_runtime(task="sequence_design", model_name=args.model_name)
    import hashlib
    source_hashes = {str(p): hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in pdb_paths}
    # Preserve native clean_pdbs scheduling/return order, including joblib workers.
    import importlib
    try:
        cleaner = importlib.import_module('caliby.data.preprocessing.atomworks.clean_pdbs')
    except ImportError:
        cleaner = None
    original_clean = cleaner.clean_pdb if cleaner is not None else None
    if cleaner is not None:
        cleaner.clean_pdb = clean_pdb_with_correspondence
    try:
        cleaned = maybe_clean_inputs(
            pdb_paths=pdb_paths,
            cleaned_dir=output_dir / "cleaned_pdbs",
            num_workers=args.clean_num_workers,
        )
    finally:
        if cleaner is not None:
            cleaner.clean_pdb = original_clean
    sources = source_correspondence(pdb_paths, cleaned)
    for original, prepared in zip(pdb_paths, cleaned):
        sources[Path(prepared).stem]['source_structure_sha256'] = source_hashes[str(original)]
    model = load_caliby_model(args.model_name)
    pos_constraint_df = load_constraints_dataframe(Path(args.pos_constraint_csv).resolve()) if args.pos_constraint_csv else None
    pos_constraint_df = remap_constraint_dataframe_to_cleaned_paths(
        pos_constraint_df,
        original_paths=pdb_paths,
        cleaned_paths=cleaned,
        sources=sources,
    )
    sampling_overrides = json.loads(args.sampling_overrides_json) if args.sampling_overrides_json.strip() else {}
    results = model.sample(
        cleaned,
        out_dir=str(output_dir / "designed"),
        num_seqs_per_pdb=args.num_seqs_per_pdb,
        batch_size=args.batch_size,
        omit_aas=parse_omit_aas(args.omit_aas),
        num_workers=args.num_workers,
        temperature=args.temperature,
        pos_constraint_df=pos_constraint_df,
        sampling_overrides=sampling_overrides,
    )
    self_consistency = maybe_run_self_consistency(
        model=model,
        designed_paths=list(results.get("out_pdb", [])),
        output_dir=output_dir / "self_consistency",
        enabled=bool(args.run_self_consistency_eval),
        num_models=max(1, args.self_consistency_num_models),
        num_recycles=max(1, args.self_consistency_num_recycles),
        use_multimer=bool(args.self_consistency_use_multimer),
    )

    manifest = normalize_sampling_results(
        results=results,
        output_pdb_dir=output_dir,
        output_meta_dir=output_dir,
        prefix="caliby",
        source="caliby",
        stage_mode="sequence_design",
        extra_metadata={"caliby_model": args.model_name, "validation_status": "unvalidated",
                        "terminal_producer": "caliby",
                        "effective_settings": {key: value for key, value in vars(args).items()
                                               if key not in {"input_dir", "output_dir"}}},
        self_consistency=self_consistency,
    )

    annotate_native_outputs(manifest, results, args.binder_chains, args.target_chains, sources)

    if args.source_identity_json:
        sources = json.loads(Path(args.source_identity_json).read_text())
        by_native_key = {Path(row["staged_name"]).stem: row for row in sources}
        # Native Caliby sets example_id = Path(input_pdb).stem at its dataset
        # producer. Cleaning preserves that key (existing constraint owner).
        for item in manifest:
            metadata_path = Path(item["metadata_path"])
            record = json.loads(metadata_path.read_text())
            source = by_native_key.get(str(item["example_id"]))
            record["selected_source"] = source
            if source:
                source_meta = source.get("source_meta") or {}
                record["source_document_id"] = source_meta.get("id")
                record["source_meta"] = source_meta
            metadata_path.write_text(json.dumps(record, indent=2))
        Path("caliby_selection.json").write_text(json.dumps(sources, indent=2))

    (output_dir / "caliby_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    jsonl_path = output_dir / "caliby_metadata.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for item in manifest:
            metadata_path = Path(str(item["metadata_path"]))
            handle.write(metadata_path.read_text(encoding="utf-8").strip())
            handle.write("\n")


if __name__ == "__main__":
    main()
