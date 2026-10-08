from pathlib import Path
import re
import sys

import pytest


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.nextflow import resolve_nextflow_entrypoint  # noqa: E402


RETIRED_BINDER_ID = "bind" + "craft"


def test_retired_workflow_files_are_deleted() -> None:
    retired_paths = [
        "apptainer/" + RETIRED_BINDER_ID + ".def",
        "modules/" + RETIRED_BINDER_ID + ".nf",
        "modules/free" + RETIRED_BINDER_ID + ".nf",
        "scripts/spawn_" + RETIRED_BINDER_ID + "_children.py",
        "workflows/" + RETIRED_BINDER_ID + "_design.nf",
    ]
    assert [path for path in retired_paths if (REPO_ROOT / path).exists()] == []


def test_retired_ids_are_absent_from_active_source() -> None:
    forbidden = re.compile(
        re.escape(RETIRED_BINDER_ID),
        re.IGNORECASE,
    )
    roots = [
        REPO_ROOT / "platform/api",
        REPO_ROOT / "platform/frontend/src",
        REPO_ROOT / "workflows",
        REPO_ROOT / "modules",
        REPO_ROOT / "scripts",
        REPO_ROOT / "apptainer",
    ]
    hits: list[str] = []
    for root in roots:
        for path in root.rglob("*"):
            if (
                not path.is_file()
                or "tests" in path.parts
                or "__pycache__" in path.parts
                or any(part.startswith(".") for part in path.relative_to(REPO_ROOT).parts)
            ):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if forbidden.search(text) or forbidden.search(str(path.relative_to(REPO_ROOT))):
                hits.append(str(path.relative_to(REPO_ROOT)))
    assert hits == []


@pytest.mark.parametrize(
    ("model_id", "mode"),
    [
        (RETIRED_BINDER_ID, "design"),
    ],
)
def test_retired_ids_fail_closed_during_entrypoint_resolution(model_id: str, mode: str) -> None:
    with pytest.raises(ValueError, match="permanently removed"):
        resolve_nextflow_entrypoint(
            effective_profile="boltz",
            model_id=model_id,
            mode=mode,
            params={},
        )
