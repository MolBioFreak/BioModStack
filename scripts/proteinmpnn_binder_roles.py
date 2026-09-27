#!/usr/bin/env python3
"""Explicit chain-role boundary for the installed dl_binder_design runner.

Native model loading, sampling, scores, relaxation and CLI remain native. Only
role masks, author-to-feature indexing, sequence threading and output identity
are adapted. No input chain reordering, antibody convention or last-chain role.
The unmarked historical runner is not changed.
"""
from __future__ import annotations

import argparse
import ast
import base64
import json
from pathlib import Path
import sys
import tempfile


def role_selection(domain, request):
    if __package__:
        from .prep_fampnn_constraints_generic import selected_chains, fixed_residues
    else:
        from prep_fampnn_constraints_generic import selected_chains, fixed_residues
    binders = selected_chains(request.get('binder_chains'), domain, 'binder_chains')
    targets = selected_chains(request.get('target_chains'), domain, 'target_chains')
    if not binders or not targets:
        raise ValueError('explicit ProteinMPNN binder and target chains are required')
    if binders & targets:
        raise ValueError('binder and target chains overlap')
    return sorted(binders), sorted(targets), fixed_residues(request.get('fixed_positions'), domain)


def install(native, request):
    """Bind roles to native objects before their ordinary execution loop."""
    Features = native['sample_features']
    Runner = native['ProteinMPNN_runner']
    Manager = native['StructManager']
    util = native['mpnn_util']
    original_load = Manager.load_pose
    original_dump = Manager.dump_pose
    original_thread = Features.thread_mpnn_seq

    def load_pose(manager, source):
        pose = original_load(manager, source)
        manager.role_source = str(source)
        manager.role_source_tag = Path(source).stem
        # Standalone generic preparation is a byte-preserving copy. Capture at
        # the native load, not a controller/worker path comparison.
        import hashlib
        manager.role_source_sha256 = hashlib.sha256(Path(source).read_bytes()).hexdigest()
        info = pose.pdb_info()
        manager.role_source_residues = {
            i: dict(chain_id=info.chain(i), auth_seq_id=int(info.number(i)),
                    insertion_code=str(info.icode(i)).strip())
            for i in range(1, pose.total_residue() + 1)
        }
        return pose

    def parse_fixed_res(sample):
        pose = sample.pose
        info = pose.pdb_info()
        identities = [(info.chain(i), info.number(i)) for i in range(1, pose.total_residue() + 1)]
        sample.chains = list(dict.fromkeys(c for c, _ in identities))
        sample.binders, sample.targets, selected = role_selection(dict.fromkeys(identities), request)
        sample.role_indices = {c: [i for i, (chain, _) in enumerate(identities, 1) if chain == c]
                               for c in sample.chains}
        sample.fixed_res = {c: [] for c in sample.chains}
        sample.residue_mapping = []
        for chain, indices in sample.role_indices.items():
            for local, index in enumerate(indices, 1):
                labels = [str(label).strip() for label in info.get_reslabels(index)]
                if identities[index - 1] in selected or 'FIXED' in labels:
                    sample.fixed_res[chain].append(local)
                sample.residue_mapping.append({'chain_id': chain, 'author_number': info.number(index),
                                               'insertion_code': str(info.icode(index)).strip(),
                                               'pose_index': index, 'feature_index': local})

    def sequence_optimize(runner, sample, num_seqs=None):
        # Native parsing fills author-number gaps with artificial residues. Use
        # a clone with explicit contiguous chain-local numbering for features,
        # retaining the original pose and author identity for threading/output.
        feature_pose = sample.pose.clone()
        info = feature_pose.pdb_info()
        for entry in sample.residue_mapping:
            info.number(entry['pose_index'], entry['feature_index'])
            info.icode(entry['pose_index'], ' ')
        with tempfile.TemporaryDirectory(prefix='mpnn-features-', dir='.') as directory:
            pdb = Path(directory) / 'features.pdb'
            feature_pose.dump_pdb(str(pdb))
            features = util.generate_seqopt_features(str(pdb), sample.chains)
        args = util.set_default_args(runner.seqs_per_struct if num_seqs is None else num_seqs,
                                     omit_AAs=runner.omit_AAs)
        args['temperature'] = runner.temperature
        # tied_featurize sorts masked chains. Thread the returned concatenation
        # in that exact order, not pose order or caller selection order.
        visible = sorted(set(sample.chains) - set(sample.binders))
        return util.generate_sequences(runner.mpnn_model, runner.device, features, args,
                                       list(sample.binders), visible,
                                       bias_AAs_np=runner.bias_AAs_np,
                                       fixed_positions_dict={features['name']: sample.fixed_res})

    def thread_mpnn_seq(sample, sequence):
        indices = [i for c in sample.binders for i in sample.role_indices[c]]
        if len(sequence) != len(indices):
            raise ValueError('native designed sequence length does not match explicit binder mapping')
        # Reuse native residue construction without its implicit pose prefix.
        # The small proxy redirects only replace_residue to the exact pose index.
        class MappedPose:
            def residue_type_set_for_pose(self, *args):
                return sample.pose.residue_type_set_for_pose(*args)

            def replace_residue(self, index, residue, orient):
                return sample.pose.replace_residue(indices[index - 1], residue, orient)
        from types import SimpleNamespace
        original_thread(SimpleNamespace(pose=MappedPose()), sequence)
        native['struct_manager'].role_sample = sample

    def dump_pose(manager, pose, tag, sequence=None, score=None):
        original_dump(manager, pose, tag, sequence, score)
        if not manager.pdb or sequence is None or score is None:
            return
        sample = manager.role_sample
        sequences = {c: ''.join(pose.residue(i).name1() for i in indices)
                     for c, indices in sample.role_indices.items()}
        path = Path(manager.outpdbdir) / f'mpnn_{tag}.json'
        record = json.loads(path.read_text())
        record.update(binder_chains=sample.binders, target_chains=sample.targets,
                      chain_sequences=sequences,
                      designed_chain_sequences={c: sequences[c] for c in sample.binders},
                      residue_mapping=sample.residue_mapping,
                      source_input_tag=manager.role_source_tag,
                      source_input_path=manager.role_source,
                      native_output_tag=tag,
                      source_structure_sha256=manager.role_source_sha256,
                      source_residue_mapping=[
                          dict(source=identity, output=dict(
                              chain_id=pose.pdb_info().chain(i),
                              auth_seq_id=int(pose.pdb_info().number(i)),
                              insertion_code=str(pose.pdb_info().icode(i)).strip()))
                          for i, identity in manager.role_source_residues.items()
                      ])
        path.write_text(json.dumps(record) + '\n')

    def proteinmpnn(runner, sample):
        # Same native no-cycle loop, correcting its undefined seq_idx in the
        # optional relax_output log. Sampling and relax operations stay native.
        for index, (sequence, score) in enumerate(runner.sequence_optimize(sample)):
            sample.thread_mpnn_seq(sequence)
            if native['args'].relax_output:
                runner.relax_pose(sample)
            runner.struct_manager.dump_pose(sample.pose, f'{sample.tag}_seq_{index}', sequence, score)

    Manager.load_pose = load_pose
    Manager.dump_pose = dump_pose
    Features.parse_fixed_res = parse_fixed_res
    Features.thread_mpnn_seq = thread_mpnn_seq
    Runner.sequence_optimize = sequence_optimize
    Runner.proteinmpnn = proteinmpnn


def run(native_script, request, argv):
    """Execute the native parser/definitions, install the boundary, then main."""
    path = Path(native_script).resolve()
    tree = ast.parse(path.read_text(), filename=str(path))
    split = next(i for i, node in enumerate(tree.body)
                 if isinstance(node, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == 'struct_manager' for t in node.targets))
    namespace = {'__name__': '__main__', '__file__': str(path)}
    sys.path.insert(0, str(path.parent))
    sys.argv = [str(path), *argv]
    exec(compile(ast.Module(body=tree.body[:split], type_ignores=[]), str(path), 'exec'), namespace)
    install(namespace, request)
    exec(compile(ast.Module(body=tree.body[split:], type_ignores=[]), str(path), 'exec'), namespace)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-script', default='/dl_binder_design/mpnn_fr/dl_interface_design_multi.py')
    parser.add_argument('--request-base64', required=True)
    parser.add_argument('native_args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    argv = args.native_args[1:] if args.native_args[:1] == ['--'] else args.native_args
    run(args.native_script, json.loads(base64.b64decode(args.request_base64, validate=True)), argv)


if __name__ == '__main__':
    main()
