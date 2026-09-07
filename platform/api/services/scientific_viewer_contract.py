"""Versioned transport projection, not scientific/numerical authority.

Axes retain producer candidate/document identity. ``document`` explicitly maps
that trusted source to the selected Design/StructureDocumentRef namespace. No
producer is asked to predict a database Design ID.
"""
from typing import Literal
from pydantic import BaseModel, ConfigDict, model_validator


class IdentityWire(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)


class ViewerDocument(IdentityWire):
    documentId: str
    candidateId: str
    contentSha256: str
    sourceKind: Literal['pdb', 'mmcif']


class ProducerBinding(IdentityWire):
    candidate_id: str
    document_id: str


class NativeResidue(IdentityWire):
    index: int
    chain_id: str
    residue_name: str
    insertion_code: str
    selected_model: int
    selected_altloc: str
    auth_asym_id: str | None
    auth_seq_id: int | None
    label_asym_id: str | None
    label_seq_id: int | None
    source_entity_id: str | None
    entity_instance_id: str | None


class NativeAxis(ProducerBinding):
    source_sha256: str
    residues: list[NativeResidue]


class NativeChain(IdentityWire):
    native_asym_id: int
    source_chain_index: int
    output_asym_id: int
    chain_id: str
    native_entity_id: int
    native_sym_id: int


class NativeMetricBase(IdentityWire):
    schema_name: Literal['core_protein_viewer_metric']
    schema_version: Literal[1]
    contract_revision: Literal[1]
    design_id: str
    design_name: str
    status: Literal['ok']
    reason: None
    document: ViewerDocument
    producer_binding: ProducerBinding
    artifact_sha256: str
    axis: NativeAxis
    native_positions: list[int]

    @model_validator(mode='after')
    def source_binding(self):
        import re
        if (self.document.candidateId != self.design_id
                or self.axis.candidate_id != self.producer_binding.candidate_id
                or self.axis.document_id != self.producer_binding.document_id
                or self.axis.source_sha256 != self.document.contentSha256
                or not re.fullmatch('[a-f0-9]{64}', self.artifact_sha256)
                or not re.fullmatch('[a-f0-9]{64}', self.document.contentSha256)
                or not self.axis.residues
                or self.native_positions != [r.index for r in self.axis.residues]
                or sorted(self.native_positions) != list(range(len(self.axis.residues)))):
            raise ValueError('invalid native source binding')
        return self


class ScientificResidueMetric(NativeMetricBase):
    metric: Literal['residue_plddt']
    units: Literal['fraction']
    values: list[float]

    @model_validator(mode='before')
    @classmethod
    def numeric_values(cls, v):
        if isinstance(v, dict) and any(type(n) not in (int, float) for n in v.get('values', [])):
            raise ValueError('non-numeric native fraction')
        return v

    @model_validator(mode='after')
    def vector_shape(self):
        if len(self.values) != len(self.axis.residues) or any(not 0 <= v <= 1 for v in self.values):
            raise ValueError('invalid native fraction vector')
        return self


class ScientificChainMetric(NativeMetricBase):
    metric: Literal['chain_metrics']
    chain_index_map: list[NativeChain]
    chains_ptm: dict[str, float]
    pair_chains_iptm: dict[str, dict[str, float]]
    role_assignment: None
    role_reason: Literal['missing_role_assignment']

    @model_validator(mode='before')
    @classmethod
    def numeric_values(cls, v):
        if isinstance(v, dict):
            values = list(v.get('chains_ptm', {}).values())
            values += [n for row in v.get('pair_chains_iptm', {}).values() for n in row.values()]
            if any(type(n) not in (int, float) for n in values):
                raise ValueError('non-numeric native score')
        return v

    @model_validator(mode='after')
    def chain_shape(self):
        keys = {str(c.native_asym_id) for c in self.chain_index_map}
        names = {c.chain_id for c in self.chain_index_map}
        if (len(keys) != len(self.chain_index_map) or len(names) != len(keys)
                or names != {r.chain_id for r in self.axis.residues}
                or set(self.chains_ptm) != keys or set(self.pair_chains_iptm) != keys
                or any(set(row) != keys for row in self.pair_chains_iptm.values())
                or any(not 0 <= v <= 1 for v in self.chains_ptm.values())
                or any(not 0 <= v <= 1 for row in self.pair_chains_iptm.values() for v in row.values())):
            raise ValueError('invalid native chain metric')
        return self


class ScientificViewerMetric(IdentityWire):
    schema_name: Literal['core_protein_viewer_metric']
    schema_version: Literal[1]
    contract_revision: Literal[1]
    design_id: str
    design_name: str
    metric: Literal['pae', 'residue_plddt', 'chain_metrics']
    status: Literal['ok', 'unavailable']
    reason: str | None
    document: ViewerDocument | None
    producer_binding: ProducerBinding | None
    artifact_sha256: str | None
    row_axis: NativeAxis | None
    column_axis: NativeAxis | None
    native_row_positions: list[int] | None
    native_column_positions: list[int] | None
    native_shape: list[int] | None
    sampled_row_indices: list[int] | None
    sampled_column_indices: list[int] | None
    pae_matrix: list[list[float]] | None
    size: int | None

    @model_validator(mode='before')
    @classmethod
    def exact_revisions(cls, value):
        if isinstance(value, dict) and any(type(value.get(k)) is not int for k in ('schema_version','contract_revision')):
            raise ValueError('revision must be an integer, not bool')
        return value

    @model_validator(mode='after')
    def coherent_state(self):
        import re
        result_fields = ('document', 'producer_binding', 'artifact_sha256', 'row_axis', 'column_axis',
                         'native_shape', 'native_row_positions', 'native_column_positions', 'sampled_row_indices', 'sampled_column_indices', 'pae_matrix', 'size')
        if self.status == 'unavailable':
            if not self.reason or not self.reason.strip() or any(getattr(self, k) is not None for k in result_fields):
                raise ValueError('unavailable identity requires reason and null values')
            return self
        if self.metric != 'pae' or self.reason is not None or any(getattr(self, k) is None for k in result_fields):
            raise ValueError('incomplete native metric projection')
        if self.document.candidateId != self.design_id:
            raise ValueError('foreign selected design')
        if not re.fullmatch('[a-f0-9]{64}', self.artifact_sha256) or not re.fullmatch('[a-f0-9]{64}', self.document.contentSha256):
            raise ValueError('invalid artifact/source hash')
        if len(self.native_shape) != 2 or min(self.native_shape) <= 0:
            raise ValueError('invalid native shape')
        for axis, count, indexes in zip((self.row_axis, self.column_axis), self.native_shape,
                                       (self.sampled_row_indices, self.sampled_column_indices)):
            if (axis.candidate_id != self.producer_binding.candidate_id
                    or axis.document_id != self.producer_binding.document_id
                    or axis.source_sha256 != self.document.contentSha256
                    or len(axis.residues) != count):
                raise ValueError('foreign native axis')
            if sorted(r.index for r in axis.residues) != list(range(count)):
                raise ValueError('invalid source positions')
            if not indexes or sorted(set(indexes)) != indexes or min(indexes) < 0 or max(indexes) >= count:
                raise ValueError('missing/invalid sampled indexes')
        if (self.native_row_positions != [r.index for r in self.row_axis.residues]
                or self.native_column_positions != [r.index for r in self.column_axis.residues]):
            raise ValueError('contradictory native position ledger')
        by_index = {r.index: r for r in self.row_axis.residues}
        if any(by_index.get(r.index) != r for r in self.column_axis.residues):
            raise ValueError('contradictory native axes')
        if (self.size != len(self.sampled_row_indices) or len(self.pae_matrix) != self.size
                or any(len(r) != len(self.sampled_column_indices) for r in self.pae_matrix)
                or any(v < 0 for r in self.pae_matrix for v in r)):
            raise ValueError('invalid sampled matrix')
        return self
