from __future__ import annotations

import ast
import json
from pathlib import Path

from fastapi import FastAPI

from routers import molbio_ops


ROOT = Path(__file__).resolve().parents[3]
FRONTEND_CATALOG = (
    ROOT
    / "platform/frontend/src/components/MolBioToolkit/utils/restrictionEnzymes.ts"
)


def _active_source(paths: list[Path]) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in paths if path.exists())


def test_frontend_restriction_catalog_and_browser_science_authority_are_absent() -> None:
    assert not FRONTEND_CATALOG.exists()
    frontend_sources = list((ROOT / "platform/frontend/src").rglob("*.ts")) + list(
        (ROOT / "platform/frontend/src").rglob("*.tsx")
    )
    source = _active_source(frontend_sources)
    forbidden = {
        "RESTRICTION_ENZYME_GROUPS",
        "ALL_RESTRICTION_ENZYMES",
        "findRestrictionSiteMatches",
        "findRestrictionSites(",
        "reverseComplementSite",
        "/api/molbio/digest",
        "enzymes: { name: string; site?: string }[]",
    }
    assert forbidden.isdisjoint({token for token in forbidden if token in source})


def test_legacy_digest_fresh_write_route_and_geometry_models_are_absent() -> None:
    app = FastAPI()
    app.include_router(molbio_ops.router)
    document = app.openapi()
    assert "/api/molbio/digest" not in document["paths"]
    assert "DigestRequest" not in document["components"]["schemas"]
    source = (ROOT / "platform/api/routers/molbio_ops.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    class_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    assert {"EnzymeSchema", "DigestRequest", "DigestFragmentResponse"}.isdisjoint(class_names)

    service_source = (ROOT / "platform/api/services/molbio_ops.py").read_text(encoding="utf-8")
    service_tree = ast.parse(service_source)
    service_names = {
        node.name
        for node in ast.walk(service_tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert {"DigestEnzyme", "DigestFragment", "digest_sequence", "golden_gate_assembly"}.isdisjoint(
        service_names
    )


def test_restriction_specific_sequence_utils_are_not_exported_or_tracked() -> None:
    package = ROOT / "packages/sequence-utils/src"
    retired = {
        "aliasedEnzymesByName.js",
        "computeDigestFragments.js",
        "cutSequenceByRestrictionEnzyme.js",
        "defaultEnzymesByName.js",
        "doesEnzymeChopOutsideOfRecognitionSite.js",
        "getCutsitesFromSequence.js",
        "getCutsiteType.js",
        "getDigestFragmentsForCutsites.js",
        "getDigestFragmentsForRestrictionEnzymes.js",
        "getPossiblePartsFromSequenceAndEnzymes.js",
        "getVirtualDigest.js",
        "isEnzymeType2S.js",
    }
    assert not [name for name in sorted(retired) if (package / name).exists()]
    index_source = (package / "index.js").read_text(encoding="utf-8")
    assert all(name.removesuffix(".js") not in index_source for name in retired)


def test_governed_contracts_publish_backend_owned_restriction_operations() -> None:
    golden_gate = json.loads(
        (ROOT / "schemas/ngs_molbio/molbio-assembly-golden_gate-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert "enzyme_id" in golden_gate["properties"]
    assert "enzyme_name" not in golden_gate["properties"]
    assert "enzyme_id" in golden_gate["required"]
    assert {"catalog_id", "catalog_sha256"} <= set(golden_gate["required"])

    registry = json.loads(
        (ROOT / "platform/api/config/ngs_molbio/schema_registry_v2.json").read_text(
            encoding="utf-8"
        )
    )
    registered = {entry["schema_id"] for entry in registry["entries"]}
    assert "bms.operation-parameters.molbio.restriction_digest.v2" in registered

    inventory = json.loads(
        (ROOT / "platform/api/config/ngs_molbio/capability_inventory_v2.json").read_text(
            encoding="utf-8"
        )
    )
    capabilities = {row["capability_id"]: row for row in inventory["capabilities"]}
    digest = capabilities["molbio.restriction_digest"]
    golden_gate_capability = capabilities["molbio.assembly.golden_gate"]
    assert golden_gate_capability["native_mapping"]["native_request_compatibility"] == "partial_native_mapping"
    assert "fragments" in golden_gate_capability["classified_parameter_keys"]
    assert golden_gate_capability["unclassified_parameter_keys"] == []
    assert digest["native_mapping"]["source"] == "POST /api/molbio/restriction/digests"
    assert "enzyme_ids" in digest["classified_parameter_keys"]
    assert not {"site", "recognition_site", "cut_index", "cut_offset"}.intersection(
        digest["observed_parameter_keys"]
    )

    project_hub_source = (
        ROOT / "platform/api/routers/ngs_molbio_n5.py"
    ).read_text(encoding="utf-8")
    assert '"restriction_digest": "restriction_digest"' in project_hub_source
    assert '"legacy_inexact"' in project_hub_source
    assert "__import__(" not in project_hub_source
