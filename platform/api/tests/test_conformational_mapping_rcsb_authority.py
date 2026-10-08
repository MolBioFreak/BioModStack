from __future__ import annotations

import hashlib
import io
import json
from types import SimpleNamespace

import httpx
import pytest
from Bio.PDB.MMCIF2Dict import MMCIF2Dict
from fastapi import HTTPException

from database import ConformationalMappingSource
import routers.conformational_mapping as cm_router


def _multi_context_mmcif() -> bytes:
    rows: list[str] = []
    atom_id = 1
    for model_id in ("1", "2"):
        for label_chain, auth_chain, entity_id, offset in (
            ("L1", "X", "1", 0),
            ("L2", "Y", "2", 20),
        ):
            for label_seq_id, auth_seq_id, residue, residue_offset in (
                ("1", "10", "ALA", 0),
                ("2", "11", "GLY", 4),
            ):
                for element, atom_name, atom_offset in (
                    ("N", "N", 0),
                    ("C", "CA", 1),
                    ("C", "C", 2),
                    ("O", "O", 3),
                ):
                    x = offset + residue_offset + atom_offset + int(model_id) * 100
                    rows.append(
                        f"ATOM {atom_id} {element} {atom_name} . {residue} {label_chain} "
                        f"{entity_id} {label_seq_id} ? {x} 0 0 1.00 10.00 {auth_seq_id} "
                        f"{residue} {auth_chain} {atom_name} {model_id}"
                    )
                    atom_id += 1
    return (
        "data_1ABC\n"
        "_entry.id 1ABC\n"
        "loop_\n"
        "_entity.id\n"
        "_entity.type\n"
        "1 polymer\n"
        "2 polymer\n"
        "loop_\n"
        "_struct_asym.id\n"
        "_struct_asym.entity_id\n"
        "L1 1\n"
        "L2 2\n"
        "loop_\n"
        "_entity_poly.entity_id\n"
        "_entity_poly.type\n"
        "_entity_poly.pdbx_seq_one_letter_code_can\n"
        "1 'polypeptide(L)' AG\n"
        "2 'polypeptide(L)' AG\n"
        "loop_\n"
        "_entity_poly_seq.entity_id\n"
        "_entity_poly_seq.num\n"
        "_entity_poly_seq.mon_id\n"
        "_entity_poly_seq.hetero\n"
        "1 1 ALA n\n"
        "1 2 GLY n\n"
        "2 1 ALA n\n"
        "2 2 GLY n\n"
        "loop_\n"
        "_atom_site.group_PDB\n"
        "_atom_site.id\n"
        "_atom_site.type_symbol\n"
        "_atom_site.label_atom_id\n"
        "_atom_site.label_alt_id\n"
        "_atom_site.label_comp_id\n"
        "_atom_site.label_asym_id\n"
        "_atom_site.label_entity_id\n"
        "_atom_site.label_seq_id\n"
        "_atom_site.pdbx_PDB_ins_code\n"
        "_atom_site.Cartn_x\n"
        "_atom_site.Cartn_y\n"
        "_atom_site.Cartn_z\n"
        "_atom_site.occupancy\n"
        "_atom_site.B_iso_or_equiv\n"
        "_atom_site.auth_seq_id\n"
        "_atom_site.auth_comp_id\n"
        "_atom_site.auth_asym_id\n"
        "_atom_site.auth_atom_id\n"
        "_atom_site.pdbx_PDB_model_num\n"
        + "\n".join(rows)
        + "\n"
    ).encode("utf-8")


def _request(accession: str = "1abc"):
    request = cm_router.Request({
        "type": "http",
        "method": "POST",
        "scheme": "http",
        "path": f"/api/conformational-mapping/sources/rcsb/{accession}",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 42000),
        "server": ("127.0.0.1", 8000),
    })
    request.state.authenticated_principal = {"subject": "test-operator", "roles": ["operator"]}
    return request


def _mock_rcsb_client(handler):
    return lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    )


class _RegistrationSession:
    def __init__(self) -> None:
        self.source = None

    async def get(self, model, source_id):
        assert model is ConformationalMappingSource
        assert source_id == "cm_src_rcsb_selected"
        return self.source


@pytest.mark.asyncio
async def test_rcsb_search_enumerates_only_server_materializable_asymmetric_unit_contexts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_bytes = _multi_context_mmcif()

    async def no_cached_entries(principal_id, session):
        assert principal_id == "test-operator"
        return []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rest/v1/core/entry/1ABC":
            return httpx.Response(
                200,
                request=request,
                json={
                    "struct": {"title": "Authoritative two-chain entry"},
                    "exptl": [{"method": "X-RAY DIFFRACTION"}],
                    "rcsb_entry_info": {"resolution_combined": [1.5]},
                },
            )
        if request.url.path == "/download/1ABC.cif":
            return httpx.Response(200, request=request, content=source_bytes)
        raise AssertionError(f"unexpected RCSB request: {request.url}")

    monkeypatch.setattr(cm_router, "_cached_rcsb_entries", no_cached_entries)
    monkeypatch.setattr(cm_router, "_rcsb_http_client", _mock_rcsb_client(handler))
    result = await cm_router.search_rcsb_sources(
        _request(), keyword=None, accession="1abc", limit=10, session=object()  # type: ignore[arg-type]
    )

    assert result["cached"] is False
    entry = result["entries"][0]
    assert entry["title"] == "Authoritative two-chain entry"
    assert entry["experimental_methods"] == ["X-RAY DIFFRACTION"]
    assert entry["resolution"] == 1.5
    assert entry["models"] == [
        {"model_id": "1", "label": "Model 1"},
        {"model_id": "2", "label": "Model 2"},
    ]
    assert entry["samples"] == [
        {"sample_id": "asymmetric-unit", "label": "Deposited asymmetric unit"}
    ]
    assert entry["chains"] == [
        {
            "chain_id": "X",
            "label": "Author chain X (label asym L1)",
            "entity_id": "1",
            "entity_type": "protein",
            "residue_count": 2,
        },
        {
            "chain_id": "Y",
            "label": "Author chain Y (label asym L2)",
            "entity_id": "2",
            "entity_type": "protein",
            "residue_count": 2,
        },
    ]
    assert entry["entities"] == [
        {"entity_id": "1", "label": "Protein entity 1", "entity_type": "protein", "residue_count": 2},
        {"entity_id": "2", "label": "Protein entity 2", "entity_type": "protein", "residue_count": 2},
    ]
    assert entry["required_selection"] == ["model_id", "sample_id", "chain_ids", "entity_ids"]


@pytest.mark.asyncio
async def test_rcsb_registration_materializes_exact_selected_model_chain_entity_and_binds_digests(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    downloaded = _multi_context_mmcif()
    captured: dict[str, object] = {}
    session = _RegistrationSession()

    async def fake_register_source(**kwargs):
        payload = await kwargs["file"].read()
        captured.update(kwargs)
        captured["payload"] = payload
        digest = hashlib.sha256(payload).hexdigest()
        session.source = SimpleNamespace(
            source_id="cm_src_rcsb_selected",
            source_kind="structure_upload",
            metadata_json=json.loads(kwargs["metadata_json"]),
            content_sha256=digest,
            size_bytes=len(payload),
            principal_id="test-operator",
        )
        return {
            "source_id": "cm_src_rcsb_selected",
            "source_kind": "structure_upload",
            "format": "mmcif",
            "sha256": digest,
            "bytes": len(payload),
            "metadata": session.source.metadata_json,
        }

    monkeypatch.setattr(cm_router, "get_data_root", lambda: tmp_path)
    monkeypatch.setattr(
        cm_router,
        "_rcsb_http_client",
        _mock_rcsb_client(
            lambda request: httpx.Response(200, request=request, content=downloaded)
        ),
    )
    monkeypatch.setattr(cm_router, "register_source", fake_register_source)

    result = await cm_router.register_rcsb_mmcif_source(
        "1abc",
        _request(),
        session,  # type: ignore[arg-type]
        cm_router.RcsbSelection(
            accession="1ABC",
            model_id="2",
            sample_id="asymmetric-unit",
            chain_ids=["Y"],
            entity_ids=["2"],
        ),
    )

    materialized = captured["payload"]
    assert isinstance(materialized, bytes)
    assert materialized != downloaded
    parsed = MMCIF2Dict(io.StringIO(materialized.decode("utf-8")))
    assert set(parsed["_atom_site.pdbx_PDB_model_num"]) == {"2"}
    assert set(parsed["_atom_site.auth_asym_id"]) == {"Y"}
    assert set(parsed["_atom_site.label_asym_id"]) == {"L2"}
    assert set(parsed["_atom_site.label_entity_id"]) == {"2"}
    assert parsed["_entity.id"] == ["2"]
    assert parsed["_struct_asym.id"] == ["L2"]
    assert parsed["_struct_asym.entity_id"] == ["2"]

    receipt_payload = result["authority_receipt"]["payload"]
    resolved = receipt_payload["selection"]
    assert resolved == {
        "accession": "1ABC",
        "model_id": "2",
        "sample_id": "asymmetric-unit",
        "chain_ids": ["Y"],
        "entity_ids": ["2"],
    }
    materialized_digest = hashlib.sha256(materialized).hexdigest()
    assert receipt_payload["source_sha256"] == materialized_digest
    assert receipt_payload["download_sha256"] == hashlib.sha256(downloaded).hexdigest()
    assert result["authority_receipt"]["content_sha256"] == materialized_digest
    assert receipt_payload["materialization"] == "selected_asymmetric_unit_context_v1"
    assert "assembly" not in json.dumps(receipt_payload).casefold()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("selection", "message"),
    [
        (cm_router.RcsbSelection(accession="1ABC"), "ambiguous"),
        (
            cm_router.RcsbSelection(
                accession="1ABC",
                model_id="1",
                sample_id="asymmetric-unit",
                chain_ids=["X"],
                entity_ids=["2"],
            ),
            "chain/entity",
        ),
        (
            cm_router.RcsbSelection(
                accession="1ABC",
                model_id="1",
                sample_id="biological-assembly-1",
                chain_ids=["X"],
                entity_ids=["1"],
            ),
            "sample",
        ),
    ],
)
async def test_rcsb_registration_rejects_ambiguous_or_unsupported_context_before_registration(
    monkeypatch: pytest.MonkeyPatch,
    selection: cm_router.RcsbSelection,
    message: str,
) -> None:
    registered = False

    async def should_not_register(**kwargs):
        nonlocal registered
        registered = True
        raise AssertionError("unsupported RCSB context reached immutable registration")

    monkeypatch.setattr(
        cm_router,
        "_rcsb_http_client",
        _mock_rcsb_client(
            lambda request: httpx.Response(200, request=request, content=_multi_context_mmcif())
        ),
    )
    monkeypatch.setattr(cm_router, "register_source", should_not_register)

    with pytest.raises(HTTPException, match=message) as error:
        await cm_router.register_rcsb_mmcif_source(
            "1abc", _request(), _RegistrationSession(), selection  # type: ignore[arg-type]
        )
    assert error.value.status_code == 422
    assert registered is False
