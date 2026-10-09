"""Selected NGS transport evidence stays separate from launch approval."""
import hashlib
import importlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def owners(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    monkeypatch.syspath_prepend(str(ROOT / 'platform/api'))
    monkeypatch.setenv('BMS_DATA', str(tmp_path))
    for variable, name in [('DATABASE_URL', 'core'), ('BMS_EXPERIMENT_DATABASE_URL', 'experiment'),
                           ('BMS_MOLBIO_NGS_DATABASE_URL', 'molbio-ngs')]:
        monkeypatch.setenv(variable, 'sqlite+aiosqlite:///' + str(tmp_path / (name + '.db')))
    monkeypatch.setenv('BMS_MOLBIO_DB_PATH', str(tmp_path / 'molbio.db'))
    bundle = importlib.import_module('services.remote_execution.bundle')
    portable = importlib.import_module('scripts.lib.portable_inputs')
    for name in ('get_data_root', 'get_inputs_dir', 'get_results_dir'):
        monkeypatch.setattr(bundle, name, lambda: tmp_path)
    return bundle, portable


def invocation(mode):
    return SimpleNamespace(model_id='nanopore', mode=mode, generated_inputs=())


def assets(bundle, tmp_path, params, mode):
    return bundle._input_assets(params, native_invocation=invocation(mode),
        repo_root=ROOT, runtime_paths=set(), output_dir=tmp_path / 'out')


def test_final_inventory_reuses_bodies_but_retains_change_and_membership(owners, tmp_path, monkeypatch):
    bundle, portable = owners
    tree = tmp_path / 'reads'
    tree.mkdir()
    read = tree / 'a.fastq'
    read.write_bytes(b'@r\nACGT\n+\nIIII\n' * 1024)
    params = {'fastq_path': str(tree)}
    expected = portable.discover_native_input_references('nanopore', 'ont_fastq_qc',
        params, (), output_dir=tmp_path, allowed_roots=(tmp_path,))
    job = SimpleNamespace(provenance={'execution_plan_approval': {
        'input_request': dict(model_id='nanopore', mode='ont_fastq_qc', params=params,
                              output_dir=str(tmp_path)), 'input_identities': expected}})
    inventory = {}
    records = bundle._input_records(tree, 'inputs/reads', native_invocation=invocation('ont_fastq_qc'),
        output_dir=tmp_path, input_inventory=inventory)
    hashes = {path: row[:2] for path, row in inventory.items()}
    reads = []
    original = portable._identity
    def counted(path):
        reads.append(path.stat().st_size)
        return original(path)
    monkeypatch.setattr(portable, '_identity', counted)
    bundle.verify_approved_native_inputs(job, {}, hashes, inventory)
    assert reads == []
    print('final_inventory_body_reads=0 final_inventory_body_bytes=0 reused_bytes=' + str(read.stat().st_size))
    assert sum(row.size_bytes for row in records) == read.stat().st_size
    read.write_bytes(b'changed')
    with pytest.raises(bundle.RemoteBundleError, match='bytes or membership'):
        bundle.verify_approved_native_inputs(job, {}, hashes, inventory)
    assert reads == [7]
    read.write_bytes(b'@r\nACGT\n+\nIIII\n' * 1024)
    (tree / 'new.fastq').write_text('new')
    with pytest.raises(bundle.RemoteBundleError, match='bytes or membership'):
        bundle.verify_approved_native_inputs(job, {}, hashes, inventory)
    # Approval-absent legacy/Local dispatch is still inert.
    bundle.verify_approved_native_inputs(SimpleNamespace(provenance={}), {}, {})


def test_selected_options_transfer_inventory_not_approval(owners, tmp_path, monkeypatch):
    bundle, portable = owners
    pod5 = tmp_path / 'pod5'
    pod5.mkdir()
    (pod5 / 'a.pod5').write_bytes(b'signal')
    (pod5 / 'pairs.txt').write_text('a b')
    primers = tmp_path / 'primers.fasta'
    primers.write_text('>p\nACGT\n')
    params = dict(pod5_dir=str(pod5), duplex_pairs=str(pod5 / 'pairs.txt'),
                  wf_clone_primers=str(primers), run_assembly=True,
                  ont_workflow_id='ont_construct_screening')
    for key in ('sample_sheet', 'wf_clone_insert_reference', 'wf_clone_host_reference',
                'wf_clone_regions_bedfile'):
        path = (pod5 if key == 'sample_sheet' else tmp_path) / key
        path.write_text(key)
        params[key] = str(path)
    selected = assets(bundle, tmp_path, params, 'construct_screening')
    assert {p for p, _ in selected} == {pod5, primers, *(Path(params[key]) for key in ('wf_clone_insert_reference', 'wf_clone_host_reference', 'wf_clone_regions_bedfile'))}
    assert portable.discover_native_input_references('nanopore', 'construct_screening',
        params, (), output_dir=tmp_path, allowed_roots=(tmp_path,)) == []
    transfers, records = [], []
    remote = tmp_path / 'worker'
    for source, relative in selected:
        target = remote / 'bundle/inputs' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target) if source.is_dir() else shutil.copyfile(source, target)
        transfers.append(bundle.TransferPlan(source, str(target)))
        records.extend(bundle._input_records(source, 'inputs/' + relative,
            native_invocation=invocation('construct_screening'), output_dir=tmp_path))
    transfer, _ = bundle._write_portable_bindings(staging_root=tmp_path,
        remote_attempt=str(remote), references=[], input_transfers=transfers,
        input_records=records, remote_runtime=str(remote / 'runtime'), remote_results=str(remote / 'results'),
        selected_inputs=portable.selected_ngs_input_fields('construct_screening', params))
    payload = json.loads(transfer.source.read_bytes())
    assert len(payload['bindings']) == 7
    assert {tuple(row['reference']['selector']) for row in payload['bindings']} == {
        ('pod5_dir', 'a.pod5'), ('duplex_pairs',), ('wf_clone_primers',),
        ('sample_sheet',), ('wf_clone_insert_reference',), ('wf_clone_host_reference',),
        ('wf_clone_regions_bedfile',)}
    monkeypatch.setenv(portable.ENV, str(transfer.source))
    assert portable.resolve_input_path(primers).read_bytes() == primers.read_bytes()
    monkeypatch.delenv(portable.ENV)
    assert portable.resolve_input_path(primers) == primers
    params.update(run_assembly=False, pod5_dir='' , duplex_pairs='/missing/inactive')
    assert assets(bundle, tmp_path, params, 'construct_screening') == []


def test_pooled_declared_siblings_keep_layout_and_manifest_bytes(owners, tmp_path, monkeypatch):
    bundle, portable = owners
    from services import ont_pooled_reference_assignment as pooled
    from scripts.pooled_ont_reference_assignment import validate_reference_set
    monkeypatch.setattr(pooled, 'get_inputs_dir', lambda: tmp_path)
    rows = [dict(request=SimpleNamespace(target_id=name, label=name, indistinguishable_group=None),
                 sequence=sequence, sequence_id='seq-' + name, revision_id='rev-' + name,
                 revision_sha256=hashlib.sha256(sequence.encode()).hexdigest())
            for name, sequence in [('t', 'ACGT'), ('u', 'TTTT')]]
    manifest, _, _, _ = pooled._stage_reference_set('set-portability', rows)
    snapshot = manifest.parent
    fasta = snapshot / 'refs/t.fasta'
    other = snapshot / 'refs/u.fasta'
    (snapshot / 'refs/unrelated.fasta').write_text('unselected')
    before = manifest.read_bytes()
    params = {'reference_set_manifest': str(manifest)}
    selected = dict(assets(bundle, tmp_path, params, 'ont_pooled_reference_assignment'))
    assert set(selected) == {manifest, fasta, other}
    assert Path(selected[fasta]).parent.parent == Path(selected[manifest]).parent
    assert manifest.read_bytes() == before
    worker = tmp_path / 'worker'
    for source, relative in selected.items():
        target = worker / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    moved = worker / selected[manifest]
    validate_reference_set(moved, moved.parent)
    document = json.loads(moved.read_bytes())
    assert (moved.parent / document['entries'][0]['fasta_path']).read_bytes() == fasta.read_bytes()
    manifest.write_text(json.dumps({'entries': [{'fasta_path': '../escape.fasta'}]}))
    with pytest.raises(ValueError):
        assets(bundle, tmp_path, params, 'ont_pooled_reference_assignment')


def test_inventory_symlink_and_post_inventory_same_size_replacement(owners, tmp_path):
    bundle, portable = owners
    path = tmp_path / 'reads.fastq'
    path.write_text('AAAA')
    inventory = {}
    bundle._input_records(path, 'inputs/reads.fastq', native_invocation=invocation('ont_fastq_qc'),
                          output_dir=tmp_path, input_inventory=inventory)
    original = inventory[str(path)][:2]
    path.unlink()
    path.write_text('TTTT')
    records = portable.discover_native_input_references('nanopore', 'ont_fastq_qc',
        {'fastq_path': str(path)}, (), output_dir=tmp_path, allowed_roots=(tmp_path,), input_inventory=inventory)
    assert records[0]['sha256'] != original[0]
    link = tmp_path / 'link.fastq'
    link.symlink_to(path)
    with pytest.raises(ValueError, match='symlink'):
        portable.discover_native_input_references('nanopore', 'ont_fastq_qc',
            {'fastq_path': str(link)}, (), output_dir=tmp_path, allowed_roots=(tmp_path,), input_inventory=inventory)
