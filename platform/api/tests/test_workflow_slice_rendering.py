from pathlib import Path

import biomodstack_services as services


def test_workflow_parent_slice_requests_cpu_controller_for_both_lanes(tmp_path: Path) -> None:
    development = services.render_user_units(tmp_path / "repo", runtime_mode=services.DEV_RUNTIME_MODE)
    production = services.render_user_units(tmp_path / "repo", runtime_mode=services.CONTAINER_RUNTIME_MODE)

    expected_parent = services.render_workflow_parent_slice()
    assert services.WORKFLOW_PARENT_SLICE == "biomodstack.slice"
    assert development[services.WORKFLOW_PARENT_SLICE] == expected_parent
    assert production[services.WORKFLOW_PARENT_SLICE] == expected_parent
    assert "CPUAccounting=true" in expected_parent
    assert "CPUWeight=100" in expected_parent
    assert "MemoryAccounting=true" in expected_parent
    assert "TasksAccounting=true" in expected_parent
