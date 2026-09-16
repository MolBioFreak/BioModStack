"""Source-bound RFantibody role export. No biological inference from PDB letters.

The supported native get_chain_idx maps AbPose.H/L/T objects to writer labels;
the writer uses global one-based positions and exports get_loop_map positions.
Only exact inspected source bytes authorize that interpretation. An overlay with
other bytes cannot emit this record. This is source binding, not image qualification.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

NATIVE_SOURCES = {
    'scripts/rfdiffusion_inference.py': 'fe66312248ba280e6bb05fa66677a6eb96dd8206679ce7d0d9ad5760a9495aca',
    'src/rfantibody/util/io.py': '8aa179cfdf4cb84092308e57cd2b43c8e8cde9074c072bb2f089ab0a5c9aab19',
    'src/rfantibody/rfdiffusion/inference/model_runners.py': '1cbfddc72b257ba1b7bec3b28c8712f37e7eca5ff0d93da92da60033ed8d8c65',
    'src/rfantibody/rfdiffusion/inference/ab_pose.py': '215aea008dba95547a294988965b91416e19cf60739a45647c93691e3f77c917',
    'src/rfantibody/rfdiffusion/parsers.py': '607fbf934c643a8a5da975eb1e7a2f98fd15a2a3ee796f4b057dfae00d1400e1',
}
PREFIX = 'REMARK 950 BMS_RFANTIBODY '


def body_bytes(lines):
    return '\n'.join(line for line in lines if not line.startswith(PREFIX)).encode()


def domain(lines):
    values = []
    for line in lines:
        if line.startswith('ATOM  '):
            value = f'{line[21]}:{int(line[22:26])}:{line[26].strip()}'
            if value not in values:
                values.append(value)
    return values


def source_snapshot(path):
    raw = Path(path).read_bytes()
    return dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(),
                content=raw.decode(), domain=domain(raw.decode().splitlines()))


def input_sources(framework, target):
    return {role: source_snapshot(path)
            for role, path in [('framework', framework), ('target', target)]}


def admission_inputs(params):
    """Snapshot only existing operator inputs; never a future producer output."""
    return dict(
        framework=source_snapshot(params['framework_pdb']) if params.get('framework_pdb') else None,
        target=source_snapshot(params['target_pdb']) if params.get('target_pdb') else None,
        target_transform=dict(owner='normalize_target_pdb:v1',
            chains=params.get('antigen_chains') or '', model_number=params.get('target_model_number')))


def validate_input_sources(recipe, proof):
    """Replay the existing deterministic input owner against admitted bytes.

    Native task paths can differ from API paths. Paths alone never confer roles;
    the actual native snapshots must equal the declared input/controlled transform.
    """
    import tempfile
    from normalize_target_pdb import normalize_pdb, parse_chain_set
    admitted = recipe.get('inputs')
    if not isinstance(admitted, dict) or admitted.get('target') is None:
        raise ValueError('missing authoritative antibody input snapshots')
    for role in ('framework', 'target'):
        actual = proof['input_sources'][role]
        raw = actual.get('content', '').encode()
        if (hashlib.sha256(raw).hexdigest() != actual.get('sha256')
                or domain(raw.decode().splitlines()) != actual.get('domain')):
            raise ValueError('native source snapshot mismatch')
        expected = admitted[role]
        if expected is None:
            presets = {'standard-fv':'hu-4D5-8_Fv.pdb', 'nanobody':'h-NbBCII10.pdb'}
            preset = presets.get(recipe['settings']['framework_type'])
            if role != 'framework' or not preset or actual['path'] != '/opt/RFantibody/scripts/examples/example_inputs/' + preset or not actual['domain']:
                raise ValueError('framework preset source conflicts with declaration')
            continue
        original = expected['content'].encode()
        if hashlib.sha256(original).hexdigest() != expected['sha256']:
            raise ValueError('admitted input snapshot mismatch')
        if raw == original:
            continue
        if role != 'target':
            raise ValueError('framework source differs from authoritative parent input')
        transform = admitted['target_transform']
        if transform.get('owner') != 'normalize_target_pdb:v1':
            raise ValueError('unsupported target input transform')
        with tempfile.TemporaryDirectory(prefix='bms-antibody-input-') as directory:
            source, output = Path(directory)/'source.pdb', Path(directory)/'normalized.pdb'
            source.write_bytes(original)
            normalize_pdb(source, output, parse_chain_set(transform['chains']),
                not bool(transform['model_number']), model_number=transform['model_number'])
            if raw != output.read_bytes():
                raise ValueError('target source differs from authoritative parent input transform')


def native_export(lines, chain_idx, loop_map, source_hashes, sources=None):
    if source_hashes != NATIVE_SOURCES:
        raise ValueError('unverified RFantibody source binding')
    lines = '\n'.join(lines).splitlines()
    identities = domain(lines)
    expected = [f'{chain}:{index}:' for index, chain in enumerate(chain_idx, 1)]
    if identities != expected or not identities or set(chain_idx) - {'H', 'L', 'T'}:
        raise ValueError('native writer residue identity mismatch')
    roles = {role: [identity for identity, chain in zip(identities, chain_idx) if chain == key]
             for role, key in [('heavy','H'), ('light','L'), ('target','T')]}
    cdrs = []
    loops = {}
    for loop, positions in loop_map.items():
        loop = loop.upper()
        if loop not in {'H1','H2','H3','L1','L2','L3'}:
            raise ValueError('unsupported native CDR loop')
        selected = []
        for position in positions:
            if type(position) is not int or not 1 <= position <= len(identities):
                raise ValueError('invalid native CDR position')
            identity = identities[position - 1]
            if identity not in roles['heavy' if loop.startswith('H') else 'light']:
                raise ValueError('native CDR role mismatch')
            selected.append(identity)
        loops[loop] = selected
        cdrs.extend(selected)
    if len(set(cdrs)) != len(cdrs):
        raise ValueError('overlapping native CDR positions')
    proof = dict(schema_version=1, source='rfantibody_native_export_v1',
                 source_hashes=dict(source_hashes), input_sources=deepcopy(sources), roles=roles, cdrs=cdrs, loops=loops,
                 body_sha256=hashlib.sha256(body_bytes(lines)).hexdigest())
    return lines + [PREFIX + json.dumps(proof, sort_keys=True)]


def read_export(path):
    lines = Path(path).read_bytes().decode().splitlines()
    records = [line[len(PREFIX):] for line in lines if line.startswith(PREFIX)]
    if not records:
        return None
    if len(records) != 1:
        raise ValueError('duplicate antibody export provenance')
    proof = json.loads(records[0])
    if proof.get('source_hashes') != NATIVE_SOURCES or proof.get('source') != 'rfantibody_native_export_v1':
        raise ValueError('unverified antibody export source')
    if proof.get('body_sha256') != hashlib.sha256(body_bytes(lines)).hexdigest():
        raise ValueError('antibody export bytes changed without transform provenance')
    roles = [v for group in proof['roles'].values() for v in group]
    if len(roles) != len(set(roles)) or set(roles) != set(domain(lines)):
        raise ValueError('antibody role coverage mismatch')
    return proof


def carry_export(source, output, pairs):
    proof = read_export(source)
    if proof is None:
        return
    proof = deepcopy(proof)
    mapping = dict(pairs)
    if 'native_origin' not in proof:
        proof['native_origin'] = dict(sha256=hashlib.sha256(Path(source).read_bytes()).hexdigest(),
            pairs=[[v, v] for v in domain(Path(source).read_text().splitlines())])
    proof['native_origin']['pairs'] = [[native, mapping[current]]
        for native, current in proof['native_origin']['pairs']]
    for key in ('roles','loops'):
        proof[key] = {role: [mapping[v] for v in values] for role, values in proof[key].items()}
    proof['cdrs'] = [mapping[v] for v in proof['cdrs']]
    lines = Path(output).read_bytes().decode().splitlines()
    lines = [line for line in lines if not line.startswith(PREFIX)]
    proof['body_sha256'] = hashlib.sha256(body_bytes(lines)).hexdigest()
    Path(output).write_text('\n'.join(lines + [PREFIX + json.dumps(proof, sort_keys=True)]))
    read_export(output)


def record_constraints(source, receipt_path, fixed, mode, loops, protect, lock_framework, lock_target):
    """Capture the existing constraint owner's actual result at its transform."""
    proof = read_export(source)
    receipt = json.loads(Path(receipt_path).read_text())
    if hashlib.sha256(Path(source).read_bytes()).hexdigest() != receipt['source_pdb_sha256']:
        raise ValueError('constraint input differs from preparation source')
    mapping = dict(receipt['pairs'])
    antibody = proof['roles']['heavy'] + proof['roles']['light']
    selected = [v for loop, values in proof['loops'].items() for v in values
                if mode != 'cdr_selective' or loop in loops]
    if mode in {'framework_allowed', 'full_design'} or not lock_framework:
        authorized = antibody
        if mode == 'cdr_selective':
            authorized = [v for v in authorized if v not in proof['cdrs'] or v in selected]
    else:
        authorized = selected
    fixed_ids = [v for v in receipt['source_domain'] if int(v.split(':')[1]) in fixed.get(v.split(':')[0], [])]
    receipt['antibody_constraints'] = dict(
        settings=dict(antibody_design_mode=mode, antibody_design_loops=','.join(loops),
                      protect_vhh_tetrad=protect, lock_antibody_framework=lock_framework,
                      lock_target_chains=lock_target),
        sequence_design=[mapping[v] for v in authorized],
        mutation=[mapping[v] for v in selected if v not in fixed_ids],
        fixed=[mapping[v] for v in fixed_ids])
    Path(receipt_path).write_text(json.dumps(receipt, sort_keys=True) + '\n')


def verify_parent_preparation(parent, source, receipt):
    """Resolve candidates from the existing parent's publication authority.

    An adjacent request file is not an owner. Only the parent's synchronously
    published PrepFAMPNN inputs can be selected, and their native role origin must
    resolve into that same parent's RFantibody collection.
    """
    recipe = parent.provenance['fampnn_analysis_declaration']['materialization']
    if admission_inputs(parent.params) != recipe['inputs']:
        raise ValueError('current parent inputs differ from admitted source snapshots')
    root = Path(parent.output_dir).resolve()
    expected_dir = root/'prep'/'fampnn'
    source = Path(source)
    if source.is_symlink() or source.resolve().parent != expected_dir:
        raise ValueError('FA-MPNN input is not owned by the scientific parent')
    sidecar = source.with_suffix('.fampnn_prep.json')
    if sidecar.is_symlink() or sidecar.resolve().parent != expected_dir:
        raise ValueError('FA-MPNN receipt is not owned by the scientific parent')
    proof = read_export(source)
    if proof is None:
        raise ValueError('missing parent native role provenance')
    origin = proof.get('native_origin')
    native_hash = origin['sha256'] if origin else receipt['source_pdb_sha256']
    # This is the current parent's producer collection, not a caller-supplied
    # directory or a global search by basename/hash.
    candidates = list((root/'collected'/'rfantibody_raw').glob('*.pdb'))
    candidates += list((root/'prep'/'fampnn'/'native').glob('*.pdb'))
    candidates += list((root/'collected'/'rfantibody_filtered').glob('*.pdb'))
    candidates += list((root/'run'/'rfantibody'/'output').glob('*.pdb'))
    candidates += list((root/'run'/'rfantibody').glob('*.pdb'))
    for candidate in candidates:
        if candidate.is_symlink() or not candidate.resolve().is_relative_to(root):
            continue
        raw = candidate.read_bytes()
        if hashlib.sha256(raw).hexdigest() != native_hash:
            continue
        native = read_export(candidate)
        if native is None:
            continue
        pairs = origin['pairs'] if origin else [[v, v] for v in domain(raw.decode().splitlines())]
        mapping = dict(pairs)
        if (len(mapping) != len(pairs) or set(mapping) != set(domain(raw.decode().splitlines()))
                or len(set(mapping.values())) != len(mapping)
                or set(mapping.values()) != set(domain(source.read_text().splitlines()))):
            raise ValueError('parent native correspondence domain mismatch')
        projected = deepcopy(native)
        for key in ('roles', 'loops'):
            projected[key] = {role:[mapping[v] for v in values] for role, values in native[key].items()}
        projected['cdrs'] = [mapping[v] for v in native['cdrs']]
        for key in ('source', 'source_hashes', 'input_sources', 'roles', 'loops', 'cdrs'):
            if proof.get(key) != projected.get(key):
                raise ValueError('prepared roles differ from authoritative parent native source')
        validate_input_sources(parent.provenance['fampnn_analysis_declaration']['materialization'], native)
        return
    raise ValueError('prepared input has no current parent native producer origin')


def materialize(declaration, receipt, path):
    """Bind deferred selectors only once the physical generated input exists."""
    recipe = declaration.get('materialization')
    if (not isinstance(recipe, dict) or set(recipe) - {'source','summary','mutation','settings','inputs','origins'}
            or not {'source','summary','mutation','settings','inputs'} <= set(recipe)
            or recipe['source'] != 'rfantibody_native_export_v1'
            or recipe['summary'] != 'authorized_antibody_domain'
            or recipe['mutation'] != 'resolved_cdrs'):
        raise ValueError('unsupported antibody materialization declaration')
    proof = read_export(path)
    if proof is None or receipt.get('antibody') != proof:
        raise ValueError('missing/mismatched native antibody role provenance')
    if not isinstance(proof.get('input_sources'), dict) or set(proof['input_sources']) != {'framework', 'target'}:
        raise ValueError('missing native framework/target source binding')
    validate_input_sources(recipe, proof)
    if 'origins' in recipe:
        origin = (proof.get('native_origin') or {}).get('sha256', receipt['source_pdb_sha256'])
        if origin not in recipe['origins']:
            raise ValueError('prepared input differs from admitted parent producer origin')
    constraints = receipt.get('antibody_constraints')
    if not isinstance(constraints, dict):
        raise ValueError('missing actual antibody constraint result')
    settings = declaration['materialization']['settings']
    if any(settings.get(key) != value for key, value in constraints['settings'].items()):
        raise ValueError('antibody constraints conflict with request declaration')
    if declaration['allow_summary_override'] or declaration['summary_override'] is not None:
        raise ValueError('antibody summary override forbidden')
    mutation = declaration['mutation_override']
    if mutation is None:
        mutation = constraints['mutation']
    if not set(mutation) <= set(constraints['mutation']):
        raise ValueError('mutation override outside resolved CDR domain')
    inverse = {out: src for src, out in receipt['pairs']}
    result = {k: deepcopy(v) for k, v in declaration.items() if k != 'materialization'}
    result.update(input_domain=receipt['source_domain'],
                  sequence_design=[inverse[v] for v in constraints['sequence_design']],
                  summary=[inverse[v] for v in constraints['sequence_design']],
                  fixed=[inverse[v] for v in constraints['fixed']],
                  mutation_override=[inverse[v] for v in mutation])
    return result


def install_native_export(root='/opt/RFantibody'):
    """Hook the existing native writer before the existing entrypoint imports it."""
    hashes = {name: hashlib.sha256((Path(root)/name).read_bytes()).hexdigest() for name in NATIVE_SOURCES}
    if hashes != NATIVE_SOURCES:
        raise ValueError('unverified RFantibody source; role export unavailable')
    from rfantibody.util import io
    from rfantibody.rfdiffusion.inference import model_runners
    import sys
    loaded = {'src/rfantibody/util/io.py': io,
              'src/rfantibody/rfdiffusion/inference/model_runners.py': model_runners,
              'src/rfantibody/rfdiffusion/inference/ab_pose.py': sys.modules.get('rfantibody.rfdiffusion.inference.ab_pose'),
              'src/rfantibody/rfdiffusion/parsers.py': sys.modules.get('rfantibody.rfdiffusion.parsers')}
    for path, module in loaded.items():
        if module is None or Path(getattr(module, '__file__', '')).resolve() != (Path(root)/path).resolve():
            raise ValueError('loaded RFantibody module differs from verified source binding')
    original_init = model_runners.AbSampler.sample_init
    sources = {}
    def sample_init_with_sources(sampler, *args, **kwargs):
        sources.clear()
        if sampler.ab_conf.framework_pdb and sampler.ab_conf.target_pdb:
            sources.update(input_sources(sampler.ab_conf.framework_pdb, sampler.ab_conf.target_pdb))
        return original_init(sampler, *args, **kwargs)
    model_runners.AbSampler.sample_init = sample_init_with_sources
    original = io.ab_write_pdblines
    def write_with_roles(*args, **kwargs):
        # The inspected entrypoint supplies these exact native keyword arguments.
        lines = original(*args, **kwargs)
        return native_export(lines, list(kwargs['chain_idx']), kwargs['loop_map'], hashes, sources)
    io.ab_write_pdblines = write_with_roles
