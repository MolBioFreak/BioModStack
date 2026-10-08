from services.nextflow import _apply_protenix_oom_retry_downshift, _attempt_has_cuda_oom


def test_cuda_oom_detection_accepts_supported_markers() -> None:
    markers = (
        "runtime: CUDA out of memory while allocating",
        "torch.OutOfMemoryError: GPU exhausted",
        "CUBLAS_STATUS_ALLOC_FAILED",
    )
    for marker in markers:
        assert _attempt_has_cuda_oom((marker,))
    assert not _attempt_has_cuda_oom(("ordinary workflow failure",))


def test_protenix_oom_ladder_changes_unsafe_parameters_on_every_rung() -> None:
    initial = {
        "protenix_n_sample": 5,
        "protenix_n_cycle": 10,
        "protenix_n_step": 200,
        "protenix_use_msa": True,
        "msa_preset": "maximum",
    }

    rung1, changes1 = _apply_protenix_oom_retry_downshift(initial, 1)
    assert rung1["protenix_n_sample"] == 1
    assert rung1["protenix_n_cycle"] == 4
    assert rung1["msa_preset"] == "fast"
    assert changes1 and rung1 != initial

    rung2, changes2 = _apply_protenix_oom_retry_downshift(rung1, 2)
    assert rung2["protenix_use_msa"] is False
    assert changes2 and rung2 != rung1

    rung3, changes3 = _apply_protenix_oom_retry_downshift(rung2, 3)
    assert rung3["protenix_n_step"] == 100
    assert changes3 and rung3 != rung2


def test_protenix_oom_ladder_stops_when_no_safer_change_remains() -> None:
    already_minimal = {
        "protenix_n_sample": 1,
        "protenix_n_cycle": 4,
        "protenix_n_step": 100,
        "protenix_use_msa": False,
        "msa_preset": "fast",
    }
    tuned, changes = _apply_protenix_oom_retry_downshift(already_minimal, 3)
    assert tuned == already_minimal
    assert changes == []
