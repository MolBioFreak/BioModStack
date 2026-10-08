"""Typed provider selection and scientific settings, with no service calls."""
import copy
import pytest
from services.msa_policy import POLICY, apply_msa_policy, requires_msa_search
from schemas import JobCreate

MODELS = ("protenix", "boltz2", "boltz_cp_experimental", "rf3")
FIELDS = POLICY["neurosnap_settings"]

@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("provider", POLICY["enabled_search_backends"])
def test_selection_roundtrip(model, provider):
    requested = {"msa_provider": provider}
    saved = copy.deepcopy(requested)
    effective = apply_msa_policy(model, requested)
    assert requested == saved
    assert effective["msa_provider"] == provider
    assert requires_msa_search(model, effective)
    assert apply_msa_policy(model, effective) == effective
    if provider == "neurosnap_api":
        assert {k: effective[k] for k in FIELDS} == {k: f["default"] for k, f in FIELDS.items()}

@pytest.mark.parametrize("key", ["msa_provider", "protenix_msa_backend"])
def test_backend_alias_and_local_rejection(key):
    assert apply_msa_policy("protenix", {key: "neurosnap_api"})["msa_provider"] == "neurosnap_api"
    assert apply_msa_policy("protenix", {key: "auto"})["msa_provider"] == "colabfold_api"
    with pytest.raises(ValueError, match="Local MSA search is disabled"):
        apply_msa_policy("protenix", {key: "local"})

def test_conflict_fails_without_rewriting_saved_intent():
    params = {"msa_provider": "neurosnap_api", "protenix_msa_backend": "auto"}
    with pytest.raises(ValueError, match="Conflicting"):
        apply_msa_policy("protenix", params)
    assert params["protenix_msa_backend"] == "auto"

@pytest.mark.parametrize("key", FIELDS)
def test_native_types_bounds_and_persistence(key):
    field = FIELDS[key]
    good = [False, True] if field["type"] == "boolean" else [field["minimum"], field["maximum"]]
    bad = [0, "false", None] if field["type"] == "boolean" else [True, str(field["default"]), field["minimum"] - 1, field["maximum"] + 1, float("nan")]
    for value in good:
        params = {"msa_provider": "neurosnap_api", key: value}
        assert apply_msa_policy("boltz2", params)[key] == value
        request = JobCreate(name="saved", model_id="boltz2", mode="predict", params=params)
        assert JobCreate.model_validate(request.model_dump()).params[key] == value
    for value in bad:
        with pytest.raises(ValueError, match=key):
            apply_msa_policy("boltz2", {"msa_provider": "neurosnap_api", key: value})

def test_closed_native_keys_and_integral_depth():
    for key, value in [("msa_neurosnap_unknown", 1), ("msa_neurosnap_max_sequences", 10.5)]:
        with pytest.raises(ValueError, match=key):
            apply_msa_policy("boltz2", {"msa_provider": "neurosnap_api", key: value})

@pytest.mark.parametrize("key", ["msa_neurosnap_force_uppercase", "msa_neurosnap_pad_sequences"])
def test_unsupported_output_flags_explicitly_rejected(key):
    params = {"msa_provider": "neurosnap_api", key: True}
    with pytest.raises(ValueError, match="unsupported for Protenix"):
        apply_msa_policy("protenix", params)
    assert params[key] is True

@pytest.mark.parametrize("model", MODELS)
def test_registry_has_complete_native_contract(model):
    from model_registry import get_registry
    registry_model = get_registry().get_model(model)
    params = {p.name: p for p in registry_model.params}
    assert "neurosnap_api" in params["msa_provider"].enum
    assert "local" not in params["msa_provider"].enum
    for key, field in FIELDS.items():
        assert params[key].default == field["default"]
        assert params[key].type == field["type"]
        assert field["native_name"] in params[key].description
        if "minimum" in field:
            assert params[key].minimum == field["minimum"]
            assert params[key].maximum == field["maximum"]
        for mode in registry_model.modes:
            assert key in mode.params

def test_old_colabfold_controls_preserved_not_transposed():
    params = {"msa_provider": "colabfold_api", "msa_use_env": False, "msa_preset": "maximum", "msa_min_seq_id": 0.3}
    result = apply_msa_policy("protenix", params)
    assert all(result[k] == v for k, v in params.items())
    assert not any(k in result for k in FIELDS)

@pytest.mark.parametrize("key", POLICY["colabfold_settings"])
def test_colabfold_native_schema_types_and_saved_values(key):
    field = POLICY["colabfold_settings"][key]
    values = [True, False] if field["type"] == "boolean" else field["enum"]
    for value in values:
        result = apply_msa_policy("boltz2", {"msa_provider": "colabfold_api", key: value})
        assert result[key] == value
    with pytest.raises(ValueError, match=key):
        apply_msa_policy("boltz2", {"msa_provider": "colabfold_api", key: "invalid"})
    from model_registry import get_registry
    for model in MODELS:
        params = {p.name: p for p in get_registry().get_model(model).params}
        assert params[key].default == field["default"]
        assert params[key].type == field["type"]


def test_legacy_environment_alias_preserves_false_and_rejects_conflict():
    result = apply_msa_policy("boltz2", {"msa_use_env": False})
    assert result["msa_use_env"] is result["colabfold_use_env"] is False
    with pytest.raises(ValueError, match="Conflicting msa_use_env"):
        apply_msa_policy("boltz2", {"msa_use_env": False, "colabfold_use_env": True})
