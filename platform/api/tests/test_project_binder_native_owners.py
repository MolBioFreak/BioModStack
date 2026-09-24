import pytest
from services.workflow_adapter_registry import is_project_native_destination_registered


@pytest.mark.parametrize("model,mode", [
    ("boltzgen", "protein_binder"), ("boltzgen", "nanobody_binder"),
    ("boltzgen", "peptide_binder"), ("ppiflow", "protein_binder"),
    ("ppiflow", "antibody_binder"), ("ppiflow", "nanobody_binder"),
])
def test_native_route_registration_is_exact(model, mode):
    owner = f"{model}:{mode}"
    route = f"/submit?model={model}&mode={mode}"
    assert is_project_native_destination_registered(owner, route)
    assert not is_project_native_destination_registered(owner, route + "&mode=other")
    assert not is_project_native_destination_registered(owner, route + "&template=antibody_denovo")
    assert not is_project_native_destination_registered(owner, route.replace(mode, "unknown"))
    assert not is_project_native_destination_registered(owner, "https://example.org" + route)
    assert not is_project_native_destination_registered(owner, route + "#fragment")


def test_rfantibody_uses_actual_template_owner_not_fictitious_direct_route():
    assert is_project_native_destination_registered("antibody_denovo", "/submit?template=antibody_denovo")
    assert not is_project_native_destination_registered("rfantibody", "/submit?model=rfantibody&mode=binder")
