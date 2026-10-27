import {
    api, fetchMolBioNgsDomainState, fetchMolBioNgsStateRevision,
    issueMolecularRevisionMemberReceipt, saveMolBioNgsStateRevision,
    type DomainStateRevisionPayload,
} from './api';
import { attachExistingEntity, createResearchRecord, getDomainExperiment, getProject } from './projectManager';

export const MOLECULAR_MEMBER_ADAPTER = 'bms.molbio.member-molecular-revision.adapter.v1';

// A sequence inventory does not authorize acquisition or analysis. Existing
// workspaces always retain their complete saved policy when membership changes.
export function sequenceInventoryPolicy(): DomainStateRevisionPayload {
    return {
        schema: 'bms.molbio-ngs.domain-state-revision.v1',
        design: { sample_revision_ids: [], conditions: [], replicates: [], expected_molecule_roles: [] },
        reference_policy: { required_roles: [], coordinate_policy: 'exact_revision' },
        acquisition_policy: { platform: 'none', required_terminal_manifest: true },
        analysis_policy: { allowed_workflow_ids: [], required_manifest_schemas: [] },
        assessment_policy: { rule_id: 'server-owned-rule', completion_is_scientific_pass: false },
        notes: '',
    };
}

export async function addMolecularProjectMember(
    projectId: string, experimentId: string, domainId: string,
    entityId: string, expectedStateRevisionId?: string | null,
) {
    const identity = new URLSearchParams(entityId);
    const sequenceId = identity.get('sequence_id');
    const revisionId = identity.get('revision_id');
    if (!sequenceId || !revisionId) throw new Error('Select an exact saved molecular revision.');
    const state = await fetchMolBioNgsDomainState(domainId);
    const current = state.current_state_revision_id
        ? await fetchMolBioNgsStateRevision(domainId, state.current_state_revision_id) : null;
    const receipt = await issueMolecularRevisionMemberReceipt({ sequence_id: sequenceId, revision_id: revisionId });
    const present = current?.members.find((member) => member.receipt_id === receipt.receipt_id);
    if (present) return current!;
    if (expectedStateRevisionId && state.current_state_revision_id !== expectedStateRevisionId) {
        throw new Error('The workspace advanced. Reopen its current state before attaching this sequence.');
    }
    const domain = await getDomainExperiment(projectId, experimentId, domainId);
    if (!domain.current_revision_id) throw new Error('The current Domain revision is unavailable.');
    const members = (current?.members ?? []).filter((member) => {
        const params = member.reopen_destination?.params as { sequence_id?: string } | undefined;
        return !(member.role === 'molecular_expected_construct' && member.sample_revision_id === null && member.entity_kind === 'molecular_revision' && params?.sequence_id === sequenceId);
    }).map(({ receipt_id, role, ordinal, sample_revision_id }) => ({ receipt_id, role, ordinal, sample_revision_id }));
    members.push({ receipt_id: receipt.receipt_id, role: 'molecular_expected_construct',
        ordinal: Math.max(-1, ...members.map((member) => member.ordinal)) + 1, sample_revision_id: null });
    return saveMolBioNgsStateRevision(domainId, {
        global_domain_experiment_revision_id: domain.current_revision_id,
        expected_head_generation: state.head_generation,
        parent_revision_id: state.current_state_revision_id,
        idempotency_key: crypto.randomUUID(),
        payload: current?.payload ?? sequenceInventoryPolicy(),
        members,
    });
}

export interface ProjectExportJournalEntry { timestamp: string; label: string; summary: string }
export interface MolecularProjectHistory {
    schema: 'bms.molbio.project-export-history.v1';
    sequence_id: string; selected_revision_id: string; name: string; complete: true; manifest_sha256: string;
    entries: Array<{ kind: 'revision' | 'operation' | 'pcr_review'; id: string; label: string;
        adapter_id: string | null; entity_id: string | null; content_sha256: string | null; reopen_href: string }>;
}

export async function readMolecularProjectHistory(entityId: string): Promise<MolecularProjectHistory> {
    const identity = new URLSearchParams(entityId);
    const sequenceId = identity.get('sequence_id');
    const revisionId = identity.get('revision_id');
    if (!sequenceId || !revisionId) throw new Error('Select an exact saved sequence first.');
    const { data } = await api.get<MolecularProjectHistory>(`/api/molbio/sequences/${encodeURIComponent(sequenceId)}/project-export-history`, { params: { revision_id: revisionId } });
    if (data.schema !== 'bms.molbio.project-export-history.v1' || data.complete !== true || data.sequence_id !== sequenceId || data.selected_revision_id !== revisionId) {
        throw new Error('The complete history does not match the selected saved sequence.');
    }
    return data;
}

export async function exportMolecularProjectHistory(
    projectId: string, globalExperimentId: string, domainExperimentId: string,
    history: MolecularProjectHistory, journal: ProjectExportJournalEntry[], currentReceiptId: string,
) {
    const receiptIds = [currentReceiptId];
    const lines = [`Plasmid history: ${history.name}`, `Sequence: ${history.sequence_id}`,
        `Selected revision: ${history.selected_revision_id}`, `History manifest SHA-256: ${history.manifest_sha256}`,
        'Saved molecular revisions, related saved operations and PCR reviews captured at export:', ''];
    for (const entry of history.entries) {
        if (entry.adapter_id && entry.entity_id) {
            const project = await getProject(projectId);
            const receipt = await attachExistingEntity(projectId, globalExperimentId, domainExperimentId, {
                adapter_id: entry.adapter_id, entity_id: entry.entity_id, operation: 'attach_reference', role: 'references',
                note: `Plasmid history export: ${history.sequence_id}`, expected_head_generation: project.head_generation,
            });
            receiptIds.push(receipt.source_receipt_id);
        }
        lines.push(`- [${entry.kind} ${entry.id}](${entry.reopen_href})${entry.content_sha256 ? ` · SHA-256 ${entry.content_sha256}` : ''}`);
    }
    lines.push('', 'Recorded local edit history (operator journal; separate from immutable server revisions):');
    for (const entry of journal) lines.push(`${entry.timestamp} · ${entry.label} · ${entry.summary}`);
    if (!journal.length) lines.push('No local edit journal entries were recorded in this workspace.');
    // Persist the complete journal in bounded append-only notes. No sequence or
    // scientific result payload is copied out of its canonical source store.
    const parts: string[] = [];
    let part = '';
    for (const line of lines) {
        if (part.length + line.length > 24_000 && part) { parts.push(part); part = ''; }
        part += `${line}\n`;
    }
    if (part) parts.push(part);
    for (const [index, body] of parts.entries()) await createResearchRecord({ projectId, globalExperimentId, domainExperimentId }, {
        record_kind: 'note', body: `Plasmid history export ${history.manifest_sha256} — part ${index + 1}/${parts.length}\n\n${body}`,
        source_receipt_ids: [...new Set(receiptIds)],
    });
}

/** Remove current Project membership; canonical sequences and prior states remain intact. */
export async function removeMolecularProjectMember(projectId: string, experimentId: string, domainId: string, sequenceId: string, expectedStateRevisionId: string) {
    const state = await fetchMolBioNgsDomainState(domainId);
    if (state.current_state_revision_id !== expectedStateRevisionId) throw new Error('This Project changed. Open its current sequence workspace before deleting membership.');
    const current = await fetchMolBioNgsStateRevision(domainId, expectedStateRevisionId);
    const members = current.members.filter(member => {
        const params = member.reopen_destination?.params as {sequence_id?:string}|undefined;
        return !(member.entity_kind === 'molecular_revision' && params?.sequence_id === sequenceId);
    }).map(({receipt_id, role, ordinal, sample_revision_id})=>({receipt_id,role,ordinal,sample_revision_id}));
    if (members.length === current.members.length) throw new Error('The sequence is no longer a member of this Project state.');
    const domain = await getDomainExperiment(projectId,experimentId,domainId);
    if (!domain.current_revision_id) throw new Error('The current workspace revision is unavailable.');
    return saveMolBioNgsStateRevision(domainId,{
        global_domain_experiment_revision_id:domain.current_revision_id,
        expected_head_generation:state.head_generation,parent_revision_id:expectedStateRevisionId,
        idempotency_key:crypto.randomUUID(),payload:current.payload,members,
    });
}
