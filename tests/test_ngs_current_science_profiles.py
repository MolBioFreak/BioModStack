from pathlib import Path
import json
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'platform/api'))
from validate_wf_clone_runtime import selected_images
from scripts.lib.component_adapter import wf_clone_container_config
from services.ont_ngs_contract import normalize_ont_launch_params


def test_current_profiles_preserve_native_quality_and_optional_branches():
    expected = json.loads((ROOT / 'config/ngs/dorado_v2.1.2.lock.json').read_text())
    for molecule in ('dna', 'rna'):
        for quality in ('fast', 'hac', 'sup'):
            p = normalize_ont_launch_params('ont_basecall_' + molecule, {'dorado_quality_mode': quality})
            assert p['dorado_model'] == expected['models'][molecule][quality]['id']
    p = normalize_ont_launch_params('ont_basecall_dna', {'dorado_quality_mode': 'hac', 'dorado_basecall_mode': 'duplex', 'duplex_pairs': '/input/pairs'})
    assert p['dorado_stereo_model'] == expected['models']['stereo']['id']
    for modification in ('none', '5mC_5hmC', '6mA'):
        p = normalize_ont_launch_params('ont_methylation_analysis', {'dorado_quality_mode': 'hac', 'modified_bases': modification, 'run_modkit': False})
        assert p['modified_bases'] == modification and p['run_modkit'] is False


def test_clone_profiles_and_all_optional_controls_preserved():
    lock = json.loads((ROOT / 'config/ngs/wf_clone_validation_v1.8.4.lock.json').read_text())
    for model in lock['models']['accepted_upstream_ids']:
        for assembler in ('flye', 'canu'):
            request = dict(wf_clone_basecaller_model=model, wf_clone_assembly_tool=assembler,
                wf_clone_primers='/input/primers.tsv', wf_clone_insert_reference='/input/insert.fa',
                wf_clone_host_reference='/input/host.fa', wf_clone_regions_bedfile='/input/regions.bed',
                wf_clone_flye_quality='nano-raw', wf_clone_canu_fast=True,
                wf_clone_non_uniform_coverage=True, wf_clone_large_construct=True)
            p = normalize_ont_launch_params('wf_clone_validation', request)
            assert all(p[k] == v for k, v in request.items())


def test_pod5_native_profile_default_and_saved_declaration_are_distinct():
    for quality in ('hac', 'sup'):
        request = {'pod5_dir': '/input/pod5', 'dorado_quality_mode': quality}
        p = normalize_ont_launch_params('wf_clone_validation', request)
        assert p['wf_clone_basecaller_model'] == p['dorado_resolved_model_id']
        request['wf_clone_basecaller_model'] = 'dna_r10.4.1_e8.2_400bps_hac@v5.0.0'
        p = normalize_ont_launch_params('wf_clone_validation', request)
        assert p['wf_clone_basecaller_model'] == request['wf_clone_basecaller_model']
    fast = normalize_ont_launch_params('wf_clone_validation', {
        'pod5_dir': '/input/pod5', 'dorado_quality_mode': 'fast'})
    assert fast['dorado_resolved_model_id'] == 'dna_r10.4.1_e8.2_400bps_fast@v5.2.0'
    # FAST remains selectable; the separate declared polisher is not evidence
    # of a native FAST-trained Medaka model or of read origin.
    assert fast['wf_clone_basecaller_model'] == 'dna_r10.4.1_e8.2_400bps_hac@v6.0.0'
    disabled = normalize_ont_launch_params('ont_construct_screening', {
        'pod5_dir': '/input/pod5', 'dorado_quality_mode': 'sup', 'run_assembly': False})
    assert 'wf_clone_basecaller_model' not in disabled


@pytest.mark.parametrize('backend', ['apptainer', 'udocker'])
def test_selected_clone_image_paths_override_the_real_native_labels(tmp_path, monkeypatch, backend):
    lock = json.loads((ROOT / 'config/ngs/wf_clone_validation_v1.8.4.lock.json').read_text())
    lock['containers']['cache_dir'] = str(tmp_path)
    path = tmp_path / 'lock.json'; path.write_text(json.dumps(lock))
    monkeypatch.setenv('BMS_CONTAINER_BACKEND', backend)
    if backend == 'udocker': monkeypatch.setenv('BMS_CONTAINER_EXECUTABLE', '/private/bms-container')
    for assembler, count in [('flye', 4), ('canu', 5)]:
        images = selected_images(lock, assembler)
        assert len(images) == count
        config = wf_clone_container_config(path, assembler)
        for image in images:
            assert str(tmp_path / image['cache_file']) in config
            assert "withLabel: '" + image['label'] + "'" in config
        assert ("withLabel: 'canu'" in config) == (assembler == 'canu')
