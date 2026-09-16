"""Shared weight relocation through the real native command/bundle producer."""
from services.remote_execution.bundle import prepare_remote_bundle
from test_remote_bundle_runtime_gaps import package, compile_native


def test_selected_custom_weight_root_is_relocated_not_copied_as_input(package):
    roots, release, job, target, _ = package
    original = roots['weights'] / 'protenix'
    custom = roots['weights'] / 'custom installed weights'
    original.rename(custom)
    params = dict(job.params, protenix_weights=str(custom), protenix_use_msa=False,
                  protenix_msa_backend="colabfold_api", run_frustrampnn=False)
    command = compile_native(job, params)
    prepared = prepare_remote_bundle(job=job, target=target, command=command,
                                     native_invocation=job.native_invocation)
    argv = prepared.envelope.command
    shared = prepared.envelope.environment['BMS_WEIGHTS']
    assert argv[argv.index('--protenix_weights') + 1] == shared + '/protenix'
    assert str(custom) not in ' '.join(argv)
    assert all(e.remote_destination.startswith(shared + '/protenix/') for e in prepared.runtime_weights)
    assert all(e.source.is_relative_to(custom) for e in prepared.runtime_weights)
    assert not any('custom installed weights' in str(t.source) for t in prepared.input_transfers)
    assert not any(r.relative_path.startswith('runtime/weights/') for r in prepared.envelope.files)
