"""CPU-only BC2 runtime handoff, with pinned interpreter differential."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from services.bindcraft2_native import PIN, compile_for_native, _canonical
from services.bindcraft2_runtime import CampaignContractError, prepare_campaign, run_campaign

UPSTREAM = Path(os.environ["BMS_TEST_BC2_UPSTREAM"]) if os.environ.get("BMS_TEST_BC2_UPSTREAM") else None
SCRIPT = Path(__file__).parents[3] / "scripts/run_bindcraft2_campaign.py"


def native_python():
    if UPSTREAM is None:
        pytest.skip("set BMS_TEST_BC2_UPSTREAM to the pinned CPU checkout")
    assert UPSTREAM is not None
    interpreter = UPSTREAM / ".venv/bin/python"
    if not interpreter.exists():
        pytest.skip("pinned BC2 CPU interpreter not available")
    assert subprocess.check_output(["git", "-C", str(UPSTREAM), "rev-parse", "HEAD"], text=True).strip() == PIN
    return interpreter


def test_native_cli_settings_differential_and_exact_command(tmp_path):
    python = native_python()
    campaign = tmp_path / "job" / "campaign"
    request = {"max_trajectories": 3, "modality": ["binder"],
               "targets": [{"name": "on", "target_path": str(tmp_path / "target.cif"), "hotspots": "A:10"}],
               "sparse_output": True}
    # Compile and prepare in the pinned CPU interpreter (API interpreter need not
    # import the native JAX stack). Compare its CLI read with compiled effective.
    code = """import json,sys,runpy
from pathlib import Path
runpy.run_path(sys.argv[3], run_name='bc2_module')
from bindcraft.settings import load_settings, read_settings
from bindcraft.preflight import cleaned_campaign_settings
from bindcraft.parameter_sweep import parameter_sweep_arms
from services.bindcraft2_native import compile_for_native, _canonical
from services.bindcraft2_runtime import prepare_campaign
request=json.loads(sys.argv[1]); folder=Path(sys.argv[2]); compiled=compile_for_native(request,folder/'campaign',load_settings,parameter_sweep_arms)
prepared=prepare_campaign(compiled,folder,load_settings,parameter_sweep_arms,lambda p: cleaned_campaign_settings(read_settings(p)))
assert _canonical(cleaned_campaign_settings(read_settings(prepared['settings_path'])))==_canonical(compiled['effective_settings'])
assert prepare_campaign(compiled,folder,load_settings,parameter_sweep_arms,lambda p: cleaned_campaign_settings(read_settings(p)))==prepared
print(json.dumps(prepared))
"""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1])}
    output = subprocess.check_output([str(python), "-c", code, json.dumps(request), str(campaign.parent), str(SCRIPT)],
                                     text=True, env=env)
    prepared = json.loads(output.splitlines()[-1])
    assert prepared["command"] == ["bindcraft", "design", str(campaign.parent / "native_settings.json")]
    assert prepared["campaign_root"] == str(campaign)
    assert prepared["aggregate_attempt_allowance"] == 3
    assert not campaign.exists()  # no GPU campaign or fake result
    # Public CLI adapter consumes the compiler's receipt without changing it.
    receipt = Path(prepared["receipt_path"])
    output = subprocess.check_output([str(python), str(SCRIPT), str(receipt), str(campaign.parent),
                                      "--native-source", str(UPSTREAM)], text=True, env=env)
    assert json.loads(output.splitlines()[-1]) == prepared


def test_native_sweep_budget_differential(tmp_path):
    python = native_python()
    code = """import json,sys,runpy
from pathlib import Path
runpy.run_path(sys.argv[2],run_name='bc2_module')
from bindcraft.settings import load_settings,read_settings
from bindcraft.parameter_sweep import parameter_sweep_arms
from bindcraft.preflight import cleaned_campaign_settings
from services.bindcraft2_native import compile_for_native
from services.bindcraft2_runtime import prepare_campaign
root=Path(sys.argv[1]); request={'max_trajectories':6,'targets':[{'name':'t','target_path':str(root/'target.cif')}], 'parameter_sweep':{'axes':['weights_interface_contacts'],'levels':[0.5,2.0]}}
compiled=compile_for_native(request,root/'campaign',load_settings,parameter_sweep_arms)
assert compiled['sweep_budget']=={'arms':3,'per_arm':2,'aggregate_allowance':6}
prepared=prepare_campaign(compiled,root,load_settings,parameter_sweep_arms,lambda p:cleaned_campaign_settings(read_settings(p)))
print(json.dumps(prepared['aggregate_attempt_allowance']))
try:
    compile_for_native({**request,'max_trajectories':2},root/'other_campaign',load_settings,parameter_sweep_arms)
except ValueError as exc:
    assert 'exceeding requested' in str(exc)
else:
    raise AssertionError('native sweep over-budget accepted')
"""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1])}
    result = subprocess.check_output([str(python), "-c", code, str(tmp_path / "job"), str(SCRIPT)], text=True, env=env)
    assert json.loads(result.splitlines()[-1]) == 6


def test_receipt_tampering_or_wrong_location_cannot_prepare(tmp_path):
    def resolve(request):
        return {**request, "binder_lengths": [60, 180]}
    request = {"max_trajectories": 2, "targets": [{"name": "t", "target_path": str(tmp_path / "t.fasta")}]}
    compiled = compile_for_native(request, tmp_path / "job/campaign", resolve)
    read = lambda path: resolve(json.loads(path.read_text()))
    arms = lambda effective: ()
    for edit in ({"effective_sha256": "0" * 64}, {"sweep_budget": {"arms": 0}},
                 {"upstream_commit": "unverified"}):
        with pytest.raises(CampaignContractError):
            prepare_campaign({**compiled, **edit}, tmp_path / "job", resolve, arms, read)
    with pytest.raises(CampaignContractError, match="folder/resume"):
        prepare_campaign(compiled, tmp_path / "other", resolve, arms, read)
    prepared = prepare_campaign(compiled, tmp_path / "job", resolve, arms, read)
    Path(prepared["settings_path"]).write_text("{}")
    with pytest.raises(CampaignContractError, match="existing BC2"):
        prepare_campaign(compiled, tmp_path / "job", resolve, arms, read)


def test_native_exit_code_propagates_without_result_fabrication(tmp_path):
    assert run_campaign({"settings_path": str(tmp_path / "request.json")}, executable="/bin/false") != 0
    assert not list(tmp_path.iterdir())


def test_native_environment_override_cannot_replace_compiled_science(tmp_path):
    env = {**os.environ, "BINDCRAFT_BINDER_LENGTHS": "12-14"}
    proc = subprocess.run(
        [str(native_python()), str(SCRIPT), str(tmp_path / "missing.json"), str(tmp_path / "job"),
         "--native-source", str(UPSTREAM)], env=env, text=True, capture_output=True,
    )
    assert proc.returncode != 0
    assert "unbound native overrides: BINDCRAFT_BINDER_LENGTHS" in proc.stderr
    assert not (tmp_path / "job").exists()
