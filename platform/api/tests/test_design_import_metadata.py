from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from database import Design
from routers.designs import _design_to_response


def test_design_response_surfaces_generic_import_metadata_for_esmfold_rows() -> None:
    design = Design(
        id="external-esmfold-design-1",
        job_id="external-import-job-1",
        name="external_import_001",
        stage_family="validation",
        stage_mode="bundle_import",
        artifact_class="imported_structure",
        provenance={
            "dataset_name": "Selected submissions",
        },
        confidence_metrics={
            "esmfold_plddt": 42.09418604651162,
            "structure_prediction_url": "https://example.org/esmfold-default.cif",
        },
        is_favorite=False,
        created_at=datetime(2026, 4, 14, 18, 0, 0),
    )

    response = _design_to_response(design)

    assert response.is_imported is True
    assert response.import_source == "external"
    assert response.import_method == "esmfold"
    assert response.import_label == "Imported • ESMFold"


def test_design_response_inferrs_boltz2_import_method_from_generic_metrics() -> None:
    design = Design(
        id="external-boltz-design-1",
        job_id="external-import-job-1",
        name="external_import_002",
        stage_family="validation",
        stage_mode="bundle_import",
        artifact_class="imported_structure",
        provenance={
            "dataset_name": "Selected submissions",
        },
        confidence_metrics={
            "boltz2_ptm": 0.66,
            "boltz2_iptm": 0.89,
            "boltz2_complex_iplddt": 0.75,
            "boltz2_complex_pde": 0.60,
            "boltz2_ipsae": 0.58,
        },
        is_favorite=False,
        created_at=datetime(2026, 4, 14, 18, 0, 0),
    )

    response = _design_to_response(design)

    assert response.is_imported is True
    assert response.import_source == "external"
    assert response.import_method == "boltz2"
    assert response.import_label == "Imported • Boltz2"


def test_design_response_leaves_native_validation_rows_unflagged() -> None:
    design = Design(
        id="native-validation-design-1",
        job_id="native-validation-job-1",
        name="native_validation_001",
        stage_family="validation",
        stage_mode="post_structure_validation",
        ptm=0.66,
        iptm=0.89,
        complex_iplddt=0.75,
        complex_ipde=0.60,
        ipsae=0.58,
        is_favorite=False,
        created_at=datetime(2026, 4, 14, 18, 0, 0),
    )

    response = _design_to_response(design)

    assert response.is_imported is False
    assert response.import_source is None
    assert response.import_method is None
    assert response.import_label is None
