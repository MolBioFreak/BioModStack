"""Focused lane-E inventory and isolated native compilation tests."""
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from services.bindcraft2_inventory import inventory
from services.bindcraft2_native import PIN, compile_for_native

SNAPSHOT = Path(__file__).parents[1] / "config/models/bindcraft2_native_inventory.json"


def test_static_inventory_and_discrepancies():
    snapshot = json.loads(SNAPSHOT.read_text())
    assert snapshot["upstream_commit"] == PIN
    assert len(snapshot["reference_fields"]) == 235
    assert set(snapshot["presets"]["modality"]) >= {
        "binder", "large_binder", "peptide", "cyclic_peptide", "homo_oligomer",
        "multidomain", "VHH", "ARP", "scFv", "Fab", "induced_fit", "fold_switch",
    }
    assert "paratope_conformations" in snapshot["preset_only_not_in_reference"]
    assert snapshot["coverage_status"].startswith("INCOMPLETE")
    upstream = os.environ.get("BMS_TEST_BC2_UPSTREAM")
    if upstream:
        assert subprocess.check_output(["git", "-C", upstream, "rev-parse", "HEAD"], text=True).strip() == PIN
        assert inventory(Path(upstream)) == snapshot
        patch = Path(__file__).parents[3] / "apptainer/bindcraft2-producer.patch"
        subprocess.run(["git", "-C", upstream, "apply", "--check", str(patch)], check=True)


def test_import_does_not_import_native_accelerator():
    code = "import services.bindcraft2_native, services.bindcraft2_inventory; import sys; assert 'jax' not in sys.modules and 'bindcraft' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True)


def _resolver(request):
    return {**request, "binder_lengths": request.get("binder_lengths", [60, 180])}


def test_compilation_is_bounded_and_preserves_nested_science(tmp_path):
    request = {"max_trajectories": 7, "modality": ["VHH"], "targets": [
        {"name": "on", "target_path": "/inputs/on.cif", "hotspots": "A:10"},
        {"name": "off", "target_path": "/inputs/off.cif", "objective": "detarget"}],
        "paratope_conformations": {"CDR3": "extended"}}
    compiled = compile_for_native(request, tmp_path / "campaign", _resolver)
    assert compiled["native_request"]["targets"] == request["targets"]
    assert compiled["effective_settings"]["binder_lengths"] == [60, 180]
    assert compiled["effective_sha256"] == compile_for_native(request, tmp_path / "campaign", _resolver)["effective_sha256"]
    assert compiled["native_request"]["resume"] is False


@pytest.mark.parametrize("native_input", [
    {"max_trajectories": None}, {"max_trajectories": 0}, {"max_trajectories": True},
    {"max_trajectories": 2, "project_folder": "/tmp/mine"},
    {"max_trajectories": 2, "resume": True},
])
def test_unsafe_compilation_refused(tmp_path, native_input):
    with pytest.raises(ValueError):
        compile_for_native(native_input, tmp_path, _resolver)
