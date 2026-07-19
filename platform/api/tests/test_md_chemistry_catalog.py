from __future__ import annotations

import importlib
import hashlib
import json
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent
CATALOG_DIR = API_ROOT / "config" / "md_chemistry_profiles"
GROMACS_SIF_SHA256 = "97c117ea07496c0d1b13d80be84d33345b89063b47ccfb83f6cbff0145f1385b"
for candidate in (str(API_ROOT), str(REPO_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)


def _catalog_module():
    try:
        return importlib.import_module("services.md.chemistry_catalog")
    except ModuleNotFoundError:
        pytest.fail("chemistry catalog service is not implemented")


def _probe(*asset_ids: str):
    module = _catalog_module()
    return module.RuntimeProbeResult(
        runtime_id="gromacs-2025.3",
        runtime_version="2025.3",
        available=True,
        asset_ids=frozenset(asset_ids),
        checked_at="2026-07-19T03:30:00Z",
        error_code=None,
        sif_sha256=GROMACS_SIF_SHA256,
    )


def _stock_catalog(*asset_ids: str):
    module = _catalog_module()
    return module.ChemistryCatalog(
        config_dir=CATALOG_DIR,
        probe=lambda: _probe(*asset_ids),
    )


def _profile(catalog: Any, profile_id: str) -> dict[str, Any]:
    profile = catalog.get_profile(profile_id)
    assert profile is not None
    return profile


def _valid_smoke_selection(catalog: Any) -> dict[str, Any]:
    smoke = _profile(catalog, "gmx_amber99sb_ildn_tip3p_smoke_v1")
    return {
        "profile_id": smoke["id"],
        "profile_sha256": smoke["profile_sha256"],
        "force_field": "amber99sb-ildn",
        "water_model": "tip3p",
        "engine": "gromacs",
        "requested_scope": "smoke_auto",
    }


def test_absent_charmm36_assets_cannot_be_selectable() -> None:
    catalog = _stock_catalog("amber99sb-ildn.ff", "charmm27.ff")

    charmm_profiles = [profile for profile in catalog.list_profiles() if profile["family"] == "charmm"]

    assert charmm_profiles
    assert all(profile["states"]["installed"] is False for profile in charmm_profiles)
    assert all(profile["states"]["asset_probe_success"] is False for profile in charmm_profiles)
    assert all(profile["states"]["selectable"] is False for profile in charmm_profiles)
    assert not any(
        profile["v1_preparation"]["force_field"] == "charmm36-jul2022" and profile["states"]["selectable"]
        for profile in catalog.list_profiles()
    )


def test_smoke_profile_is_the_only_selectable_automatic_profile_and_is_scope_labeled() -> None:
    catalog = _stock_catalog("amber99sb-ildn.ff", "charmm27.ff", "oplsaa.ff")

    smoke = _profile(catalog, "gmx_amber99sb_ildn_tip3p_smoke_v1")
    selectable = [
        profile["id"]
        for profile in catalog.list_profiles()
        if profile["automatic_preparation"] and profile["states"]["selectable"]
    ]

    assert selectable == ["gmx_amber99sb_ildn_tip3p_smoke_v1"]
    assert smoke["assurance"] == "smoke_fixture"
    assert smoke["legacy"] is True
    assert smoke["states"] == {
        "installed": True,
        "runtime_validated": True,
        "scientifically_validated": True,
        "operator_enabled": True,
        "asset_probe_success": True,
        "selectable": True,
    }
    assert smoke["scientific_validation"]["scope"]["launch_scope"] == "smoke_auto"
    assert smoke["scientific_validation"]["scope"]["system_classes"] == ["infrastructure_smoke_fixture"]
    assert smoke["v1_preparation"] == {"force_field": "amber99sb-ildn", "water_model": "tip3p"}
    assert smoke["launch_constraints"] == {
        "input_mode": "structure",
        "structure_sha256": "c75d7a689617248cdd92dc6633531d2506fb9bef1e6e21e26c8f579ae6955abb",
        "replicas": 1,
        "engine": "gromacs",
        "force_field": "amber99sb-ildn",
        "water_model": "tip3p",
        "timestep_fs": 2.0,
        "temperature_k": 300.0,
        "pressure_bar": 1.0,
        "salt_molar": 0.15,
        "padding_nm": 1.0,
        "max_production_steps": 5_000,
        "max_minimization_steps": 50_000,
        "max_nvt_steps": 50_000,
        "max_npt_steps": 50_000,
    }
    assert smoke["runtime_identity"] == {
        "runtime_id": "gromacs-2025.3",
        "runtime_version": "2025.3",
        "sif_sha256": GROMACS_SIF_SHA256,
    }


def test_selectable_smoke_yaml_pins_the_live_deployed_sif_sha256() -> None:
    smoke_yaml = yaml.safe_load(
        (CATALOG_DIR / "gmx_amber99sb_ildn_tip3p_smoke_v1.yaml").read_text(encoding="utf-8")
    )

    assert smoke_yaml["asset_probe"]["required_runtime_identity"] == {
        "runtime_id": "gromacs-2025.3",
        "runtime_version": "2025.3",
        "sif_sha256": GROMACS_SIF_SHA256,
    }


def test_probe_reports_exact_sif_sha256_without_exposing_its_path(tmp_path: Path) -> None:
    module = _catalog_module()
    image = tmp_path / "private" / "gromacs.sif"
    image.parent.mkdir()
    image.write_bytes(b"deployed-image-bytes")

    completed = module.probe_deployed_gromacs_assets(
        image_path=image,
        runner=lambda *_args, **_kwargs: __import__("subprocess").CompletedProcess(
            args=[], returncode=0, stdout="amber99sb-ildn.ff\n", stderr=""
        ),
    )

    assert completed.runtime_identity == module.RuntimeIdentity(
        runtime_id="gromacs-2025.3",
        runtime_version="2025.3",
        sif_sha256=hashlib.sha256(image.read_bytes()).hexdigest(),
    )
    assert str(tmp_path) not in json.dumps(completed.runtime_identity.as_public_dict())


def test_sif_digest_memo_hashes_once_until_stat_fingerprint_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _catalog_module()
    image = tmp_path / "gromacs.sif"
    image.write_bytes(b"generation-one")
    calls = 0
    original_hash = module._hash_sif_file

    def counted_hash(path: Path) -> str:
        nonlocal calls
        calls += 1
        return original_hash(path)

    monkeypatch.setattr(module, "_hash_sif_file", counted_hash)
    module._clear_sif_digest_memo_for_tests()
    with ThreadPoolExecutor(max_workers=16) as executor:
        first = list(executor.map(lambda _index: module._memoized_sif_sha256(image), range(32)))
    assert len(set(first)) == 1
    assert calls == 1

    image.write_bytes(b"generation-two-with-a-different-size")
    second = module._memoized_sif_sha256(image)

    assert second != first[0]
    assert calls == 2


@pytest.mark.parametrize(
    "changed_identity",
    [
        {"runtime_version": "2025.4"},
        {"sif_sha256": "b" * 64},
    ],
)
def test_runtime_identity_change_changes_digest_and_makes_smoke_profile_unavailable(
    changed_identity: dict[str, str],
) -> None:
    module = _catalog_module()
    identity = {
        "runtime_id": "gromacs-2025.3",
        "runtime_version": "2025.3",
        "sif_sha256": GROMACS_SIF_SHA256,
    }

    def probe():
        return module.RuntimeProbeResult(
            runtime_id=identity["runtime_id"],
            runtime_version=identity["runtime_version"],
            available=True,
            asset_ids=frozenset({"amber99sb-ildn.ff"}),
            checked_at="2026-07-19T05:00:00Z",
            error_code=None,
            sif_sha256=identity["sif_sha256"],
        )

    catalog = module.ChemistryCatalog(config_dir=CATALOG_DIR, probe=probe)
    first = catalog.view()
    assert first.profile_index["gmx_amber99sb_ildn_tip3p_smoke_v1"]["states"]["selectable"] is True
    identity.update(changed_identity)

    catalog.refresh()
    second = catalog.view()

    assert second.profile_index["gmx_amber99sb_ildn_tip3p_smoke_v1"]["states"]["selectable"] is False
    assert second.catalog_digest != first.catalog_digest


def test_modern_profiles_remain_nonselectable_candidates_when_assets_are_absent() -> None:
    catalog = _stock_catalog("amber99sb-ildn.ff")

    modern = _profile(catalog, "amber_ff19sb_opc_protein_v1")

    assert modern["inventory_class"] == "candidate"
    assert modern["states"]["installed"] is False
    assert modern["states"]["runtime_validated"] is False
    assert modern["states"]["scientifically_validated"] is False
    assert modern["states"]["selectable"] is False
    assert modern["launch_constraints"] is None
    assert "candidate" in modern["availability_explanation"].lower()


@pytest.mark.parametrize(
    ("missing_field", "expected_code"),
    [
        ("profile_id", "MD_CHEMISTRY_PROFILE_REQUIRED"),
        ("profile_sha256", "MD_CHEMISTRY_PROFILE_REQUIRED"),
        ("requested_scope", "MD_CHEMISTRY_PROFILE_REQUIRED"),
    ],
)
def test_v1_profile_selection_requires_exact_id_digest_and_scope(
    missing_field: str,
    expected_code: str,
) -> None:
    module = _catalog_module()
    catalog = _stock_catalog("amber99sb-ildn.ff")
    selection = _valid_smoke_selection(catalog)
    selection[missing_field] = None

    with pytest.raises(module.ChemistryProfileSelectionError) as error:
        catalog.validate_v1_profile_selection(**selection)

    assert error.value.code == expected_code


@pytest.mark.parametrize(
    ("override", "expected_code"),
    [
        ({"water_model": "spce"}, "MD_CHEMISTRY_COMBINATION_UNSUPPORTED"),
        ({"engine": "openmm"}, "MD_CHEMISTRY_COMBINATION_UNSUPPORTED"),
        ({"requested_scope": "production"}, "MD_CHEMISTRY_COMBINATION_UNSUPPORTED"),
    ],
)
def test_v1_profile_selection_rejects_pair_engine_and_scope_mismatches(
    override: dict[str, str],
    expected_code: str,
) -> None:
    module = _catalog_module()
    catalog = _stock_catalog("amber99sb-ildn.ff")
    selection = _valid_smoke_selection(catalog)
    selection.update(override)

    with pytest.raises(module.ChemistryProfileSelectionError) as error:
        catalog.validate_v1_profile_selection(**selection)

    assert error.value.code == expected_code


def test_declared_selectable_profile_requires_valid_launch_constraints(tmp_path: Path) -> None:
    module = _catalog_module()
    smoke_yaml = yaml.safe_load((CATALOG_DIR / "gmx_amber99sb_ildn_tip3p_smoke_v1.yaml").read_text(encoding="utf-8"))
    smoke_yaml.pop("launch_constraints", None)
    (tmp_path / "smoke.yaml").write_text(yaml.safe_dump(smoke_yaml, sort_keys=False), encoding="utf-8")
    catalog = module.ChemistryCatalog(
        config_dir=tmp_path,
        probe=lambda: _probe("amber99sb-ildn.ff"),
    )

    with pytest.raises(module.ChemistryCatalogError, match="launch_constraints"):
        catalog.list_profiles()


def test_malformed_yaml_is_wrapped_as_sanitized_catalog_error(tmp_path: Path) -> None:
    module = _catalog_module()
    private_dir = tmp_path / "home" / "private" / "catalog"
    private_dir.mkdir(parents=True)
    (private_dir / "broken.yaml").write_text("schema: [unterminated\n/private/secret", encoding="utf-8")
    catalog = module.ChemistryCatalog(config_dir=private_dir, probe=lambda: _probe("amber99sb-ildn.ff"))

    with pytest.raises(module.ChemistryCatalogError) as error:
        catalog.view()

    assert str(tmp_path) not in str(error.value)
    assert "/private/secret" not in str(error.value)


def test_catalog_read_and_probe_failures_are_wrapped_without_private_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _catalog_module()
    profile_path = tmp_path / "private-profile.yaml"
    profile_path.write_text("placeholder", encoding="utf-8")
    read_catalog = module.ChemistryCatalog(config_dir=tmp_path, probe=lambda: _probe("amber99sb-ildn.ff"))
    original_read_text = Path.read_text

    def private_read_failure(path: Path, *args: Any, **kwargs: Any) -> str:
        if path == profile_path:
            raise OSError(f"read failed at {tmp_path}/operator/private-profile.yaml")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", private_read_failure)
    with pytest.raises(module.ChemistryCatalogError) as read_error:
        read_catalog.view()
    assert str(tmp_path) not in str(read_error.value)

    probe_catalog = module.ChemistryCatalog(
        config_dir=CATALOG_DIR,
        probe=lambda: (_ for _ in ()).throw(OSError(f"probe failed at {tmp_path}/runtime.sif")),
    )
    with pytest.raises(module.ChemistryCatalogError) as probe_error:
        probe_catalog.view()
    assert str(tmp_path) not in str(probe_error.value)


def test_disabled_unavailable_and_stale_profile_selections_are_rejected(tmp_path: Path) -> None:
    module = _catalog_module()
    smoke_yaml = yaml.safe_load((CATALOG_DIR / "gmx_amber99sb_ildn_tip3p_smoke_v1.yaml").read_text(encoding="utf-8"))
    smoke_yaml["operator_enabled"] = False
    disabled_dir = tmp_path / "disabled"
    disabled_dir.mkdir()
    (disabled_dir / "smoke.yaml").write_text(yaml.safe_dump(smoke_yaml, sort_keys=False), encoding="utf-8")
    disabled_catalog = module.ChemistryCatalog(
        config_dir=disabled_dir,
        probe=lambda: _probe("amber99sb-ildn.ff"),
    )
    disabled = _profile(disabled_catalog, "gmx_amber99sb_ildn_tip3p_smoke_v1")

    with pytest.raises(module.ChemistryProfileSelectionError) as disabled_error:
        disabled_catalog.validate_v1_profile_selection(
            profile_id=disabled["id"],
            profile_sha256=disabled["profile_sha256"],
            force_field="amber99sb-ildn",
            water_model="tip3p",
            engine="gromacs",
            requested_scope="smoke_auto",
        )
    assert disabled_error.value.code == "MD_CHEMISTRY_PROFILE_UNAVAILABLE"

    unavailable_catalog = _stock_catalog()
    unavailable = _profile(unavailable_catalog, "gmx_amber99sb_ildn_tip3p_smoke_v1")
    with pytest.raises(module.ChemistryProfileSelectionError) as unavailable_error:
        unavailable_catalog.validate_v1_profile_selection(
            profile_id=unavailable["id"],
            profile_sha256=unavailable["profile_sha256"],
            force_field="amber99sb-ildn",
            water_model="tip3p",
            engine="gromacs",
            requested_scope="smoke_auto",
        )
    assert unavailable_error.value.code == "MD_CHEMISTRY_PROFILE_UNAVAILABLE"

    available_catalog = _stock_catalog("amber99sb-ildn.ff")
    available = _profile(available_catalog, "gmx_amber99sb_ildn_tip3p_smoke_v1")
    with pytest.raises(module.ChemistryProfileSelectionError) as stale_error:
        available_catalog.validate_v1_profile_selection(
            profile_id=available["id"],
            profile_sha256="0" * 64,
            force_field="amber99sb-ildn",
            water_model="tip3p",
            engine="gromacs",
            requested_scope="smoke_auto",
        )
    assert stale_error.value.code == "MD_CHEMISTRY_PROFILE_UNAVAILABLE"
    assert "stale" in str(stale_error.value).lower()


def test_probe_is_cached_and_refresh_is_internal_only() -> None:
    module = _catalog_module()
    calls = 0

    def probe():
        nonlocal calls
        calls += 1
        return _probe("amber99sb-ildn.ff")

    catalog = module.ChemistryCatalog(config_dir=CATALOG_DIR, probe=probe)
    first = catalog.list_profiles()
    second = catalog.list_profiles()
    assert first == second
    assert calls == 1

    catalog.refresh()
    assert catalog.list_profiles() == first
    assert calls == 2


class _DeterministicClock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


@pytest.mark.parametrize("ttl", [0.0, -1.0, math.inf, math.nan])
def test_catalog_rejects_nonpositive_or_nonfinite_cache_ttl(ttl: float) -> None:
    module = _catalog_module()

    with pytest.raises(ValueError, match="cache_ttl_seconds"):
        module.ChemistryCatalog(
            config_dir=CATALOG_DIR,
            probe=lambda: _probe("amber99sb-ildn.ff"),
            cache_ttl_seconds=ttl,
        )


def test_catalog_uses_one_probe_before_ttl_and_refreshes_once_at_expiry() -> None:
    module = _catalog_module()
    clock = _DeterministicClock()
    assets = {"amber99sb-ildn.ff"}
    calls = 0

    def probe():
        nonlocal calls
        calls += 1
        return _probe(*sorted(assets))

    catalog = module.ChemistryCatalog(
        config_dir=CATALOG_DIR,
        probe=probe,
        monotonic_clock=clock,
        cache_ttl_seconds=10.0,
    )
    first = _profile(catalog, "gmx_amber99sb_ildn_tip3p_smoke_v1")
    first_digest = catalog.catalog_digest()
    catalog.probe_summary()
    catalog.list_profiles()
    assert first["states"]["selectable"] is True
    assert calls == 1

    clock.value = 9.999
    assert catalog.catalog_digest() == first_digest
    assert calls == 1

    assets.clear()
    clock.value = 10.0
    expired = _profile(catalog, "gmx_amber99sb_ildn_tip3p_smoke_v1")
    assert expired["states"]["selectable"] is False
    assert catalog.catalog_digest() != first_digest
    assert calls == 2


def test_concurrent_first_read_after_ttl_expiry_performs_exactly_one_refresh() -> None:
    module = _catalog_module()
    clock = _DeterministicClock()
    calls = 0

    def probe():
        nonlocal calls
        calls += 1
        time.sleep(0.01)
        return _probe("amber99sb-ildn.ff")

    catalog = module.ChemistryCatalog(
        config_dir=CATALOG_DIR,
        probe=probe,
        monotonic_clock=clock,
        cache_ttl_seconds=5.0,
    )
    catalog.list_profiles()
    assert calls == 1
    clock.value = 5.0

    with ThreadPoolExecutor(max_workers=16) as executor:
        results = list(executor.map(lambda _index: catalog.list_profiles(), range(32)))

    assert all(result == results[0] for result in results)
    assert calls == 2


def test_selection_captures_one_snapshot_without_calling_public_catalog_methods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = _stock_catalog("amber99sb-ildn.ff")
    selection = _valid_smoke_selection(catalog)

    monkeypatch.setattr(
        catalog,
        "list_profiles",
        lambda: pytest.fail("selection crossed through a separate public catalog operation"),
    )

    assert catalog.validate_v1_profile_selection(**selection)["id"] == selection["profile_id"]


def test_refresh_publishes_one_coherent_fail_closed_unavailable_snapshot() -> None:
    module = _catalog_module()
    available = True
    calls = 0

    def probe():
        nonlocal calls
        calls += 1
        if available:
            return _probe("amber99sb-ildn.ff")
        return module.RuntimeProbeResult(
            runtime_id="gromacs-2025.3",
            runtime_version=None,
            available=False,
            asset_ids=frozenset(),
            checked_at="2026-07-19T04:00:00Z",
            error_code="runtime_probe_failed",
        )

    catalog = module.ChemistryCatalog(config_dir=CATALOG_DIR, probe=probe)
    assert _profile(catalog, "gmx_amber99sb_ildn_tip3p_smoke_v1")["states"]["selectable"] is True
    available = False

    catalog.refresh()

    profiles = catalog.list_profiles()
    smoke = next(profile for profile in profiles if profile["id"] == "gmx_amber99sb_ildn_tip3p_smoke_v1")
    summary = catalog.probe_summary()
    expected_digest = module._canonical_digest(
        [{"id": profile["id"], "profile_sha256": profile["profile_sha256"]} for profile in profiles]
    )
    assert calls == 2
    assert smoke["states"]["selectable"] is False
    assert smoke["states"]["asset_probe_success"] is False
    assert summary["available"] is False
    assert summary["error_code"] == "runtime_probe_failed"
    assert catalog.catalog_digest() == expected_digest


def test_refresh_exception_atomically_invalidates_stale_snapshot_for_next_read() -> None:
    module = _catalog_module()
    outcome = "available"
    calls = 0

    def probe():
        nonlocal calls
        calls += 1
        if outcome == "error":
            raise OSError("transient probe failure")
        if outcome == "unavailable":
            return module.RuntimeProbeResult(
                runtime_id="gromacs-2025.3",
                runtime_version=None,
                available=False,
                asset_ids=frozenset(),
                checked_at="2026-07-19T04:01:00Z",
                error_code="runtime_probe_failed",
            )
        return _probe("amber99sb-ildn.ff")

    catalog = module.ChemistryCatalog(config_dir=CATALOG_DIR, probe=probe)
    assert _profile(catalog, "gmx_amber99sb_ildn_tip3p_smoke_v1")["states"]["selectable"] is True
    outcome = "error"
    with pytest.raises(module.ChemistryCatalogError, match="runtime probe is unavailable"):
        catalog.refresh()

    outcome = "unavailable"
    smoke = _profile(catalog, "gmx_amber99sb_ildn_tip3p_smoke_v1")
    assert calls == 3
    assert smoke["states"]["selectable"] is False
    assert catalog.probe_summary()["available"] is False


def test_concurrent_readers_and_forced_refreshes_never_observe_mixed_catalog_state() -> None:
    module = _catalog_module()
    calls = 0

    def probe():
        nonlocal calls
        calls += 1
        if calls % 2:
            return _probe("amber99sb-ildn.ff")
        return module.RuntimeProbeResult(
            runtime_id="gromacs-2025.3",
            runtime_version=None,
            available=False,
            asset_ids=frozenset(),
            checked_at=f"refresh-{calls}",
            error_code="runtime_probe_failed",
        )

    catalog = module.ChemistryCatalog(config_dir=CATALOG_DIR, probe=probe, cache_ttl_seconds=60.0)
    failures: list[str] = []

    def read_catalog() -> None:
        try:
            for _ in range(100):
                profiles = catalog.list_profiles()
                for profile in profiles:
                    states = profile["states"]
                    assert not states["selectable"] or (
                        states["installed"]
                        and states["asset_probe_success"]
                        and states["runtime_validated"]
                        and states["scientifically_validated"]
                        and states["operator_enabled"]
                    )
                assert len(catalog.catalog_digest()) == 64
                assert isinstance(catalog.probe_summary()["available"], bool)
        except Exception as exc:  # pragma: no cover - collected below across threads
            failures.append(repr(exc))

    def refresh_catalog() -> None:
        try:
            for _ in range(40):
                catalog.refresh()
        except Exception as exc:  # pragma: no cover - collected below across threads
            failures.append(repr(exc))

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(read_catalog) for _ in range(8)]
        futures.extend(executor.submit(refresh_catalog) for _ in range(2))
        for future in futures:
            future.result()

    assert failures == []
    assert calls >= 2


def test_catalog_view_is_deeply_immutable_and_captures_one_generation() -> None:
    catalog = _stock_catalog("amber99sb-ildn.ff")

    view = catalog.view()

    assert view.generation == 1
    assert view.loaded_at.endswith("Z")
    assert view.catalog_digest
    assert view.profiles
    smoke = view.profile_index["gmx_amber99sb_ildn_tip3p_smoke_v1"]
    with pytest.raises(TypeError):
        view.profile_index["replacement"] = smoke
    with pytest.raises(TypeError):
        smoke["states"]["selectable"] = False


def test_captured_view_stays_coherent_when_forced_refresh_publishes_a_later_generation() -> None:
    module = _catalog_module()
    available = True

    def probe():
        if available:
            return _probe("amber99sb-ildn.ff")
        return module.RuntimeProbeResult(
            runtime_id="gromacs-2025.3",
            runtime_version=None,
            available=False,
            asset_ids=frozenset(),
            checked_at="2026-07-19T04:30:00Z",
            error_code="runtime_probe_failed",
        )

    catalog = module.ChemistryCatalog(config_dir=CATALOG_DIR, probe=probe)
    first = catalog.view()
    available = False
    catalog.refresh()
    second = catalog.view()

    first_smoke = first.profile_index["gmx_amber99sb_ildn_tip3p_smoke_v1"]
    second_smoke = second.profile_index["gmx_amber99sb_ildn_tip3p_smoke_v1"]
    assert first.generation == 1
    assert second.generation == 2
    assert first_smoke["states"]["selectable"] is True
    assert first.probe_summary["available"] is True
    assert second_smoke["states"]["selectable"] is False
    assert second.probe_summary["available"] is False
    assert first.catalog_digest != second.catalog_digest


def test_profile_selection_can_be_bound_to_one_previously_captured_view() -> None:
    module = _catalog_module()
    available = True

    def probe():
        if available:
            return _probe("amber99sb-ildn.ff")
        return module.RuntimeProbeResult(
            runtime_id="gromacs-2025.3",
            runtime_version=None,
            available=False,
            asset_ids=frozenset(),
            checked_at="2026-07-19T04:31:00Z",
            error_code="runtime_probe_failed",
        )

    catalog = module.ChemistryCatalog(config_dir=CATALOG_DIR, probe=probe)
    view = catalog.view()
    smoke = view.profile_index["gmx_amber99sb_ildn_tip3p_smoke_v1"]
    selection = {
        "profile_id": smoke["id"],
        "profile_sha256": smoke["profile_sha256"],
        "force_field": "amber99sb-ildn",
        "water_model": "tip3p",
        "engine": "gromacs",
        "requested_scope": "smoke_auto",
    }
    available = False
    catalog.refresh()

    selected = catalog.validate_v1_profile_selection(**selection, view=view)

    assert selected["states"]["selectable"] is True
    with pytest.raises(module.ChemistryProfileSelectionError):
        catalog.validate_v1_profile_selection(**selection)


def test_catalog_api_routes_are_registered_reachable_and_do_not_expose_host_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _catalog_module()
    router_module = importlib.import_module("routers.molecular_dynamics")
    main = importlib.import_module("main")
    catalog = _stock_catalog("amber99sb-ildn.ff")
    monkeypatch.setenv("BMS_FEATURE_MOLECULAR_DYNAMICS", "1")
    monkeypatch.setattr(router_module, "get_chemistry_catalog", lambda: catalog)

    paths = main.app.openapi()["paths"]
    assert "/api/molecular-dynamics/capabilities" in paths
    assert "/api/molecular-dynamics/chemistry-profiles" in paths
    assert "/api/molecular-dynamics/chemistry-profiles/{profile_id}" in paths
    assert "/api/molecular-dynamics/chemistry-profiles/refresh" not in paths

    client = TestClient(main.app)
    capabilities = client.get("/api/molecular-dynamics/capabilities")
    inventory = client.get("/api/molecular-dynamics/chemistry-profiles")
    detail = client.get("/api/molecular-dynamics/chemistry-profiles/gmx_amber99sb_ildn_tip3p_smoke_v1")
    missing = client.get("/api/molecular-dynamics/chemistry-profiles/not_a_profile")

    assert capabilities.status_code == 200
    assert capabilities.json()["contract_schemas"] == ["bms.md.job.v1"]
    assert inventory.status_code == 200
    assert inventory.json()["selectable_profile_ids"] == ["gmx_amber99sb_ildn_tip3p_smoke_v1"]
    assert detail.status_code == 200
    assert detail.json()["id"] == "gmx_amber99sb_ildn_tip3p_smoke_v1"
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "MD_CHEMISTRY_PROFILE_UNKNOWN"

    public_payload = json.dumps(
        [capabilities.json(), inventory.json(), detail.json()],
        sort_keys=True,
    )
    assert "/mnt/" not in public_payload
    assert "/home/" not in public_payload
    assert "BMS_CONTAINER_DIR" not in public_payload


def test_each_catalog_route_captures_exactly_one_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router_module = importlib.import_module("routers.molecular_dynamics")
    main = importlib.import_module("main")
    catalog = _stock_catalog("amber99sb-ildn.ff")
    original_view = catalog.view
    calls = 0

    def one_view():
        nonlocal calls
        calls += 1
        return original_view()

    monkeypatch.setattr(catalog, "view", one_view)
    for method_name in ("list_profiles", "get_profile", "catalog_digest", "probe_summary"):
        monkeypatch.setattr(
            catalog,
            method_name,
            lambda *args, _method=method_name, **kwargs: pytest.fail(
                f"route called separate public catalog method {_method}"
            ),
        )
    monkeypatch.setattr(router_module, "get_chemistry_catalog", lambda: catalog)
    client = TestClient(main.app)

    for path in (
        "/api/molecular-dynamics/capabilities",
        "/api/molecular-dynamics/chemistry-profiles",
        "/api/molecular-dynamics/chemistry-profiles/gmx_amber99sb_ildn_tip3p_smoke_v1",
    ):
        before = calls
        response = client.get(path)
        assert response.status_code == 200
        assert calls == before + 1


def test_every_catalog_route_maps_catalog_failure_to_sanitized_typed_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _catalog_module()
    router_module = importlib.import_module("routers.molecular_dynamics")
    main = importlib.import_module("main")

    class BrokenCatalog:
        def view(self):
            raise module.ChemistryCatalogError("failed at /home/operator/private/catalog.yaml")

    monkeypatch.setattr(router_module, "get_chemistry_catalog", BrokenCatalog)
    client = TestClient(main.app)

    for path in (
        "/api/molecular-dynamics/capabilities",
        "/api/molecular-dynamics/chemistry-profiles",
        "/api/molecular-dynamics/chemistry-profiles/gmx_amber99sb_ildn_tip3p_smoke_v1",
    ):
        response = client.get(path)
        assert response.status_code == 503
        assert response.json()["detail"] == {
            "code": "MD_LAUNCH_SERVICE_UNAVAILABLE",
            "message": "The molecular-dynamics launch service is temporarily unavailable.",
        }
        assert "/home/" not in response.text
