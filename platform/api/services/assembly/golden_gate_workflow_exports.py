"""Frozen workflow download/readback; no scientific execution or source lookup."""
from __future__ import annotations

import io
import json
from typing import Literal
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

from services.assembly.golden_gate_design_types import Closed
from services.assembly.golden_gate_workflow_types import WorkflowResult


class PortableWorkflow(Closed):
    schema_version: Literal['bms.golden-gate-workflow-portable.v1']
    result: WorkflowResult


def build_workflow_exports(result: WorkflowResult) -> dict[str, bytes]:
    # Import the established format owner lazily; shared Tm types are router-owned.
    from services.assembly.golden_gate_exports import build_design_exports

    selected = None
    if result.selected_solution_id is not None:
        selected = next((s for s in result.solutions if s.id == result.selected_solution_id), None)
        if selected is None:
            raise ValueError('Selected workflow candidate is not present')
    exports = {}
    domestication = {edit.source_id: edit.proposal for edit in result.edits}
    # Unselected physical candidates retain their artifacts without inventing a
    # chosen product. Numeric directory names are independent of caller IDs.
    candidates = [selected] if selected is not None else result.solutions
    for index, candidate in enumerate(candidates):
        reaction = candidate.reaction
        # Historical single-candidate workups predate the additive field. Never
        # borrow the root's first-candidate worksheet for a split alternative.
        if 'reaction' not in candidate.model_fields_set and len(result.solutions) == 1:
            reaction = result.reaction
        native = build_design_exports(candidate.fixed_request, candidate.design,
            fidelity=candidate.fidelity, domestication=domestication or None, reaction=reaction)
        prefix = '' if selected is not None else f'candidates/{index}/'
        exports.update({prefix + name: data for name, data in native.items()})
    document = PortableWorkflow(schema_version='bms.golden-gate-workflow-portable.v1', result=result)
    exports['workflow.json'] = (document.model_dump_json(indent=2) + '\n').encode('utf-8')
    summary = {
        'task': result.requested.task,
        'selected_solution_id': result.selected_solution_id,
        'fidelity_settings': result.requested.fidelity.model_dump(mode='json'),
        'selected_fidelity': selected.fidelity if selected else None,
        'evaluation': result.evaluation,
        'search_result': result.search_result,
        'diagnostics': result.diagnostics,
    }
    exports['comparison.txt'] = (
        'Frozen Golden Gate comparison evidence\n'
        'Caller-provided preview evidence is not server-attested recomputation.\n'
        'Import is local read/display only; original revision identities are preserved,\n'
        'not asserted to resolve on this installation. No external upload is performed.\n'
        'Empirical ligation estimates are not mass yield or colony predictions.\n\n'
        + json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False) + '\n'
    ).encode('utf-8')
    return exports


def workflow_zip(result: WorkflowResult) -> bytes:
    stream = io.BytesIO()
    with ZipFile(stream, 'w', compression=ZIP_DEFLATED) as archive:
        for name, data in build_workflow_exports(result).items():
            entry = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = ZIP_DEFLATED
            archive.writestr(entry, data)
    return stream.getvalue()
