from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
GENERATOR = REPO_ROOT / "scripts/build_restriction_enzyme_catalog.py"
CATALOG = REPO_ROOT / "platform/api/config/molbio/restriction/restriction_enzyme_catalog_v1.json"
MANIFEST = REPO_ROOT / "platform/api/config/molbio/restriction/restriction_enzyme_catalog_manifest_v1.json"
VALIDATOR = REPO_ROOT / "platform/api/restriction_catalog_integrity.py"


def _module(path: Path, name: str):
    assert path.is_file(), f"missing reusable integrity module: {path}"
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _generator():
    return _module(GENERATOR, "restriction_catalog_generator_quality")


def _validator():
    return _module(VALIDATOR, "restriction_catalog_integrity_quality")


def _catalog() -> dict:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _records() -> dict[str, dict]:
    return {row["canonical_name"]: row for row in _catalog()["records"]}


def _record(name: str, *, enzyme_id: str | None = None) -> dict:
    row = copy.deepcopy(_records()[name])
    row["enzyme_id"] = enzyme_id or name
    row["canonical_name"] = enzyme_id or name
    row["source"]["canonical_name"] = enzyme_id or name
    row.pop("record_sha256", None)
    return row


def _mutated_catalog(mutator) -> dict:
    catalog = _catalog()
    mutator(catalog)
    return catalog


def _assert_invalid(catalog: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        _validator().validate_catalog_integrity(catalog)


def test_unknown_geometry_never_creates_geometry_relationships() -> None:
    generator = _generator()
    known = _record("EcoRI", enzyme_id="Known")
    unknown_a = _record("Aba13301I", enzyme_id="UnknownA")
    unknown_b = _record("Aba13301I", enzyme_id="UnknownB")
    for row in (known, unknown_a, unknown_b):
        row["recognition"] = copy.deepcopy(known["recognition"])
    generator.apply_relationships([known, unknown_a, unknown_b])

    assert known["relationships"]["equischizomer_ids"] == []
    assert known["relationships"]["neoschizomer_ids"] == []
    for row in (unknown_a, unknown_b):
        assert row["relationships"]["equischizomer_group_id"] is None
        assert row["relationships"]["equischizomer_ids"] == []
        assert row["relationships"]["neoschizomer_ids"] == []
    assert len({row["relationships"]["isoschizomer_group_id"] for row in (known, unknown_a, unknown_b)}) == 1


def test_geometry_relationships_compare_only_known_double_strand_records() -> None:
    generator = _generator()
    first = _record("EcoRI", enzyme_id="First")
    same = _record("EcoRI", enzyme_id="Same")
    different = _record("BamHI", enzyme_id="Different")
    nick = _record("Nt.BbvCI", enzyme_id="Nick")
    unknown = _record("Aba13301I", enzyme_id="Unknown")
    unsupported = _record("EcoRI", enzyme_id="Unsupported")
    unsupported["enzyme_kind"] = "restriction_enzyme_geometry_unresolved"
    for row in (same, different, nick, unknown, unsupported):
        row["recognition"] = copy.deepcopy(first["recognition"])
    different["cleavage"]["events"][0]["top_offset"] += 1
    generator.apply_relationships([first, same, different, nick, unknown, unsupported])

    assert first["relationships"]["equischizomer_ids"] == ["Same"]
    assert first["relationships"]["neoschizomer_ids"] == ["Different"]
    for row in (nick, unknown, unsupported):
        assert row["relationships"]["equischizomer_ids"] == []
        assert row["relationships"]["neoschizomer_ids"] == []


def test_stable_ids_fail_closed_on_casefold_collision_without_rewriting_prior_id() -> None:
    generator = _generator()
    assert generator.stable_ids(["EcoRI"]) == {"EcoRI": "EcoRI"}
    with pytest.raises(ValueError, match="case-fold collision"):
        generator.stable_ids(["EcoRI", "ECORI"])
    assert generator.stable_ids(["EcoRI"])["EcoRI"] == "EcoRI"


@pytest.mark.parametrize(
    ("field_path", "category"),
    [
        (("recognition", "site_iupac"), "recognition_changes"),
        (("canonical_name",), "identity_changes"),
        (("source", "record_id"), "source_provenance_changes"),
        (("source", "uri"), "source_provenance_changes"),
        (("source", "dictionary_sha256"), "source_provenance_changes"),
        (("enzyme_kind",), "enzyme_kind_capability_exclusion_changes"),
        (("analysis_capability",), "enzyme_kind_capability_exclusion_changes"),
        (("exclusion_reason",), "enzyme_kind_capability_exclusion_changes"),
        (("aliases",), None),
        (("supplier_provenance", "reported_commercial"), "historical_supplier_code_commercial_report_changes"),
    ],
)
def test_change_report_exhaustively_reports_every_canonical_mutation(
    field_path: tuple[str, ...], category: str | None
) -> None:
    generator = _generator()
    old_record = copy.deepcopy(_records()["EcoRI"])
    new_record = copy.deepcopy(old_record)
    target = new_record
    for key in field_path[:-1]:
        target = target[key]
    key = field_path[-1]
    value = target[key]
    if isinstance(value, bool):
        target[key] = not value
    elif isinstance(value, list):
        target[key] = [*value, "synthetic-alias"]
    elif value is None:
        target[key] = "synthetic"
    elif isinstance(value, int):
        target[key] = value + 1
    else:
        target[key] = f"{value}-mutated"
    old = {"catalog_id": "old", "content_sha256": "a" * 64, "records": [old_record]}
    new = {"catalog_id": "new", "content_sha256": "b" * 64, "records": [new_record]}

    report = generator.build_change_report(old, new)

    assert report["summary"]["record_changes"] == 1
    assert report["changes"]["record_changes"] == [
        {"enzyme_id": "EcoRI", "changed_fields": [".".join(field_path)]}
    ]
    if category is not None:
        assert report["changes"][category] == ["EcoRI"]
    assert sum(report["summary"].values()) > 0


def test_change_report_detects_canonical_bytes_not_covered_by_named_categories() -> None:
    generator = _generator()
    old_record = copy.deepcopy(_records()["EcoRI"])
    new_record = copy.deepcopy(old_record)
    new_record["aliases"] = ["RsrI"]
    old = {"catalog_id": "old", "content_sha256": "a" * 64, "records": [old_record]}
    new = {"catalog_id": "new", "content_sha256": "b" * 64, "records": [new_record]}
    report = generator.build_change_report(old, new)
    assert report["summary"]["record_changes"] == 1
    assert report["changes"]["record_changes"][0]["changed_fields"] == ["aliases"]


def test_recognition_projection_rejects_unequal_or_invalid_alternatives() -> None:
    generator = _generator()
    with pytest.raises(ValueError, match="equal length"):
        generator.recognition_projection("GAATTC|GATC")
    with pytest.raises(ValueError, match="IUPAC"):
        generator.recognition_projection("GAATTC|")
    projection = generator.recognition_projection("GAATTC|GAGTTC")
    assert projection["length_bp"] == 6


def test_reusable_validator_accepts_checked_catalog_and_manifest() -> None:
    _validator().validate_catalog_integrity(_catalog(), _manifest())


def _mutate_nick_geometry(catalog: dict) -> None:
    record = next(row for row in catalog["records"] if row["canonical_name"] == "Nt.BbvCI")
    record["cleavage"]["nick"]["boundary_offset"] = 3
    record["cleavage"]["nick"]["reverse_orientation"]["boundary_offset"] = 4


@pytest.mark.parametrize(
    ("label", "mutator", "message"),
    [
        (
            "wrong EcoRI overhang",
            lambda c: c["records"][next(i for i, r in enumerate(c["records"]) if r["canonical_name"] == "EcoRI")]["cleavage"]["events"][0].update(overhang_kind="three_prime", overhang_length_nt=1),
            "overhang",
        ),
        (
            "wrong DSB capability",
            lambda c: c["records"][next(i for i, r in enumerate(c["records"]) if r["canonical_name"] == "EcoRI")].update(analysis_capability="recognition_only"),
            "capability",
        ),
        (
            "wrong unresolved exclusion",
            lambda c: c["records"][next(i for i, r in enumerate(c["records"]) if r["cleavage"]["status"] == "unknown")].update(exclusion_reason=None),
            "exclusion",
        ),
        (
            "wrong reverse complement",
            lambda c: c["records"][0]["recognition"].update(reverse_complement_iupac="AAAA"),
            "reverse complement",
        ),
        (
            "dangling relation",
            lambda c: c["records"][0]["relationships"]["neoschizomer_ids"].append("missing-enzyme"),
            "relationship reference",
        ),
        (
            "self relation",
            lambda c: c["records"][0]["relationships"]["equischizomer_ids"].append(c["records"][0]["enzyme_id"]),
            "self relationship",
        ),
        (
            "duplicate record and ID",
            lambda c: c["records"].append(copy.deepcopy(c["records"][0])),
            "duplicate enzyme_id",
        ),
        (
            "duplicate case-fold name",
            lambda c: c["records"][1].update(canonical_name=c["records"][0]["canonical_name"].swapcase()),
            "case-fold canonical_name",
        ),
        (
            "wrong group",
            lambda c: c["records"][0]["relationships"].update(isoschizomer_group_id="sha256:" + "0" * 64),
            "isoschizomer group",
        ),
        (
            "unequal alternatives",
            lambda c: c["records"][0]["recognition"].update(site_alternatives_iupac=[c["records"][0]["recognition"]["site_iupac"], "A"]),
            "equal length",
        ),
        (
            "bad nick reverse arithmetic",
            lambda c: c["records"][next(i for i, r in enumerate(c["records"]) if r["canonical_name"] == "Nt.BbvCI")]["cleavage"]["nick"]["reverse_orientation"].update(boundary_offset=999),
            "nick reverse",
        ),
        (
            "coherently altered nick canonical geometry",
            _mutate_nick_geometry,
            "nick canonical",
        ),
    ],
)
def test_validator_independently_rejects_scientific_integrity_adversaries(
    label: str, mutator, message: str
) -> None:
    catalog = _catalog()
    mutator(catalog)
    _assert_invalid(catalog, message)


def test_validator_rejects_record_digest_count_order_and_catalog_digest_drift() -> None:
    cases = [
        (lambda c: c["records"][0].update(record_sha256="0" * 64), "record_sha256"),
        (lambda c: c["counts"].update(total_discoverable=1), "count summary"),
        (lambda c: c["records"].reverse(), "sorted order"),
        (lambda c: c.update(content_sha256="0" * 64), "catalog content_sha256"),
    ]
    for mutator, message in cases:
        catalog = _catalog()
        mutator(catalog)
        _assert_invalid(catalog, message)


def test_validator_rejects_manifest_summary_order_and_digest_drift() -> None:
    validator = _validator()
    catalog = _catalog()
    cases = [
        (lambda m: m["counts"].update(total_discoverable=1), "manifest count"),
        (lambda m: m["records"].reverse(), "manifest record order"),
        (lambda m: m.update(content_sha256="0" * 64), "manifest content_sha256"),
    ]
    for mutator, message in cases:
        manifest = _manifest()
        mutator(manifest)
        with pytest.raises(ValueError, match=message):
            validator.validate_catalog_integrity(catalog, manifest)


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, str(GENERATOR), *args],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def test_cli_requires_explicit_initial_release_or_prior_catalog(tmp_path: Path) -> None:
    common = (
        "--catalog-output", str(tmp_path / "catalog.json"),
        "--manifest-output", str(tmp_path / "manifest.json"),
        "--change-report-output", str(tmp_path / "report.json"),
    )
    rejected = _run_cli(*common)
    assert rejected.returncode == 2
    assert "--initial-release" in rejected.stderr
    accepted = _run_cli(*common, "--initial-release")
    assert accepted.returncode == 0, accepted.stderr


def test_cli_rejects_aliased_output_paths(tmp_path: Path) -> None:
    shared = tmp_path / "shared.json"
    result = _run_cli(
        "--catalog-output", str(shared),
        "--manifest-output", str(shared),
        "--change-report-output", str(tmp_path / "report.json"),
        "--initial-release",
    )
    assert result.returncode == 2
    assert "distinct" in result.stderr
    assert not shared.exists()


def test_cli_rejects_hard_link_aliased_output_paths(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    manifest = tmp_path / "manifest.json"
    catalog.write_text("preserve", encoding="utf-8")
    os.link(catalog, manifest)
    result = _run_cli(
        "--catalog-output", str(catalog),
        "--manifest-output", str(manifest),
        "--change-report-output", str(tmp_path / "report.json"),
        "--initial-release",
    )
    assert result.returncode == 2
    assert "distinct" in result.stderr
    assert catalog.read_text(encoding="utf-8") == "preserve"


def test_prior_reader_bounds_open_descriptor_and_rejects_special_files(
    tmp_path: Path, monkeypatch
) -> None:
    generator = _generator()
    generator.MAX_PRIOR_CATALOG_BYTES = 16
    oversized = tmp_path / "underreported.json"
    oversized.write_bytes(b"{" + b" " * 16)
    original_stat = Path.stat

    def underreported_stat(path: Path, *args, **kwargs):
        observed = original_stat(path, *args, **kwargs)
        if path == oversized:
            return os.stat_result((observed.st_mode, observed.st_ino, observed.st_dev, 1, observed.st_uid, observed.st_gid, 1, observed.st_atime, observed.st_mtime, observed.st_ctime))
        return observed

    monkeypatch.setattr(Path, "stat", underreported_stat)
    with pytest.raises(ValueError, match="too large"):
        generator.load_prior_catalog(oversized)
    with pytest.raises(ValueError, match="regular file"):
        generator.load_prior_catalog(Path("/dev/null"))


def test_cli_bounds_and_controls_prior_catalog_errors(tmp_path: Path) -> None:
    outputs = (
        "--catalog-output", str(tmp_path / "catalog.json"),
        "--manifest-output", str(tmp_path / "manifest.json"),
        "--change-report-output", str(tmp_path / "report.json"),
    )
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{", encoding="utf-8")
    malformed_result = _run_cli(*outputs, "--prior-catalog", str(malformed))
    assert malformed_result.returncode == 2
    assert "prior catalog" in malformed_result.stderr
    assert "Traceback" not in malformed_result.stderr

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}", encoding="utf-8")
    invalid_result = _run_cli(*outputs, "--prior-catalog", str(invalid))
    assert invalid_result.returncode == 2
    assert "schema" in invalid_result.stderr
    assert "Traceback" not in invalid_result.stderr

    oversized = tmp_path / "oversized.json"
    with oversized.open("wb") as handle:
        handle.truncate(_generator().MAX_PRIOR_CATALOG_BYTES + 1)
    oversized_result = _run_cli(*outputs, "--prior-catalog", str(oversized))
    assert oversized_result.returncode == 2
    assert "too large" in oversized_result.stderr
    assert "Traceback" not in oversized_result.stderr
