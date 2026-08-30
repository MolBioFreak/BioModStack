import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import {
    archiveDomainExperiment,
    archiveGlobalExperiment,
    archiveProject,
    createDomainExperiment,
    createGlobalExperiment,
    createProject,
    createResearchRecord,
    getDomainExperiment,
    getGlobalExperiment,
    getProject,
    issueAdapterReceipt,
    listDomainAdapters,
    projectManagerErrorMessage,
    restoreDomainExperiment,
    restoreGlobalExperiment,
    restoreProject,
    searchAdapterEntities,
    updateDomainExperiment,
    updateGlobalExperiment,
    updateProject,
    upgradeGlobalExperiment,
    upgradeProject,
    type AdapterEntityProjection,
    type JsonObject,
    type JsonValue,
    type ProjectManagerReadModel,
    type ProteinExperimentMode,
    type RecordKind,
} from '../../lib/projectManager';
import { globalExperimentForNode, selectedDomainContext } from './projectManagerState';

export type ManagerDialogMode = 'create_project' | 'create_global' | 'create_domain' | 'edit' | 'archive' | 'restore' | 'record';

function completeNgsDomainPayload(objective: string, experimentMode: string): JsonObject {
    return {
        schema: 'bms.ngs-molbio-experiment.v2',
        experiment_mode: experimentMode,
        scientific_objective: objective,
        planned_capability_ids: ['ngs.ont.fastq_qc'],
        grouping_intent: [],
        acceptance_criteria: [{
            criterion_id: 'ngs-result-manifest-present',
            schema_id: 'bms.scientific-criterion.artifact-presence.v1',
            schema_sha256: '3f03a62f9bc39f61c4bdfa938cca5453da91e68ef16b30effdd0f4195cc2bdc6',
            subject_role: 'result',
            payload: { artifact_role: 'ngs_result_manifest', minimum_count: 1 },
        }],
        evidence_plan: [{
            requirement_id: 'ngs-result-manifest-receipt',
            schema_id: 'bms.evidence-requirement.native-receipt.v1',
            schema_sha256: '4f1ea5545016d8d49739c2d1f1a94bc64f321667d2eb98205d0f727088da5d10',
            subject_role: 'result',
            required: true,
            payload: { receipt_kind: 'ngs_result_manifest', minimum_count: 1 },
        }],
    };
}

type ProteinTargetRole = 'target' | 'binder' | 'partner' | 'template' | 'reference' | 'control' | 'motif' | 'ligand_context' | 'other';
type ComparisonRole = 'reference' | 'target' | 'panel' | 'control';
type SubjectRole = 'input' | 'sample' | 'reference' | 'target' | 'panel' | 'control' | 'result' | 'comparison' | 'evidence' | 'other';

interface ProteinDatasetMemberDraft { datasetRevisionId: string; memberId: string }
interface ProteinTargetDraft {
    targetId: string; label: string; role: ProteinTargetRole; sourceReceiptIds: string[];
    datasetMembers: ProteinDatasetMemberDraft[]; expectedContentSha256: string;
    mapAuthorityKind: 'native_receipt' | 'governed_artifact_receipt'; mapReceiptId: string;
    mapReceiptSha256: string; mapContentSha256: string; mapSizeBytes: string;
    mapEntityCount: string; mapResidueCount: string; mapDisplayEntities: JsonValue[];
}
interface ComparisonMemberDraft { targetId: string; role: ComparisonRole }
interface ComparisonGroupDraft { groupId: string; label: string; compatibilityContractId: string; members: ComparisonMemberDraft[] }
interface AcceptanceDraft { criterionId: string; subjectRole: SubjectRole; question: string; outcome: 'approve' | 'reject' | 'record_only' }
interface EvidenceDraft { requirementId: string; subjectRole: SubjectRole; observationKind: string; prompt: string; required: boolean }
interface ProteinCapabilityOption { capabilityId: string; label: string; scientificRole: string; allowedDomainModes: string[] }

const MANUAL_REVIEW_SCHEMA_SHA256 = '581b25b646a8d581d234eef6948a3cc66e66ece0d00031b68dc8a5792dfc6b1d';
const OPERATOR_OBSERVATION_SCHEMA_SHA256 = '4122ba416790375dc99abbf028a1d494e1f0a9d11967dd138b4b4fde5a03bc06';
const blankProteinTarget = (): ProteinTargetDraft => ({
    targetId: '', label: '', role: 'target', sourceReceiptIds: [], datasetMembers: [], expectedContentSha256: '',
    mapAuthorityKind: 'native_receipt', mapReceiptId: '', mapReceiptSha256: '', mapContentSha256: '',
    mapSizeBytes: '', mapEntityCount: '', mapResidueCount: '0', mapDisplayEntities: [],
});
const nonEmptyStrings = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string' && Boolean(item.trim())) : [];
const jsonRecord = (value: unknown): JsonObject | null => value && typeof value === 'object' && !Array.isArray(value) ? value as JsonObject : null;
function parseProteinCapabilityInventory(value: unknown): ProteinCapabilityOption[] {
    const inventory = jsonRecord(value);
    if (inventory?.schema !== 'bms.protein-project-capability-inventory.v1' || typeof inventory.content_sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(inventory.content_sha256) || !Array.isArray(inventory.capabilities)) {
        throw new Error('The Protein capability inventory is not a recognized server document.');
    }
    return inventory.capabilities.flatMap((value) => {
        const row = jsonRecord(value);
        if (!row || typeof row.capability_id !== 'string' || typeof row.label !== 'string' || typeof row.scientific_role !== 'string' || typeof row.plannable !== 'boolean' || typeof row.exposure_state !== 'string' || !Array.isArray(row.allowed_domain_modes) || !row.allowed_domain_modes.every((mode) => typeof mode === 'string')) {
            throw new Error('The Protein capability inventory contains a malformed row.');
        }
        if (row.plannable !== true || row.exposure_state !== 'accepted') return [];
        return [{ capabilityId: row.capability_id, label: row.label, scientificRole: row.scientific_role, allowedDomainModes: row.allowed_domain_modes as string[] }];
    });
}

function completeProteinDomainPayload(
    objective: string,
    experimentMode: ProteinExperimentMode,
    targets: ProteinTargetDraft[],
    plannedCapabilityIds: string[],
    validationCapabilityIds: string[],
    comparisonGroups: ComparisonGroupDraft[],
    acceptanceCriteria: AcceptanceDraft[],
    evidenceRequirements: EvidenceDraft[],
    preservedAcceptanceCriteria: JsonObject[],
    preservedEvidenceRequirements: JsonObject[],
): JsonObject {
    if (!targets.length) throw new Error('Add at least one Protein target.');
    return {
        schema: 'bms.protein-in-silico-experiment.v3',
        experiment_mode: experimentMode,
        scientific_objective: objective,
        targets: targets.map((target) => ({
            target_id: target.targetId.trim(), label: target.label.trim(), role: target.role,
            source_receipt_ids: target.sourceReceiptIds,
            dataset_member_refs: target.datasetMembers.map((member) => ({ dataset_revision_id: member.datasetRevisionId.trim(), member_id: member.memberId.trim() })),
            entity_map_reference: {
                schema: 'bms.protein-entity-map-reference.v1', authority_kind: target.mapAuthorityKind,
                receipt_id: target.mapReceiptId.trim(), receipt_sha256: target.mapReceiptSha256.trim().toLowerCase(),
                content_sha256: target.mapContentSha256.trim().toLowerCase(), canonical_size_bytes: Number(target.mapSizeBytes),
                entity_count: Number(target.mapEntityCount), residue_mapping_count: Number(target.mapResidueCount), display_entities: target.mapDisplayEntities,
            },
            expected_content_sha256: target.expectedContentSha256.trim().toLowerCase(),
        })),
        design_constraints: [],
        planned_capability_ids: plannedCapabilityIds,
        comparison_groups: comparisonGroups.map((group) => ({
            group_id: group.groupId.trim(), label: group.label.trim(), compatibility_contract_id: group.compatibilityContractId.trim(),
            members: group.members.map((member, ordinal) => ({ target_id: member.targetId, role: member.role, ordinal })),
        })),
        validation_capability_ids: validationCapabilityIds,
        acceptance_criteria: [...preservedAcceptanceCriteria, ...acceptanceCriteria.map((criterion) => ({
            criterion_id: criterion.criterionId.trim(), schema_id: 'bms.scientific-criterion.manual-review.v1',
            schema_sha256: MANUAL_REVIEW_SCHEMA_SHA256, subject_role: criterion.subjectRole,
            payload: { review_question: criterion.question.trim(), required_outcome: criterion.outcome },
        }))],
        evidence_plan: [...preservedEvidenceRequirements, ...evidenceRequirements.map((requirement) => ({
            requirement_id: requirement.requirementId.trim(), schema_id: 'bms.evidence-requirement.operator-observation.v1',
            schema_sha256: OPERATOR_OBSERVATION_SCHEMA_SHA256, subject_role: requirement.subjectRole, required: requirement.required,
            payload: { observation_kind: requirement.observationKind.trim(), prompt: requirement.prompt.trim() },
        }))],
    };
}

interface ManagerDialogProps {
    mode: ManagerDialogMode | null;
    projectId?: string;
    summary?: ProjectManagerReadModel;
    onClose: () => void;
    onComplete: (destination?: { projectId?: string; focusId?: string; selectedNodeKey?: string }) => void;
}

export function ManagerDialog({ mode, projectId, summary, onClose, onComplete }: ManagerDialogProps) {
    const selection = summary?.selection;
    const selectionId = typeof selection?.canonical_identity.entity_id === 'string' ? selection.canonical_identity.entity_id : null;
    const selectedGlobalId = summary && selection ? globalExperimentForNode(summary, selection.node_key) : null;
    const domainContext = summary ? selectedDomainContext(summary) : null;
    const [name, setName] = useState('');
    const [projectScope, setProjectScope] = useState<'global' | 'ngs_molbio_local'>('global');
    const [description, setDescription] = useState('');
    const [objective, setObjective] = useState('');
    const [question, setQuestion] = useState('');
    const [hypothesis, setHypothesis] = useState('');
    const [priority, setPriority] = useState<'low' | 'normal' | 'high' | 'critical'>('normal');
    const [contributors, setContributors] = useState('');
    const [tags, setTags] = useState('');
    const [startDate, setStartDate] = useState('');
    const [targetEndDate, setTargetEndDate] = useState('');
    const [successCriteria, setSuccessCriteria] = useState('');
    const [reviewSummary, setReviewSummary] = useState('');
    const [conclusion, setConclusion] = useState('');
    const [domainKind, setDomainKind] = useState<'protein_in_silico' | 'ngs_molbio'>('protein_in_silico');
    const [ngsExperimentMode, setNgsExperimentMode] = useState('analysis');
    const [proteinExperimentMode, setProteinExperimentMode] = useState<ProteinExperimentMode>('design');
    const [proteinTargets, setProteinTargets] = useState<ProteinTargetDraft[]>([blankProteinTarget()]);
    const [activeProteinTarget, setActiveProteinTarget] = useState(0);
    const [plannedCapabilityIds, setPlannedCapabilityIds] = useState<string[]>([]);
    const [validationCapabilityIds, setValidationCapabilityIds] = useState<string[]>([]);
    const [plannedCapabilityDraft, setPlannedCapabilityDraft] = useState('');
    const [validationCapabilityDraft, setValidationCapabilityDraft] = useState('');
    const [comparisonGroups, setComparisonGroups] = useState<ComparisonGroupDraft[]>([]);
    const [acceptanceCriteria, setAcceptanceCriteria] = useState<AcceptanceDraft[]>([]);
    const [evidenceRequirements, setEvidenceRequirements] = useState<EvidenceDraft[]>([]);
    const [preservedAcceptanceCriteria, setPreservedAcceptanceCriteria] = useState<JsonObject[]>([]);
    const [preservedEvidenceRequirements, setPreservedEvidenceRequirements] = useState<JsonObject[]>([]);
    const [proteinSourceAdapterId, setProteinSourceAdapterId] = useState('');
    const [proteinSourceQuery, setProteinSourceQuery] = useState('');
    const [proteinSourceSelection, setProteinSourceSelection] = useState<AdapterEntityProjection | null>(null);
    const [recordKind, setRecordKind] = useState<RecordKind>('note');
    const [body, setBody] = useState('');

    const needsDetail = Boolean(mode && ['edit', 'archive', 'restore'].includes(mode));
    const detailQuery = useQuery({
        queryKey: ['project-manager', 'hierarchy-detail', projectId, selection?.node_type, selectionId],
        enabled: needsDetail && Boolean(projectId && selectionId),
        queryFn: ({ signal }) => {
            if (!projectId || !selectionId || !selection) throw new Error('The selected hierarchy identity is unavailable.');
            if (selection.node_type === 'project') return getProject(projectId, signal);
            if (selection.node_type === 'global_experiment') return getGlobalExperiment(projectId, selectionId, signal);
            if (selection.node_type === 'domain_experiment' && selectedGlobalId) return getDomainExperiment(projectId, selectedGlobalId, selectionId, signal);
            throw new Error('This selection does not support revision management.');
        },
    });
    const proteinAdaptersQuery = useQuery({
        queryKey: ['project-manager', 'protein-source-adapters'],
        queryFn: ({ signal }) => listDomainAdapters(signal),
        enabled: (mode === 'create_domain' && domainKind === 'protein_in_silico')
            || (mode === 'edit' && selection?.node_type === 'domain_experiment' && jsonRecord(detailQuery.data?.payload?.domain_payload)?.schema === 'bms.protein-in-silico-experiment.v3'),
    });
    const proteinAdapters = (proteinAdaptersQuery.data?.adapters ?? []).filter((adapter) => adapter.domain_kind === 'protein_in_silico');
    const proteinCapabilitiesQuery = useQuery({
        queryKey: ['project-manager', 'protein-project-capabilities'],
        queryFn: async ({ signal }) => parseProteinCapabilityInventory((await api.get<unknown>('/api/protein-project-capabilities', { signal })).data),
        enabled: (mode === 'create_domain' && domainKind === 'protein_in_silico')
            || (mode === 'edit' && selection?.node_type === 'domain_experiment' && jsonRecord(detailQuery.data?.payload?.domain_payload)?.schema === 'bms.protein-in-silico-experiment.v3'),
        retry: false,
    });
    const proteinSourceSearch = useMutation({
        mutationFn: () => searchAdapterEntities(proteinSourceAdapterId, proteinSourceQuery, 25),
        onSuccess: () => setProteinSourceSelection(null),
    });
    const proteinReceiptIssue = useMutation({
        mutationFn: async () => {
            if (!projectId || !proteinSourceAdapterId || !proteinSourceSelection) throw new Error('Select one verified Protein source.');
            const result = await issueAdapterReceipt(proteinSourceAdapterId, proteinSourceSelection.entity_id, projectId);
            const digest = result.receipt.content_digest;
            if (typeof digest !== 'string' || !/^[0-9a-f]{64}$/.test(digest)) throw new Error('The verified source receipt has no exact content digest.');
            return { result, digest };
        },
        onSuccess: ({ result, digest }) => {
            setProteinTargets((current) => current.map((target, index) => index === activeProteinTarget ? {
                ...target,
                sourceReceiptIds: Array.from(new Set([...target.sourceReceiptIds, result.receipt_id])),
                expectedContentSha256: digest,
            } : target));
        },
    });

    useEffect(() => {
        if (!mode) return;
        setName('');
        setProjectScope('global');
        setDescription('');
        setObjective('');
        setQuestion('');
        setHypothesis('');
        setPriority('normal');
        setContributors('');
        setTags('');
        setStartDate('');
        setTargetEndDate('');
        setSuccessCriteria('');
        setReviewSummary('');
        setConclusion('');
        setDomainKind('protein_in_silico');
        setNgsExperimentMode('analysis');
        setProteinExperimentMode('design');
        setProteinTargets([blankProteinTarget()]);
        setActiveProteinTarget(0);
        setPlannedCapabilityIds([]);
        setValidationCapabilityIds([]);
        setPlannedCapabilityDraft('');
        setValidationCapabilityDraft('');
        setComparisonGroups([]);
        setAcceptanceCriteria([]);
        setEvidenceRequirements([]);
        setPreservedAcceptanceCriteria([]);
        setPreservedEvidenceRequirements([]);
        setProteinSourceAdapterId('');
        setProteinSourceQuery('');
        setProteinSourceSelection(null);
        setRecordKind('note');
        setBody('');
    }, [mode]);

    useEffect(() => {
        if (proteinSourceAdapterId || !proteinAdapters.length) return;
        setProteinSourceAdapterId(proteinAdapters[0]?.adapter_id ?? '');
    }, [proteinAdapters, proteinSourceAdapterId]);

    useEffect(() => {
        if (mode !== 'edit' || !detailQuery.data) return;
        setName(detailQuery.data.name ?? '');
        const payload = detailQuery.data.payload ?? {};
        const list = (value: unknown) => Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
        setDescription(detailQuery.data.description ?? '');
        setObjective(typeof payload.research_objective === 'string' ? payload.research_objective : typeof payload.objective === 'string' ? payload.objective : '');
        setQuestion(typeof payload.scientific_question === 'string' ? payload.scientific_question : '');
        setHypothesis(typeof payload.hypothesis === 'string' ? payload.hypothesis : '');
        setPriority(payload.priority === 'low' || payload.priority === 'high' || payload.priority === 'critical' ? payload.priority : 'normal');
        setContributors(list(payload.contributors).join(', '));
        setTags(list(payload.tags).join(', '));
        setStartDate(typeof payload.start_date === 'string' ? payload.start_date : '');
        setTargetEndDate(typeof payload.target_end_date === 'string' ? payload.target_end_date : '');
        setSuccessCriteria(list(payload.success_criteria).join('\n'));
        setReviewSummary(typeof payload.review_summary === 'string' ? payload.review_summary : '');
        setConclusion(typeof payload.conclusion === 'string' ? payload.conclusion : '');
        const proteinPayload = jsonRecord(payload.domain_payload);
        if (selection?.node_type === 'domain_experiment' && proteinPayload?.schema === 'bms.protein-in-silico-experiment.v3') {
            setDomainKind('protein_in_silico');
            const modeValue = proteinPayload.experiment_mode;
            if (typeof modeValue === 'string' && ['exploration', 'design', 'redesign', 'prediction', 'validation', 'comparison', 'simulation', 'analysis'].includes(modeValue)) {
                setProteinExperimentMode(modeValue as ProteinExperimentMode);
            }
            const parsedTargets = Array.isArray(proteinPayload.targets) ? proteinPayload.targets.flatMap((value) => {
                const target = jsonRecord(value);
                const map = jsonRecord(target?.entity_map_reference);
                if (!target || !map) return [];
                const role = typeof target.role === 'string' ? target.role as ProteinTargetRole : 'target';
                const datasetMembers = Array.isArray(target.dataset_member_refs) ? target.dataset_member_refs.flatMap((memberValue) => {
                    const member = jsonRecord(memberValue);
                    return member && typeof member.dataset_revision_id === 'string' && typeof member.member_id === 'string'
                        ? [{ datasetRevisionId: member.dataset_revision_id, memberId: member.member_id }]
                        : [];
                }) : [];
                return [{
                    targetId: typeof target.target_id === 'string' ? target.target_id : '',
                    label: typeof target.label === 'string' ? target.label : '', role,
                    sourceReceiptIds: nonEmptyStrings(target.source_receipt_ids), datasetMembers,
                    expectedContentSha256: typeof target.expected_content_sha256 === 'string' ? target.expected_content_sha256 : '',
                    mapAuthorityKind: map.authority_kind === 'governed_artifact_receipt' ? 'governed_artifact_receipt' as const : 'native_receipt' as const,
                    mapReceiptId: typeof map.receipt_id === 'string' ? map.receipt_id : '',
                    mapReceiptSha256: typeof map.receipt_sha256 === 'string' ? map.receipt_sha256 : '',
                    mapContentSha256: typeof map.content_sha256 === 'string' ? map.content_sha256 : '',
                    mapSizeBytes: typeof map.canonical_size_bytes === 'number' ? String(map.canonical_size_bytes) : '',
                    mapEntityCount: typeof map.entity_count === 'number' ? String(map.entity_count) : '',
                    mapResidueCount: typeof map.residue_mapping_count === 'number' ? String(map.residue_mapping_count) : '0',
                    mapDisplayEntities: Array.isArray(map.display_entities) ? map.display_entities : [],
                }];
            }) : [];
            setProteinTargets(parsedTargets.length ? parsedTargets : [blankProteinTarget()]);
            setPlannedCapabilityIds(nonEmptyStrings(proteinPayload.planned_capability_ids));
            setValidationCapabilityIds(nonEmptyStrings(proteinPayload.validation_capability_ids));
            setComparisonGroups(Array.isArray(proteinPayload.comparison_groups) ? proteinPayload.comparison_groups.flatMap((value) => {
                const group = jsonRecord(value);
                if (!group) return [];
                const members = Array.isArray(group.members) ? group.members.flatMap((memberValue) => {
                    const member = jsonRecord(memberValue);
                    return member && typeof member.target_id === 'string' && typeof member.role === 'string'
                        ? [{ targetId: member.target_id, role: member.role as ComparisonRole }]
                        : [];
                }) : [];
                return [{ groupId: typeof group.group_id === 'string' ? group.group_id : '', label: typeof group.label === 'string' ? group.label : '', compatibilityContractId: typeof group.compatibility_contract_id === 'string' ? group.compatibility_contract_id : '', members }];
            }) : []);
            const criteria = Array.isArray(proteinPayload.acceptance_criteria) ? proteinPayload.acceptance_criteria.flatMap((value) => jsonRecord(value) ? [jsonRecord(value)!] : []) : [];
            setAcceptanceCriteria(criteria.flatMap((criterion) => {
                const criterionPayload = jsonRecord(criterion.payload);
                return criterion.schema_id === 'bms.scientific-criterion.manual-review.v1' && criterionPayload
                    ? [{ criterionId: String(criterion.criterion_id ?? ''), subjectRole: String(criterion.subject_role ?? 'result') as SubjectRole, question: String(criterionPayload.review_question ?? ''), outcome: String(criterionPayload.required_outcome ?? 'approve') as AcceptanceDraft['outcome'] }]
                    : [];
            }));
            setPreservedAcceptanceCriteria(criteria.filter((criterion) => criterion.schema_id !== 'bms.scientific-criterion.manual-review.v1'));
            const evidence = Array.isArray(proteinPayload.evidence_plan) ? proteinPayload.evidence_plan.flatMap((value) => jsonRecord(value) ? [jsonRecord(value)!] : []) : [];
            setEvidenceRequirements(evidence.flatMap((requirement) => {
                const requirementPayload = jsonRecord(requirement.payload);
                return requirement.schema_id === 'bms.evidence-requirement.operator-observation.v1' && requirementPayload
                    ? [{ requirementId: String(requirement.requirement_id ?? ''), subjectRole: String(requirement.subject_role ?? 'result') as SubjectRole, observationKind: String(requirementPayload.observation_kind ?? ''), prompt: String(requirementPayload.prompt ?? ''), required: requirement.required !== false }]
                    : [];
            }));
            setPreservedEvidenceRequirements(evidence.filter((requirement) => requirement.schema_id !== 'bms.evidence-requirement.operator-observation.v1'));
        }
    }, [detailQuery.data, mode, selection?.node_type]);

    const mutation = useMutation({
        mutationFn: async () => {
            if (mode === 'create_project') {
                return createProject({
                    schema: 'bms.project.v2',
                    project_scope: projectScope,
                    name,
                    description,
                    research_objective: objective,
                    contributors: contributors.split(',').map((value) => value.trim()).filter(Boolean),
                    tags: tags.split(',').map((value) => value.trim()).filter(Boolean),
                    status: 'active',
                    start_date: startDate || null,
                    target_end_date: targetEndDate || null,
                    change_summary: 'Created in Project Manager',
                });
            }
            if (!projectId || !summary) throw new Error('A Project context is required.');
            if (mode === 'create_global') {
                return createGlobalExperiment(projectId, {
                    schema: 'bms.global-experiment.v2',
                    name,
                    objective,
                    scientific_question: question,
                    hypothesis: hypothesis || null,
                    description,
                    status: 'planned',
                    priority,
                    tags: tags.split(',').map((value) => value.trim()).filter(Boolean),
                    shared_source_receipt_ids: [],
                    shared_dataset_ids: [],
                    comparison_plan: null,
                    success_criteria: successCriteria.split('\n').map((value) => value.trim()).filter(Boolean),
                    review_summary: reviewSummary || null,
                    conclusion: conclusion || null,
                    change_summary: 'Created in Project Manager',
                });
            }
            if (mode === 'create_domain') {
                const globalId = selection?.node_type === 'global_experiment' ? selectionId : selectedGlobalId;
                if (!globalId) throw new Error('Select a Global Experiment before creating a Domain Experiment.');
                if (domainKind === 'protein_in_silico') {
                    const domainPayload = completeProteinDomainPayload(
                        objective,
                        proteinExperimentMode,
                        proteinTargets,
                        plannedCapabilityIds,
                        validationCapabilityIds,
                        comparisonGroups,
                        acceptanceCriteria,
                        evidenceRequirements,
                        preservedAcceptanceCriteria,
                        preservedEvidenceRequirements,
                    );
                    const datasetRevisionIds = Array.from(new Set(proteinTargets.flatMap((target) => target.datasetMembers.map((member) => member.datasetRevisionId.trim())).filter(Boolean)));
                    const sourceReceiptIds = Array.from(new Set(proteinTargets.flatMap((target) => target.sourceReceiptIds)));
                    return createDomainExperiment(projectId, globalId, {
                        schema: 'bms.domain-experiment.v4',
                        domain_kind: 'protein_in_silico',
                        domain_contract_version: '3',
                        name,
                        objective,
                        status: 'draft',
                        tags: [],
                        source_receipt_ids: sourceReceiptIds,
                        dataset_revision_ids: datasetRevisionIds,
                        change_summary: 'Created in Project Manager',
                        domain_payload: domainPayload,
                    });
                }
                const domainPayload = completeNgsDomainPayload(objective, ngsExperimentMode);
                return createDomainExperiment(projectId, globalId, {
                    schema: 'bms.domain-experiment.v4',
                    domain_kind: 'ngs_molbio',
                    domain_contract_version: '3',
                    name,
                    objective,
                    status: 'planned',
                    tags: [],
                    source_receipt_ids: [],
                    dataset_revision_ids: [],
                    change_summary: 'Created in Project Manager',
                    domain_payload: domainPayload,
                });
            }
            if (mode === 'record') {
                if (!selectionId || !selection) throw new Error('Select a hierarchy record before adding an ELN-lite record.');
                const subject = selection.node_type === 'project'
                    ? { projectId }
                    : selection.node_type === 'global_experiment'
                        ? { projectId, globalExperimentId: selectionId }
                        : selection.node_type === 'domain_experiment' && domainContext
                            ? { projectId, globalExperimentId: domainContext.globalExperimentId, domainExperimentId: selectionId }
                            : null;
                if (!subject) throw new Error('This selection does not accept notes or decisions.');
                return createResearchRecord(subject, { record_kind: recordKind, body });
            }
            const detail = detailQuery.data;
            if (!detail || !selectionId || !selection) throw new Error('Current generation is still loading.');
            if (mode === 'edit') {
                if (selection.node_type === 'project') {
                    if (detail.payload?.schema === 'bms.project.v1') {
                        return upgradeProject(projectId, {
                            expected_head_generation: detail.head_generation,
                            schema: 'bms.project.v2',
                            project_scope: detail.payload.project_scope === 'ngs_molbio_local' ? 'ngs_molbio_local' : 'global',
                            name,
                            description,
                            research_objective: objective,
                            contributors: contributors.split(',').map((value) => value.trim()).filter(Boolean),
                            tags: tags.split(',').map((value) => value.trim()).filter(Boolean),
                            status: detail.status as 'draft' | 'active' | 'on_hold' | 'completed' | 'archived',
                            start_date: startDate || null,
                            target_end_date: targetEndDate || null,
                            change_summary: 'Upgraded Project to v2 from Project Manager',
                        });
                    }
                    return updateProject(projectId, {
                        expected_head_generation: detail.head_generation,
                        name,
                        description,
                        research_objective: objective,
                        contributors: contributors.split(',').map((value) => value.trim()).filter(Boolean),
                        tags: tags.split(',').map((value) => value.trim()).filter(Boolean),
                        start_date: startDate || null,
                        target_end_date: targetEndDate || null,
                        change_summary: 'Edited in Project Manager',
                    });
                }
                if (selection.node_type === 'global_experiment') {
                    if (detail.payload?.schema === 'bms.global-experiment.v1') {
                        return upgradeGlobalExperiment(projectId, selectionId, {
                            expected_head_generation: detail.head_generation,
                            schema: 'bms.global-experiment.v2',
                            name,
                            objective,
                            scientific_question: question,
                            hypothesis: hypothesis || null,
                            description,
                            status: detail.status as 'draft' | 'planned' | 'active' | 'analysis' | 'review' | 'completed' | 'blocked' | 'archived',
                            priority,
                            tags: tags.split(',').map((value) => value.trim()).filter(Boolean),
                            shared_source_receipt_ids: [],
                            shared_dataset_ids: [],
                            comparison_plan: null,
                            success_criteria: successCriteria.split('\n').map((value) => value.trim()).filter(Boolean),
                            review_summary: reviewSummary || null,
                            conclusion: conclusion || null,
                            change_summary: 'Upgraded Global Experiment to v2 from Project Manager',
                        });
                    }
                    return updateGlobalExperiment(projectId, selectionId, {
                        expected_head_generation: detail.head_generation,
                        name,
                        objective,
                        scientific_question: question,
                        description,
                        hypothesis: hypothesis || null,
                        priority,
                        tags: tags.split(',').map((value) => value.trim()).filter(Boolean),
                        success_criteria: successCriteria.split('\n').map((value) => value.trim()).filter(Boolean),
                        review_summary: reviewSummary || null,
                        conclusion: conclusion || null,
                        change_summary: 'Edited in Project Manager',
                    });
                }
                if (selection.node_type === 'domain_experiment' && selectedGlobalId) {
                    const existingDomainPayload = jsonRecord(detail.payload?.domain_payload);
                    if (existingDomainPayload?.schema === 'bms.protein-in-silico-experiment.v3') {
                        const domainPayload = completeProteinDomainPayload(
                            objective, proteinExperimentMode, proteinTargets, plannedCapabilityIds, validationCapabilityIds,
                            comparisonGroups, acceptanceCriteria, evidenceRequirements, preservedAcceptanceCriteria, preservedEvidenceRequirements,
                        );
                        return updateDomainExperiment(projectId, selectedGlobalId, selectionId, {
                            expected_head_generation: detail.head_generation, name, objective,
                            source_receipt_ids: Array.from(new Set(proteinTargets.flatMap((target) => target.sourceReceiptIds))),
                            dataset_revision_ids: Array.from(new Set(proteinTargets.flatMap((target) => target.datasetMembers.map((member) => member.datasetRevisionId.trim())).filter(Boolean))),
                            domain_payload: domainPayload, change_summary: 'Edited Protein setup in Project Manager',
                        });
                    }
                    return updateDomainExperiment(projectId, selectedGlobalId, selectionId, { expected_head_generation: detail.head_generation, name, objective, change_summary: 'Edited in Project Manager' });
                }
            }
            if (mode === 'archive') {
                if (selection.node_type === 'project') return archiveProject(projectId, detail.head_generation);
                if (selection.node_type === 'global_experiment') return archiveGlobalExperiment(projectId, selectionId, detail.head_generation);
                if (selection.node_type === 'domain_experiment' && selectedGlobalId) return archiveDomainExperiment(projectId, selectedGlobalId, selectionId, detail.head_generation);
            }
            if (mode === 'restore') {
                if (selection.node_type === 'project') return restoreProject(projectId, detail.head_generation);
                if (selection.node_type === 'global_experiment') return restoreGlobalExperiment(projectId, selectionId, detail.head_generation);
                if (selection.node_type === 'domain_experiment' && selectedGlobalId) return restoreDomainExperiment(projectId, selectedGlobalId, selectionId, detail.head_generation);
            }
            throw new Error('This Project Manager operation is unavailable for the current selection.');
        },
        onSuccess: (result) => {
            if (mode === 'create_project' && 'id' in result && typeof result.id === 'string') {
                onComplete({ projectId: result.id });
            } else if (mode === 'create_global' && 'id' in result && typeof result.id === 'string') {
                onComplete({ focusId: result.id, selectedNodeKey: `global_experiment:${result.id}` });
            } else if (mode === 'create_domain' && 'id' in result && typeof result.id === 'string') {
                onComplete({ focusId: selectedGlobalId ?? selectionId ?? undefined, selectedNodeKey: `domain_experiment:${result.id}` });
            } else {
                onComplete();
            }
            onClose();
        },
    });

    const title = useMemo(() => {
        if (mode === 'create_project') return 'Create Project';
        if (mode === 'create_global') return 'Create Global Experiment';
        if (mode === 'create_domain') return 'Create Domain Experiment';
        if (mode === 'edit') return `Edit ${selection?.title ?? 'revision'}`;
        if (mode === 'archive') return `Archive ${selection?.title ?? 'selection'}`;
        if (mode === 'restore') return `Restore ${selection?.title ?? 'selection'}`;
        if (mode === 'record') return `Add record to ${selection?.title ?? 'selection'}`;
        return 'Project Manager action';
    }, [mode, selection?.title]);

    if (!mode) return null;
    const confirmation = mode === 'archive' || mode === 'restore';
    const proteinEditing = mode === 'edit'
        && selection?.node_type === 'domain_experiment'
        && jsonRecord(detailQuery.data?.payload?.domain_payload)?.schema === 'bms.protein-in-silico-experiment.v3';
    const proteinTargetsReady = proteinTargets.length > 0 && proteinTargets.every((target) => (
        target.targetId.trim().length > 0 && target.label.trim().length > 0
        && (target.sourceReceiptIds.length > 0 || target.datasetMembers.length > 0)
        && target.datasetMembers.every((member) => member.datasetRevisionId.trim() && member.memberId.trim())
        && /^[0-9a-f]{64}$/i.test(target.expectedContentSha256.trim())
        && target.mapReceiptId.trim().length > 0
        && /^[0-9a-f]{64}$/i.test(target.mapReceiptSha256.trim())
        && /^[0-9a-f]{64}$/i.test(target.mapContentSha256.trim())
        && Number(target.mapSizeBytes) >= 2 && Number(target.mapEntityCount) >= 1 && Number(target.mapResidueCount) >= 0
    ));
    const proteinPlansReady = comparisonGroups.every((group) => group.groupId.trim() && group.label.trim() && group.compatibilityContractId.trim() && group.members.length > 0 && group.members.every((member) => member.targetId))
        && acceptanceCriteria.every((criterion) => criterion.criterionId.trim() && criterion.question.trim())
        && evidenceRequirements.every((requirement) => requirement.requirementId.trim() && requirement.observationKind.trim() && requirement.prompt.trim());
    const plannedCapabilityOptions = (proteinCapabilitiesQuery.data ?? []).filter((capability) => capability.allowedDomainModes.includes(proteinExperimentMode));
    const validationCapabilityOptions = (proteinCapabilitiesQuery.data ?? []).filter((capability) => capability.allowedDomainModes.includes('validation'));
    const domainCreationReady = domainKind === 'ngs_molbio' || (proteinTargetsReady && proteinPlansReady);
    const canSubmit = confirmation
        ? Boolean(detailQuery.data)
        : mode === 'record'
            ? body.trim().length > 0
            : name.trim().length > 0
                && (mode !== 'create_domain' || domainCreationReady)
                && (!proteinEditing || (proteinTargetsReady && proteinPlansReady));

    return (
        <div className="fixed inset-0 z-[95] grid place-items-center bg-black/65 p-3" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}>
            <section role="dialog" aria-modal="true" aria-labelledby="manager-action-title" className="w-full max-h-[calc(100vh-1.5rem)] max-w-3xl overflow-y-auto rounded-2xl border border-border-primary bg-surface-secondary shadow-2xl">
                <header className="flex items-start justify-between gap-3 border-b border-border-primary px-5 py-4">
                    <div>
                        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-accent">Immutable Project record</p>
                        <h2 id="manager-action-title" className="mt-1 text-lg font-semibold text-content">{title}</h2>
                    </div>
                    <button type="button" onClick={onClose} className="rounded-lg border border-border-primary px-3 py-1.5 text-xs text-content-secondary">Close</button>
                </header>
                <form onSubmit={(event) => { event.preventDefault(); if (canSubmit) mutation.mutate(); }} className="space-y-4 p-5">
                    {detailQuery.isLoading && <p className="text-xs text-content-muted">Loading the current server-owned generation…</p>}
                    {confirmation ? (
                        <div className="rounded-xl border border-warning/50 bg-warning/10 p-4 text-sm text-content-secondary">
                            {mode === 'archive' ? (
                                <><p className="font-semibold text-warning">Archive removes this item from active navigation.</p><p className="mt-2">It retains identity, child records, receipts, notes, lineage, and audit history. It does not delete or cancel any canonical scientific run.</p></>
                            ) : (
                                <><p className="font-semibold text-success">Restore returns this item to active Project navigation.</p><p className="mt-2">The mutation uses the current server generation and creates an audited lifecycle transition.</p></>
                            )}
                        </div>
                    ) : mode === 'record' ? (
                        <>
                            <label className="block text-xs font-semibold text-content-secondary">Record type
                                <select value={recordKind} onChange={(event) => setRecordKind(event.target.value as RecordKind)} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface px-3 py-2.5 text-content">
                                    <option value="note">Note</option><option value="observation">Observation</option><option value="decision">Decision</option><option value="conclusion">Conclusion</option>
                                </select>
                            </label>
                            <label className="block text-xs font-semibold text-content-secondary">Body
                                <textarea value={body} onChange={(event) => setBody(event.target.value)} rows={5} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface px-3 py-2.5 text-sm text-content outline-none focus:border-accent focus:ring-2 focus:ring-accent/30" />
                            </label>
                            <p className="text-[10px] text-content-muted">Records are append-only. A later correction creates a replacement record; it does not rewrite this entry.</p>
                        </>
                    ) : (
                        <>
                            {mode === 'create_project' && <label className="block text-xs font-semibold text-content-secondary">Project type
                                <select aria-label="Project type" value={projectScope} onChange={(event) => setProjectScope(event.target.value as 'global' | 'ngs_molbio_local')} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface px-3 py-2.5 text-sm text-content">
                                    <option value="global">Global Project</option>
                                    <option value="ngs_molbio_local">Standalone NGS/MolBio Project</option>
                                </select>
                                <span className="mt-1 block font-normal text-content-muted">Standalone NGS/MolBio Projects can be linked to a Global Project later.</span>
                            </label>}
                            <label className="block text-xs font-semibold text-content-secondary">Name
                                <input aria-label={mode === 'create_project' ? 'Project name' : 'Name'} autoFocus value={name} onChange={(event) => setName(event.target.value)} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface px-3 py-2.5 text-sm text-content outline-none focus:border-accent focus:ring-2 focus:ring-accent/30" />
                            </label>
                            {(mode === 'create_project' || mode === 'create_global' || (mode === 'edit' && (selection?.node_type === 'project' || selection?.node_type === 'global_experiment'))) && <label className="block text-xs font-semibold text-content-secondary">Description
                                <textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={2} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface px-3 py-2.5 text-sm text-content outline-none focus:border-accent focus:ring-2 focus:ring-accent/30" />
                            </label>}
                            {(mode === 'create_project' || (mode === 'edit' && selection?.node_type === 'project')) && (
                                <div className="grid gap-3 rounded-xl border border-border-primary bg-surface p-3 sm:grid-cols-2">
                                    <label className="text-xs font-semibold text-content-secondary">Contributors
                                        <input value={contributors} onChange={(event) => setContributors(event.target.value)} placeholder="comma-separated names" className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-sm text-content" />
                                    </label>
                                    <label className="text-xs font-semibold text-content-secondary">Tags
                                        <input value={tags} onChange={(event) => setTags(event.target.value)} placeholder="comma-separated tags" className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-sm text-content" />
                                    </label>
                                    <label className="text-xs font-semibold text-content-secondary">Start date
                                        <input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-sm text-content" />
                                    </label>
                                    <label className="text-xs font-semibold text-content-secondary">Target end date
                                        <input type="date" value={targetEndDate} onChange={(event) => setTargetEndDate(event.target.value)} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-sm text-content" />
                                    </label>
                                </div>
                            )}
                            {(mode === 'create_global' || (mode === 'edit' && selection?.node_type === 'global_experiment')) && (
                                <div className="space-y-3 rounded-xl border border-border-primary bg-surface p-3">
                                    <label className="block text-xs font-semibold text-content-secondary">Hypothesis
                                        <textarea value={hypothesis} onChange={(event) => setHypothesis(event.target.value)} rows={2} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-sm text-content" />
                                    </label>
                                    <label className="block text-xs font-semibold text-content-secondary">Priority
                                        <select value={priority} onChange={(event) => setPriority(event.target.value as typeof priority)} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-sm text-content"><option value="low">Low</option><option value="normal">Normal</option><option value="high">High</option><option value="critical">Critical</option></select>
                                    </label>
                                    <label className="block text-xs font-semibold text-content-secondary">Success criteria
                                        <textarea value={successCriteria} onChange={(event) => setSuccessCriteria(event.target.value)} rows={3} placeholder="one criterion per line" className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-sm text-content" />
                                    </label>
                                    <label className="block text-xs font-semibold text-content-secondary">Review summary
                                        <textarea value={reviewSummary} onChange={(event) => setReviewSummary(event.target.value)} rows={2} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-sm text-content" />
                                    </label>
                                    <label className="block text-xs font-semibold text-content-secondary">Conclusion
                                        <textarea value={conclusion} onChange={(event) => setConclusion(event.target.value)} rows={2} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-sm text-content" />
                                    </label>
                                </div>
                            )}
                            {mode === 'create_domain' && <label className="block text-xs font-semibold text-content-secondary">Domain type
                                <select value={domainKind} onChange={(event) => setDomainKind(event.target.value as typeof domainKind)} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface px-3 py-2.5 text-content">
                                    <option value="protein_in_silico">Protein In Silico</option><option value="ngs_molbio">NGS / MolBio</option>
                                </select>
                            </label>}
                            {((mode === 'create_domain' && domainKind === 'protein_in_silico') || proteinEditing) && (
                                <div className="space-y-4 rounded-xl border border-border-primary bg-surface p-3">
                                    <p className="text-xs text-content-secondary">Define the Protein work before launching a model. Source receipts, content digests, and entity-map references are checked by the server.</p>
                                    <label className="block text-xs font-semibold text-content-secondary">Protein experiment mode
                                        <select aria-label="Protein experiment mode" value={proteinExperimentMode} onChange={(event) => setProteinExperimentMode(event.target.value as ProteinExperimentMode)} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-content">
                                            {['exploration', 'design', 'redesign', 'prediction', 'validation', 'comparison', 'simulation', 'analysis'].map((value) => <option key={value} value={value}>{value[0].toUpperCase() + value.slice(1)}</option>)}
                                        </select>
                                    </label>

                                    <div className="space-y-2 rounded-lg border border-border-primary p-3">
                                        <div className="flex items-center justify-between gap-2"><p className="text-xs font-semibold text-content-secondary">Verify a target source</p><select aria-label="Target receiving verified source" value={activeProteinTarget} onChange={(event) => setActiveProteinTarget(Number(event.target.value))} className="rounded border border-border-primary bg-surface-secondary px-2 py-1 text-xs">{proteinTargets.map((target, index) => <option key={index} value={index}>{target.label || target.targetId || `Target ${index + 1}`}</option>)}</select></div>
                                        <select aria-label="Protein source adapter" value={proteinSourceAdapterId} onChange={(event) => { setProteinSourceAdapterId(event.target.value); setProteinSourceSelection(null); }} className="w-full rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-xs text-content">
                                            <option value="">Select a verified source</option>{proteinAdapters.map((adapter) => <option key={adapter.adapter_id} value={adapter.adapter_id}>{adapter.display_name}</option>)}
                                        </select>
                                        <div className="flex gap-2"><input aria-label="Search Protein source records" value={proteinSourceQuery} onChange={(event) => setProteinSourceQuery(event.target.value)} placeholder="Source record ID" className="min-w-0 flex-1 rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-xs text-content" /><button type="button" disabled={!proteinSourceAdapterId || proteinSourceSearch.isPending} onClick={() => proteinSourceSearch.mutate()} className="rounded-lg border border-border-primary px-3 py-2 text-xs font-semibold text-content-secondary disabled:opacity-50">Search</button></div>
                                        {(proteinSourceSearch.data?.items ?? []).map((item) => <label key={item.entity_id} className="flex gap-2 rounded-lg border border-border-primary p-2 text-xs text-content-secondary"><input type="radio" name="protein-source-record" checked={proteinSourceSelection?.entity_id === item.entity_id} disabled={!item.attachable} onChange={() => setProteinSourceSelection(item)} /><span><strong className="text-content">{item.label}</strong><br />{item.canonical_state}{item.reason ? ` · ${item.reason}` : ''}</span></label>)}
                                        <button type="button" disabled={!proteinSourceSelection?.attachable || proteinReceiptIssue.isPending} onClick={() => proteinReceiptIssue.mutate()} className="rounded-lg bg-accent px-3 py-2 text-xs font-semibold text-white disabled:opacity-50">Verify and add to target</button>
                                        {(proteinAdaptersQuery.isError || proteinSourceSearch.isError || proteinReceiptIssue.isError) && <p role="alert" className="text-xs text-error">{projectManagerErrorMessage(proteinAdaptersQuery.error ?? proteinSourceSearch.error ?? proteinReceiptIssue.error)}</p>}
                                    </div>

                                    <div className="space-y-3">
                                        <div className="flex items-center justify-between"><h3 className="text-sm font-semibold text-content">Targets</h3><button type="button" onClick={() => { setProteinTargets((current) => [...current, blankProteinTarget()]); setActiveProteinTarget(proteinTargets.length); }} className="rounded border border-border-primary px-2 py-1 text-xs">Add target</button></div>
                                        {proteinTargets.map((target, targetIndex) => {
                                            const updateTarget = (patch: Partial<ProteinTargetDraft>) => setProteinTargets((current) => current.map((item, index) => index === targetIndex ? { ...item, ...patch } : item));
                                            return <div key={targetIndex} className="space-y-3 rounded-lg border border-border-primary bg-surface-secondary p-3">
                                                <div className="flex items-center justify-between"><strong className="text-xs text-content">Target {targetIndex + 1}</strong>{proteinTargets.length > 1 && <button type="button" onClick={() => { setProteinTargets((current) => current.filter((_, index) => index !== targetIndex)); setActiveProteinTarget(0); }} className="text-xs text-error">Remove</button>}</div>
                                                <div className="grid gap-2 sm:grid-cols-3"><label className="text-xs font-semibold text-content-secondary">Target ID<input value={target.targetId} onChange={(event) => updateTarget({ targetId: event.target.value })} className="mt-1 w-full rounded border border-border-primary bg-surface px-2 py-1.5 text-sm" /></label><label className="text-xs font-semibold text-content-secondary">Label<input value={target.label} onChange={(event) => updateTarget({ label: event.target.value })} className="mt-1 w-full rounded border border-border-primary bg-surface px-2 py-1.5 text-sm" /></label><label className="text-xs font-semibold text-content-secondary">Role<select value={target.role} onChange={(event) => updateTarget({ role: event.target.value as ProteinTargetRole })} className="mt-1 w-full rounded border border-border-primary bg-surface px-2 py-1.5 text-sm">{['target', 'binder', 'partner', 'template', 'reference', 'control', 'motif', 'ligand_context', 'other'].map((role) => <option key={role} value={role}>{role.replace('_', ' ')}</option>)}</select></label></div>
                                                <label className="block text-xs font-semibold text-content-secondary">Verified source receipt IDs<input value={target.sourceReceiptIds.join(', ')} onChange={(event) => updateTarget({ sourceReceiptIds: event.target.value.split(',').map((value) => value.trim()).filter(Boolean) })} placeholder="comma-separated receipt IDs" className="mt-1 w-full rounded border border-border-primary bg-surface px-2 py-1.5 text-sm" /></label>
                                                <div className="space-y-2"><div className="flex justify-between"><span className="text-xs font-semibold text-content-secondary">Dataset members</span><button type="button" onClick={() => updateTarget({ datasetMembers: [...target.datasetMembers, { datasetRevisionId: '', memberId: '' }] })} className="text-xs text-accent">Add dataset member</button></div>{target.datasetMembers.map((member, memberIndex) => <div key={memberIndex} className="grid grid-cols-[1fr_1fr_auto] gap-2"><input aria-label="Dataset revision ID" value={member.datasetRevisionId} onChange={(event) => updateTarget({ datasetMembers: target.datasetMembers.map((item, index) => index === memberIndex ? { ...item, datasetRevisionId: event.target.value } : item) })} placeholder="Dataset revision ID" className="rounded border border-border-primary bg-surface px-2 py-1.5 text-xs" /><input aria-label="Dataset member ID" value={member.memberId} onChange={(event) => updateTarget({ datasetMembers: target.datasetMembers.map((item, index) => index === memberIndex ? { ...item, memberId: event.target.value } : item) })} placeholder="Member ID" className="rounded border border-border-primary bg-surface px-2 py-1.5 text-xs" /><button type="button" onClick={() => updateTarget({ datasetMembers: target.datasetMembers.filter((_, index) => index !== memberIndex) })} className="text-xs text-error">Remove</button></div>)}</div>
                                                <fieldset className="space-y-2 rounded border border-border-primary p-2"><legend className="px-1 text-xs font-semibold text-content-secondary">Entity-map reference</legend><div className="grid gap-2 sm:grid-cols-2"><label className="text-xs">Authority<select value={target.mapAuthorityKind} onChange={(event) => updateTarget({ mapAuthorityKind: event.target.value as ProteinTargetDraft['mapAuthorityKind'] })} className="mt-1 w-full rounded border border-border-primary bg-surface px-2 py-1.5"><option value="native_receipt">Native receipt</option><option value="governed_artifact_receipt">Governed artifact receipt</option></select></label><label className="text-xs">Receipt ID<input value={target.mapReceiptId} onChange={(event) => updateTarget({ mapReceiptId: event.target.value })} className="mt-1 w-full rounded border border-border-primary bg-surface px-2 py-1.5" /></label><label className="text-xs">Receipt SHA-256<input value={target.mapReceiptSha256} onChange={(event) => updateTarget({ mapReceiptSha256: event.target.value })} className="mt-1 w-full rounded border border-border-primary bg-surface px-2 py-1.5 font-mono text-[10px]" /></label><label className="text-xs">Map content SHA-256<input value={target.mapContentSha256} onChange={(event) => updateTarget({ mapContentSha256: event.target.value })} className="mt-1 w-full rounded border border-border-primary bg-surface px-2 py-1.5 font-mono text-[10px]" /></label><label className="text-xs">Canonical size (bytes)<input type="number" min="2" value={target.mapSizeBytes} onChange={(event) => updateTarget({ mapSizeBytes: event.target.value })} className="mt-1 w-full rounded border border-border-primary bg-surface px-2 py-1.5" /></label><label className="text-xs">Entity count<input type="number" min="1" value={target.mapEntityCount} onChange={(event) => updateTarget({ mapEntityCount: event.target.value })} className="mt-1 w-full rounded border border-border-primary bg-surface px-2 py-1.5" /></label><label className="text-xs">Residue mappings<input type="number" min="0" value={target.mapResidueCount} onChange={(event) => updateTarget({ mapResidueCount: event.target.value })} className="mt-1 w-full rounded border border-border-primary bg-surface px-2 py-1.5" /></label></div></fieldset>
                                                <label className="block text-xs font-semibold text-content-secondary">Expected source content SHA-256<input value={target.expectedContentSha256} onChange={(event) => updateTarget({ expectedContentSha256: event.target.value })} className="mt-1 w-full rounded border border-border-primary bg-surface px-2 py-1.5 font-mono text-xs" /></label>
                                            </div>;
                                        })}
                                    </div>

                                    <div className="grid gap-3 sm:grid-cols-2">{([
                                        { title: 'Planned capabilities', values: plannedCapabilityIds, draft: plannedCapabilityDraft, setDraft: setPlannedCapabilityDraft, setValues: setPlannedCapabilityIds, options: plannedCapabilityOptions },
                                        { title: 'Validation capabilities', values: validationCapabilityIds, draft: validationCapabilityDraft, setDraft: setValidationCapabilityDraft, setValues: setValidationCapabilityIds, options: validationCapabilityOptions },
                                    ] as const).map((section) => <div key={section.title} className="space-y-2 rounded-lg border border-border-primary p-3"><p className="text-xs font-semibold text-content-secondary">{section.title}</p><div className="flex gap-2"><select value={section.draft} onChange={(event) => section.setDraft(event.target.value)} disabled={proteinCapabilitiesQuery.isLoading || proteinCapabilitiesQuery.isError} className="min-w-0 flex-1 rounded border border-border-primary bg-surface-secondary px-2 py-1.5 text-xs"><option value="">Select a server capability</option>{section.options.map((capability) => <option key={capability.capabilityId} value={capability.capabilityId}>{capability.label}</option>)}</select><button type="button" disabled={!section.draft} onClick={() => { section.setValues((current) => Array.from(new Set([...current, section.draft]))); section.setDraft(''); }} className="rounded border border-border-primary px-2 text-xs disabled:opacity-50">Add</button></div>{section.values.map((value) => <div key={value} className="flex justify-between rounded bg-surface-secondary px-2 py-1 text-xs"><span>{(proteinCapabilitiesQuery.data ?? []).find((item) => item.capabilityId === value)?.label ?? value}</span><button type="button" onClick={() => section.setValues((current) => current.filter((item) => item !== value))} className="text-error">Remove</button></div>)}{!proteinCapabilitiesQuery.isLoading && !proteinCapabilitiesQuery.isError && section.options.length === 0 && <p className="text-[10px] text-content-muted">The server advertises no capability for this choice.</p>}</div>)}</div>
                                    {proteinCapabilitiesQuery.isError && <p className="text-xs text-error">Server capability choices are unavailable. No new capability can be added; existing IDs remain unchanged unless removed.</p>}

                                    <div className="rounded-lg border border-dashed border-border-primary p-3"><p className="text-xs font-semibold text-content-secondary">Design constraints</p><p className="mt-1 text-xs text-content-muted">No typed Protein constraint is currently registered by the server, so constraints cannot yet be added.</p></div>

                                    <div className="space-y-2 rounded-lg border border-border-primary p-3"><div className="flex justify-between"><p className="text-xs font-semibold text-content-secondary">Comparison groups</p><button type="button" disabled title="No accepted Protein capability advertises a comparison compatibility contract." className="text-xs text-content-muted opacity-50">Add group</button></div>{comparisonGroups.map((group, groupIndex) => { const updateGroup = (patch: Partial<ComparisonGroupDraft>) => setComparisonGroups((current) => current.map((item, index) => index === groupIndex ? { ...item, ...patch } : item)); return <div key={groupIndex} className="space-y-2 rounded border border-border-primary p-2"><div className="grid gap-2 sm:grid-cols-3"><input value={group.groupId} onChange={(event) => updateGroup({ groupId: event.target.value })} placeholder="Group ID" className="rounded border border-border-primary bg-surface-secondary px-2 py-1.5 text-xs" /><input value={group.label} onChange={(event) => updateGroup({ label: event.target.value })} placeholder="Group label" className="rounded border border-border-primary bg-surface-secondary px-2 py-1.5 text-xs" /><input value={group.compatibilityContractId} onChange={(event) => updateGroup({ compatibilityContractId: event.target.value })} placeholder="Compatibility contract ID" className="rounded border border-border-primary bg-surface-secondary px-2 py-1.5 text-xs" /></div>{group.members.map((member, memberIndex) => <div key={memberIndex} className="grid grid-cols-[1fr_1fr_auto] gap-2"><select value={member.targetId} onChange={(event) => updateGroup({ members: group.members.map((item, index) => index === memberIndex ? { ...item, targetId: event.target.value } : item) })} className="rounded border border-border-primary bg-surface-secondary px-2 py-1.5 text-xs"><option value="">Choose target</option>{proteinTargets.map((target) => <option key={target.targetId} value={target.targetId}>{target.label || target.targetId}</option>)}</select><select value={member.role} onChange={(event) => updateGroup({ members: group.members.map((item, index) => index === memberIndex ? { ...item, role: event.target.value as ComparisonRole } : item) })} className="rounded border border-border-primary bg-surface-secondary px-2 py-1.5 text-xs">{['reference', 'target', 'panel', 'control'].map((role) => <option key={role}>{role}</option>)}</select><button type="button" onClick={() => updateGroup({ members: group.members.filter((_, index) => index !== memberIndex) })} className="text-xs text-error">Remove</button></div>)}<div className="flex justify-between"><button type="button" onClick={() => updateGroup({ members: [...group.members, { targetId: '', role: 'target' }] })} className="text-xs text-accent">Add member</button><button type="button" onClick={() => setComparisonGroups((current) => current.filter((_, index) => index !== groupIndex))} className="text-xs text-error">Remove group</button></div></div>; })}</div>

                                    <div className="space-y-2 rounded-lg border border-border-primary p-3"><div className="flex justify-between"><p className="text-xs font-semibold text-content-secondary">Acceptance criteria</p><button type="button" onClick={() => setAcceptanceCriteria((current) => [...current, { criterionId: '', subjectRole: 'result', question: '', outcome: 'approve' }])} className="text-xs text-accent">Add criterion</button></div>{acceptanceCriteria.map((criterion, index) => <div key={index} className="grid gap-2 rounded border border-border-primary p-2 sm:grid-cols-2"><input value={criterion.criterionId} onChange={(event) => setAcceptanceCriteria((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, criterionId: event.target.value } : item))} placeholder="Criterion ID" className="rounded border border-border-primary bg-surface-secondary px-2 py-1.5 text-xs" /><select value={criterion.subjectRole} onChange={(event) => setAcceptanceCriteria((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, subjectRole: event.target.value as SubjectRole } : item))} className="rounded border border-border-primary bg-surface-secondary px-2 py-1.5 text-xs">{['input', 'sample', 'reference', 'target', 'panel', 'control', 'result', 'comparison', 'evidence', 'other'].map((role) => <option key={role}>{role}</option>)}</select><textarea value={criterion.question} onChange={(event) => setAcceptanceCriteria((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, question: event.target.value } : item))} placeholder="What must a reviewer decide?" rows={2} className="rounded border border-border-primary bg-surface-secondary px-2 py-1.5 text-xs sm:col-span-2" /><div className="flex items-center justify-between sm:col-span-2"><select value={criterion.outcome} onChange={(event) => setAcceptanceCriteria((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, outcome: event.target.value as AcceptanceDraft['outcome'] } : item))} className="rounded border border-border-primary bg-surface-secondary px-2 py-1.5 text-xs"><option value="approve">Must approve</option><option value="reject">Must reject</option><option value="record_only">Record only</option></select><button type="button" onClick={() => setAcceptanceCriteria((current) => current.filter((_, itemIndex) => itemIndex !== index))} className="text-xs text-error">Remove</button></div></div>)}</div>

                                    <div className="space-y-2 rounded-lg border border-border-primary p-3"><div className="flex justify-between"><p className="text-xs font-semibold text-content-secondary">Evidence requirements</p><button type="button" onClick={() => setEvidenceRequirements((current) => [...current, { requirementId: '', subjectRole: 'result', observationKind: '', prompt: '', required: true }])} className="text-xs text-accent">Add requirement</button></div>{evidenceRequirements.map((requirement, index) => <div key={index} className="grid gap-2 rounded border border-border-primary p-2 sm:grid-cols-2"><input value={requirement.requirementId} onChange={(event) => setEvidenceRequirements((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, requirementId: event.target.value } : item))} placeholder="Requirement ID" className="rounded border border-border-primary bg-surface-secondary px-2 py-1.5 text-xs" /><select value={requirement.subjectRole} onChange={(event) => setEvidenceRequirements((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, subjectRole: event.target.value as SubjectRole } : item))} className="rounded border border-border-primary bg-surface-secondary px-2 py-1.5 text-xs">{['input', 'sample', 'reference', 'target', 'panel', 'control', 'result', 'comparison', 'evidence', 'other'].map((role) => <option key={role}>{role}</option>)}</select><input value={requirement.observationKind} onChange={(event) => setEvidenceRequirements((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, observationKind: event.target.value } : item))} placeholder="Observation type" className="rounded border border-border-primary bg-surface-secondary px-2 py-1.5 text-xs" /><label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={requirement.required} onChange={(event) => setEvidenceRequirements((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, required: event.target.checked } : item))} />Required</label><textarea value={requirement.prompt} onChange={(event) => setEvidenceRequirements((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, prompt: event.target.value } : item))} placeholder="What evidence must the operator record?" rows={2} className="rounded border border-border-primary bg-surface-secondary px-2 py-1.5 text-xs sm:col-span-2" /><button type="button" onClick={() => setEvidenceRequirements((current) => current.filter((_, itemIndex) => itemIndex !== index))} className="justify-self-end text-xs text-error sm:col-span-2">Remove</button></div>)}</div>
                                </div>
                            )}
                            {mode === 'create_domain' && domainKind === 'ngs_molbio' && (
                                <label className="block text-xs font-semibold text-content-secondary">Experiment mode
                                    <select value={ngsExperimentMode} onChange={(event) => setNgsExperimentMode(event.target.value)} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface px-3 py-2.5 text-content">
                                        <option value="molecular_design">Molecular design</option>
                                        <option value="assembly_validation">Assembly validation</option>
                                        <option value="pcr_validation">PCR validation</option>
                                        <option value="sequencing">Sequencing</option>
                                        <option value="quality_control">Quality control</option>
                                        <option value="alignment">Alignment</option>
                                        <option value="comparison">Comparison</option>
                                        <option value="analysis">Analysis</option>
                                    </select>
                                </label>
                            )}
                            <label className="block text-xs font-semibold text-content-secondary">Objective
                                <textarea value={objective} onChange={(event) => setObjective(event.target.value)} rows={3} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface px-3 py-2.5 text-sm text-content outline-none focus:border-accent focus:ring-2 focus:ring-accent/30" />
                            </label>
                            {(mode === 'create_global' || (mode === 'edit' && selection?.node_type === 'global_experiment')) && <label className="block text-xs font-semibold text-content-secondary">Scientific question
                                <textarea value={question} onChange={(event) => setQuestion(event.target.value)} rows={2} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface px-3 py-2.5 text-sm text-content outline-none focus:border-accent focus:ring-2 focus:ring-accent/30" />
                            </label>}
                        </>
                    )}
                    {(mutation.isError || detailQuery.isError) && <p role="alert" className="rounded-lg border border-error/50 bg-error/10 p-3 text-xs text-error">{projectManagerErrorMessage(mutation.error ?? detailQuery.error)}{mutation.error && projectManagerErrorMessage(mutation.error).toLowerCase().includes('generation') ? ' Reload the current Project read model before retrying.' : ''}</p>}
                    <div className="flex justify-end gap-2 border-t border-border-primary pt-4">
                        <button type="button" onClick={onClose} className="rounded-lg border border-border-primary px-4 py-2 text-xs font-semibold text-content-secondary">Cancel</button>
                        <button type="submit" disabled={!canSubmit || mutation.isPending} className="rounded-lg bg-accent px-4 py-2 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50 focus:ring-2 focus:ring-accent">
                            {mutation.isPending ? 'Saving…' : mode === 'create_project' ? 'Create Project' : mode === 'archive' ? 'Archive without cancelling runs' : mode === 'restore' ? 'Restore' : mode === 'record' ? `Append ${recordKind}` : 'Save immutable revision'}
                        </button>
                    </div>
                </form>
            </section>
        </div>
    );
}
