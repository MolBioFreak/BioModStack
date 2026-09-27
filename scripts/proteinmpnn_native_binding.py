#!/usr/bin/env python3
"""Explicit author roles at dl_binder_design's feature/threading boundary.

Only generic ProteinMPNN requests use this adapter. Native sampling, scoring,
argparse, iteration and output names remain owned by the installed runner.
Feature axes retain native number gaps and insertion ordering; the pose is never
reordered or renumbered. Historical antibody calls bypass this adapter.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import sys

ROLE_REMARK = 'REMARK BMS_MPNN_ROLES '


def pdb_domain(path, *, text=None):
    """Exact ordered protein author identities supported by the native parser."""
    residues, atoms = {}, set()
    previous = None
    models = 0
    for line in (Path(path).read_text() if text is None else text).splitlines():
        if line.startswith('MODEL '):
            models += 1
            if models > 1:
                raise ValueError('multiple PDB models are ambiguous')
        if line.startswith('HETATM'):
            raise ValueError('generic protein design cannot preserve unrecognized HETATM identity')
        if not line.startswith('ATOM  '):
            continue
        if len(line) < 54 or line[16].strip():
            raise ValueError('malformed or alternate-location PDB atom')
        identity = (line[21], int(line[22:26]), line[26].strip())
        if not re.fullmatch('[A-Za-z0-9]', identity[0]) or (identity[2] and not identity[2].isalpha()):
            raise ValueError('native parser cannot represent this chain/residue identity')
        atom = identity, line[12:16].strip()
        if atom in atoms or (identity != previous and identity in residues):
            raise ValueError('duplicate or discontiguous PDB residue identity')
        if identity in residues and residues[identity] != line[17:20]:
            raise ValueError('ambiguous residue identity')
        atoms.add(atom)
        residues[identity] = line[17:20]
        previous = identity
    if not residues:
        raise ValueError('input PDB has no protein ATOM residues')
    return residues


def selected_chains(value, present, field):
    if value is None or value == '':
        return []
    parts = value.split(',') if isinstance(value, str) else value
    if not isinstance(parts, list) or any(not isinstance(c, str) or not re.fullmatch('[A-Za-z0-9]', c.strip()) for c in parts):
        raise ValueError(f'{field}: malformed chain selection')
    chains = list(dict.fromkeys(c.strip() for c in parts))
    if not set(chains) <= set(present):
        raise ValueError(f'{field}: absent chain selection')
    return chains


def fixed_residues(value, domain):
    if value is None or value == '':
        return set()
    if not isinstance(value, str):
        raise ValueError('fixed_positions must be an author-residue selection string')
    result = set()
    for token in value.split(','):
        match = re.fullmatch(r'([A-Za-z0-9]):(-?\d+)([A-Za-z]?)(?:-(-?\d+))?', token.strip())
        if not match:
            raise ValueError('malformed fixed_positions; expected a:10B or A:1-10')
        chain, start, insertion, end = match.groups()
        if end is None:
            selected = {(chain, int(start), insertion)} & set(domain)
        else:
            if insertion or int(start) > int(end):
                raise ValueError('invalid fixed_positions range')
            # A number-only interval selects blank insertion codes only.
            selected = {r for r in domain if r[0] == chain and int(start) <= r[1] <= int(end) and not r[2]}
        if not selected:
            raise ValueError('fixed_positions contains absent residues')
        result.update(selected)
    return result


def role_contract(path, request=None, *, data=None):
    path = Path(path)
    data = path.read_bytes() if data is None else data
    text = data.decode()
    domain = pdb_domain(path, text=text)
    request = request or {}
    chains = list(dict.fromkeys(c for c, _, _ in domain))
    design_value = request.get('design_chain')
    if design_value is None:
        design_value = request.get('binder_chain_ids')
    target_value = request.get('target_chain')
    if target_value is None:
        target_value = request.get('target_chain_ids')
    if design_value is None and len(chains) == 1:
        design_value = chains[0]
    design = selected_chains(design_value, chains, 'design_chain')
    target = selected_chains(target_value, chains, 'target_chain')
    if not design:
        raise ValueError('multi-chain ProteinMPNN requires explicit design_chain roles' if len(chains) > 1 else 'design_chain must select a present chain')
    if set(design) & set(target):
        raise ValueError('design_chain and target_chain overlap')
    fixed = fixed_residues(request.get('fixed_positions'), domain)
    # Preserve historical generic monomer FIXED author-number labels. A bare
    # label on a complex is resolved only when its author identity is unique.
    for line in text.splitlines():
        if 'PDBinfo-LABEL:' not in line or 'FIXED' not in line:
            continue
        match = re.fullmatch(r'REMARK\s+PDBinfo-LABEL:\s*(-?\d+)\s+FIXED\s*', line)
        matches = {r for r in domain if match and r[1] == int(match.group(1)) and not r[2]}
        if len(matches) != 1:
            raise ValueError('absent or ambiguous native FIXED residue label; use chain-qualified fixed_positions')
        fixed.update(matches)
    return dict(source_pdb=path.name, source_pdb_sha256=hashlib.sha256(data).hexdigest(),
                chains=chains, designed_chains=design, fixed_chains=[c for c in chains if c not in design],
                target_chains=target, fixed_positions=[list(r) for r in domain if r in fixed],
                source_residues=[list(r) for r in domain])


def prepare(path, output, request=None):
    data = Path(path).read_bytes()
    contract = role_contract(path, request, data=data)
    # Inline transport follows the existing PDB-only channel; no extra manifest
    # path has to survive task staging. Preserve the original ATOM bytes.
    Path(output).write_bytes((ROLE_REMARK + json.dumps(contract, separators=(',', ':')) + '\n').encode() + data)
    return contract


def feature_axes(identities):
    """Native parse_PDB_biounits order, including one slot per missing number."""
    result = {}
    for chain in dict.fromkeys(r[0] for r in identities):
        residues = {(n, ins): i + 1 for i, (c, n, ins) in enumerate(identities) if c == chain}
        by_number = {}
        for (number, insertion), pose_index in residues.items():
            by_number.setdefault(number, []).append((insertion, pose_index))
        result[chain] = [index for number in range(min(by_number), max(by_number) + 1)
                         for _, index in sorted(by_number.get(number, [('', None)]))]
    return result


def install(namespace):
    """Bind native classes after definition, before their unchanged main loop."""
    sample_cls = namespace['sample_features']
    runner_cls = namespace['ProteinMPNN_runner']
    manager_cls = namespace['StructManager']
    util = namespace['mpnn_util']
    core = namespace['core']
    native_init = sample_cls.__init__

    native_featurize = util.tied_featurize
    native_dump = manager_cls.dump_pose

    native_relax = runner_cls.relax_pose
    active = {}

    def sample_init(self, pose, tag):
        native_init(self, pose, tag)
        # Do not truncate source names at their first dot.
        self.tag = Path(tag).stem
        lines = [line[len(ROLE_REMARK):] for line in Path(tag).read_text().splitlines() if line.startswith(ROLE_REMARK)]
        self.bms_roles = json.loads(lines[0]) if lines else role_contract(tag)
        info = pose.pdb_info()
        self.bms_ids = [(info.chain(i), info.number(i), info.icode(i).strip()) for i in range(1, pose.total_residue() + 1)]
        if self.bms_ids != [tuple(r) for r in self.bms_roles['source_residues']]:
            raise ValueError('native pose changed source author identity/order')
        self.bms_axes = feature_axes(self.bms_ids)
        self.bms_fixed = {tuple(r) for r in self.bms_roles['fixed_positions']}
        self.bms_input_sequences = chain_sequences(self)
        active['sample'] = self

    def parse_fixed(self):
        self.chains = list(self.bms_roles['chains'])
        self.fixed_res = {chain: [i + 1 for i, pose_index in enumerate(axis)
                                 if pose_index is not None and self.bms_ids[pose_index - 1] in self.bms_fixed]
                          for chain, axis in self.bms_axes.items()}

    def featurize(*args, **kwargs):
        result = native_featurize(*args, **kwargs)
        # Read the actual returned masked-chain order, rather than reproducing
        # tied_featurize's sort in the threading path.
        sample = active['sample']
        order = list(result[8][0])
        lengths = list(result[9][0])
        if set(order) != set(sample.bms_roles['designed_chains']) or lengths != [len(sample.bms_axes[c]) for c in order]:
            raise ValueError('native feature axis differs from author mapping')
        sample.bms_thread_axis = [index for c in order for index in sample.bms_axes[c]]
        sample.bms_native_order = order
        return result


    def thread(self, sequence):
        if len(sequence) != len(self.bms_thread_axis):
            raise ValueError('native sequence length differs from feature axis')
        residue_set = self.pose.residue_type_set_for_pose(core.chemical.FULL_ATOM_t)
        for pose_index, aa in zip(self.bms_thread_axis, sequence):
            if pose_index is None or self.bms_ids[pose_index - 1] in self.bms_fixed:
                continue
            residue = core.conformation.ResidueFactory.create_residue(residue_set.name_map(util.aa_1_3[aa]))
            self.pose.replace_residue(pose_index, residue, True)


    def relax_pose(self, sample):
        if len(sample.chains) > 1:
            # Keep the installed binder protocol and its numerical settings;
            # replace only hardcoded A/B and chain 1/2 role selectors.
            import xml.etree.ElementTree as ET
            xml = ET.fromstring((Path(namespace['script_dir']) / 'RosettaFastRelaxUtil.xml').read_text())
            designed = set(sample.bms_roles['designed_chains'])
            for selector in xml.findall('./RESIDUE_SELECTORS/Chain'):
                if selector.get('name') in {'chainA', 'chainB'}:
                    is_design = selector.get('name') == 'chainA'
                    indices = [str(i + 1) for i, r in enumerate(sample.bms_ids) if (r[0] in designed) == is_design]
                    selector.tag = 'Index'
                    selector.attrib.pop('chains', None)
                    if indices:
                        selector.set('resnums', ','.join(indices))
                    else:
                        selector.tag = 'False'
            movemap = xml.find("./MOVERS/FastRelax[@name='FastRelax']/MoveMap")
            movemap.clear()
            movemap.set('name', 'MM')
            for chain_index in range(1, sample.pose.num_chains() + 1):
                chain = sample.pose.pdb_info().chain(sample.pose.chain_begin(chain_index))
                ET.SubElement(movemap, 'Chain', number=str(chain_index), chi='true', bb=str(chain in designed).lower())
            fold_tree = sample.pose.fold_tree()
            for jump in range(1, sample.pose.num_jump() + 1):
                endpoints = (fold_tree.upstream_jump_residue(jump), fold_tree.downstream_jump_residue(jump))
                movable = any(sample.bms_ids[i - 1][0] in designed for i in endpoints)
                ET.SubElement(movemap, 'Jump', number=str(jump), setting=str(movable).lower())
            objects = namespace['protocols'].rosetta_scripts.XmlObjects.create_from_string(ET.tostring(xml, encoding='unicode'))
            self.FastRelax_binder = objects.get_mover('FastRelax')
        return native_relax(self, sample)

    def dump_pose(self, pose, tag, sequence=None, score=None):
        native_dump(self, pose, tag, sequence, score)
        if self.pdb and sequence is not None and score is not None:
            sample = active['sample']
            path = Path(self.outpdbdir) / f'mpnn_{tag}.json'
            payload = json.loads(path.read_text())
            payload.update({k: sample.bms_roles[k] for k in ('source_pdb', 'source_pdb_sha256', 'designed_chains', 'fixed_chains', 'target_chains')})
            payload['input_chain_sequences'] = sample.bms_input_sequences
            payload['chain_sequences'] = chain_sequences(sample, pose)
            payload['native_designed_chain_order'] = sample.bms_native_order
            payload['residue_mapping'] = [dict(chain=c, number=n, insertion=ins, pose_index=i + 1,
                feature_index=sample.bms_axes[c].index(i + 1) + 1) for i, (c, n, ins) in enumerate(sample.bms_ids)]
            # Use the upstream producer correspondence dialect for either role
            # transport, retaining native feature indices separately above.
            payload['source_input_tag'] = Path(sample.bms_roles['source_pdb']).stem
            payload['native_output_tag'] = tag
            payload['source_structure_sha256'] = sample.bms_roles['source_pdb_sha256']
            payload['binder_chains'] = sample.bms_roles['designed_chains']
            payload['designed_chain_sequences'] = {
                c: payload['chain_sequences'][c] for c in sample.bms_roles['designed_chains']}
            info = pose.pdb_info()
            payload['source_residue_mapping'] = [dict(
                source=dict(chain_id=c, auth_seq_id=n, insertion_code=ins),
                output=dict(chain_id=info.chain(i), auth_seq_id=int(info.number(i)),
                            insertion_code=str(info.icode(i)).strip()))
                for i, (c, n, ins) in enumerate(sample.bms_ids, 1)]
            # filter_mpnn.py consumes one complete JSON object per line.
            path.write_text(json.dumps(payload) + '\n')

    sample_cls.__init__ = sample_init
    sample_cls.parse_fixed_res = parse_fixed
    sample_cls.thread_mpnn_seq = thread

    runner_cls.relax_pose = relax_pose
    manager_cls.dump_pose = dump_pose
    util.tied_featurize = featurize


def chain_sequences(sample, pose=None):
    pose = pose if pose is not None else sample.pose
    sequence = pose.sequence()
    return {c: ''.join(sequence[i] for i, r in enumerate(sample.bms_ids) if r[0] == c)
            for c in sample.bms_roles['chains']}


def instrument_source(source):
    """Change only role selection and install boundary hooks in native main."""
    tree = ast.parse(source)
    runner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ProteinMPNN_runner')
    optimize = next(n for n in runner.body if isinstance(n, ast.FunctionDef) and n.name == 'sequence_optimize')
    for i, node in enumerate(optimize.body):
        if isinstance(node, ast.If) and any(isinstance(n, ast.Name) and n.id == 'masked_chains' for n in ast.walk(node)):
            optimize.body[i:i + 1] = ast.parse("masked_chains = list(sample_feats.bms_roles['designed_chains'])\nvisible_chains = list(sample_feats.bms_roles['fixed_chains'])").body
            break
    else:
        raise ValueError('native ProteinMPNN role selection boundary not found')
    # Installed no-cycle relax_output branch references undefined seq_idx.
    for node in ast.walk(runner):
        if isinstance(node, ast.FunctionDef) and node.name == 'proteinmpnn':
            for child in ast.walk(node):
                if isinstance(child, ast.Name) and child.id == 'seq_idx':
                    child.id = 'idx'
        if isinstance(node, ast.FunctionDef) and node.name == 'proteinmpnn_fastrelax':
            # The installed intermediate writer passes a pre-thread clone with
            # the post-thread sequence/score. Publish the actual threaded pose.
            for child in ast.walk(node):
                if (isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                        and child.func.attr == 'dump_pose' and child.args
                        and isinstance(child.args[0], ast.Name) and child.args[0].id == 'current_pose'):
                    child.args[0] = ast.parse('sample_feats.pose', mode='eval').body
    for i, node in enumerate(tree.body):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'struct_manager' for t in node.targets):
            tree.body.insert(i, ast.parse('_bms_install(globals())').body[0])
            break
    else:
        raise ValueError('native ProteinMPNN main boundary not found')
    return ast.fix_missing_locations(tree)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native', required=True)
    parser.add_argument('native_args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    entry = Path(args.native).resolve()
    argv = args.native_args[1:] if args.native_args[:1] == ['--'] else args.native_args
    sys.path.insert(0, str(entry.parent))
    sys.argv = [str(entry), *argv]
    exec(compile(instrument_source(entry.read_text()), str(entry), 'exec'),
         {'__name__': '__main__', '__file__': str(entry), '_bms_install': install})


if __name__ == '__main__':
    main()
