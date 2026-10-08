"""Selected post-round LigandMPNN operation; no sequence redesign or verdict.

The parent owns candidate selection and scheduling. This model owner binds exact
selected bytes to the Foundry leaf and publishes its native unclassified result.
"""

import hashlib
import json
import re
from contextvars import ContextVar
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.ligandmpnn_interface_context import read_context_result

# Shared jobs admission consults this only for the selected interface-context
# mode; a client-supplied manifest or binding is never submission authority.
selected_submission: ContextVar[bool] = ContextVar('ligandmpnn_selected_submission', default=False)


class InterfaceContextSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    binder_chain: str = Field(pattern=r'^[A-Za-z0-9]$')
    target_chain: str = Field(pattern=r'^[A-Za-z0-9]$')
    target_patch: list[str] = Field(min_length=1, max_length=32)
    seed: int = Field(ge=0, le=2**31 - 1)
    samples: int = Field(ge=1, le=16)
    temperature: float = Field(ge=0.01, le=2)

    @model_validator(mode='after')
    def check_patch(self):
        if self.binder_chain == self.target_chain:
            raise ValueError('binder and target chains must differ')
        if len(set(self.target_patch)) != len(self.target_patch) or any(
            re.fullmatch(re.escape(self.target_chain) + r'-?\d+[A-Za-z]?', residue) is None
            for residue in self.target_patch
        ):
            raise ValueError('patch must contain distinct native target-chain residue IDs')
        return self


class InterfaceContextSelection(BaseModel):
    """Typed independent operation, not an existing LigandMPNN redesign mode."""
    model_config = ConfigDict(extra='forbid', frozen=True)
    action: Literal['ligandmpnn_interface_context']
    source_job_id: str = Field(min_length=1)
    round_id: str = Field(min_length=1)
    candidate_ids: list[str] = Field(min_length=1, max_length=128)
    settings: InterfaceContextSettings

    @model_validator(mode='after')
    def unique_candidates(self):
        if any(not candidate.strip() for candidate in self.candidate_ids) or len(set(self.candidate_ids)) != len(self.candidate_ids):
            raise ValueError('candidate identities must be nonempty and distinct')
        return self


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compile_selected_manifest(selection: InterfaceContextSelection, sources: dict[str, bytes], output_dir: Path):
    """Return GeneratedInput-compatible pairs, without mutating source or disk.

    The parent must resolve IDs to same-root, source-owned immutable PDB bytes.
    The manifest has only selected entries; each PDB and request is independently
    sealed in the generated-input inventory for local or remote transport.
    """
    if set(sources) != set(selection.candidate_ids):
        raise ValueError('resolved sources must match exactly the selected candidates')
    root = Path(output_dir)
    records = []
    generated = []
    for index, candidate_id in enumerate(selection.candidate_ids):
        source = sources[candidate_id]
        if not isinstance(source, bytes) or not source:
            raise ValueError('selected source must have immutable PDB bytes')
        prefix = f'inputs/ligandmpnn_interface_context/{index:03d}'
        source_path = root / f'{prefix}/source.pdb'
        request_path = root / f'{prefix}/request.json'
        request = dict(candidate_id=candidate_id, round_id=selection.round_id,
                       source_sha256=_sha256(source), structure_path=str(source_path),
                       **selection.settings.model_dump())
        # The installed leaf's own request validator is the single native grammar.
        from importlib.util import module_from_spec, spec_from_file_location
        from paths import get_code_root
        runner_path = get_code_root() / 'scripts/run_ligandmpnn_interface_context.py'
        spec = spec_from_file_location('ligandmpnn_interface_request', runner_path)
        if spec is None or spec.loader is None:
            raise RuntimeError('Foundry interface-context request owner is unavailable')
        runner = module_from_spec(spec)
        spec.loader.exec_module(runner)
        # Its file-digest check belongs to the stage process, after materialization.
        if set(request) != runner.FIELDS:
            raise ValueError('selected request differs from native contract')
        generated.extend([(f'{prefix}/source.pdb', source),
                          (f'{prefix}/request.json', (json.dumps(request, sort_keys=True, allow_nan=False) + '\n').encode())])
        records.append(dict(invocation_id=f'{index:03d}', request_path=str(request_path), source_path=str(source_path)))
    manifest = (json.dumps(records, sort_keys=True, allow_nan=False) + '\n').encode()
    generated.append(('inputs/ligandmpnn_interface_context/selected.json', manifest))
    return generated, str(root / 'inputs/ligandmpnn_interface_context/selected.json')


def read_selected_attachment(result_dir: Path, *, candidate_id: str, round_id: str, source: bytes):
    """Read native rows and artifact digests for the existing JobArtifact owner.

    No summary score, selection eligibility, scientific success or failure is
    inferred. A caller can attach the returned native bytes to the child job.
    """
    result_dir = Path(result_dir)
    result = read_context_result(result_dir / 'result.json', candidate_id=candidate_id,
                                 round_id=round_id, source_sha256=_sha256(source))
    files = ('result.json', 'masked_complex.pdb', 'masked_without_binder.pdb')
    artifacts = {name: {'sha256': _sha256((result_dir / name).read_bytes()),
                        'bytes': len((result_dir / name).read_bytes())}
                 for name in files}
    return {'candidate_id': candidate_id, 'round_id': round_id,
            'source_sha256': _sha256(source), 'status': result['status'],
            'qualification': result['qualification'], 'conditions': result['conditions'],
            'settings': {key: result[key] for key in ('fixed_binder_chain', 'target_chain', 'target_patch', 'seed', 'samples', 'temperature')},
            'artifacts': artifacts}
