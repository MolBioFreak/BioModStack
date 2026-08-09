import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';

import { FrustraMpnnComparisonCompatibility } from '../../src/components/FrustraMpnnComparisonSurface';
import { FrustraMpnnResultAuthoritySurface } from '../../src/components/FrustraMpnnResultAuthoritySurface';
import {
    parseFrustraMpnnStatistics,
    type FrustraMpnnPairComparison,
    type FrustraMpnnResultDetail,
} from '../../src/lib/frustraMpnnApi';
import { backendStatistics } from '../fixtures/frustraMpnnBackendContracts';

Object.defineProperty(globalThis, 'IS_REACT_ACT_ENVIRONMENT', { configurable: true, value: true });
afterEach(() => document.body.replaceChildren());

const mount = async (node: React.ReactNode) => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => root.render(node));
    return { container, root };
};

const unsafeComparison = {
    schema_name: 'frustrampnn_comparison',
    comparability: { status: 'incompatible', reasons: ['checkpoint_mismatch'] },
    override_used: true,
    compatibility_domains: {
        raw_score: { status: 'hard_incompatible', reasons: ['checkpoint_mismatch'], differences: [] },
        classification: { status: 'unknown', reasons: ['raw_score_incompatible'], differences: [] },
        identity_alignment: {
            status: 'partial', reasons: ['identity_set_differs'], differences: [],
            aligned_identity_count: 8, reference_identity_count: 10, target_identity_count: 9,
        },
    },
} as unknown as FrustraMpnnPairComparison;

const baseDetail = {
    authority_version: 'v2',
    availability: true,
    missing_fields: [],
    execution_receipt: {
        execution_configuration_sha256: 'a'.repeat(64), command_count: 1, assigned_physical_gpu_id: '0',
    },
    effective_settings_json: {
        requested_settings: {
            protein_selection: { mode: 'all_protein_entities', entities: [], residues: [] },
            source_structure: { selected_model_number: 2, preferred_altloc: 'A' },
            classification_policy: { mode: 'custom', high_max: -0.75, minimal_min: 0.25 },
        },
        settings_value_origin: 'operator_request',
        resolved_chains: [{
            entity: { entity_instance_id: 'entity-1', source_entity_id: '1', label_asym_id: 'AA', auth_asym_id: 'A', pdb_chain_id: 'A' },
            pdb_chain_id: 'A',
            residues: [{
                entity_instance_id: 'entity-1', source_entity_id: '1', label_asym_id: 'AA', auth_asym_id: 'A',
                auth_seq_id: 10, insertion_code: '', sequence_index: 1, wt: 'G', pdb_chain_id: 'A', model_position: 0,
            }],
        }],
        value_sources: {
            protein_selection: { mode: 'operator_request', entities: 'operator_request', residues: 'operator_request' },
            source_structure: { selected_model_number: 'operator_request', preferred_altloc: 'operator_request' },
            classification_policy: { mode: 'operator_request', high_max: 'operator_request', minimal_min: 'operator_request' },
        },
        effective_settings_sha256: 'b'.repeat(64),
    },
    statistics_json: parseFrustraMpnnStatistics({
        ...backendStatistics,
        support: {
            ...backendStatistics.support,
            selected_residue_count: 2,
            scoreable_residue_count: 2,
            scoreable_slot_count: 40,
            missing_residue_count: 1,
            missing_slot_count: 3,
        },
    }),
} as unknown as FrustraMpnnResultDetail;

describe('mounted FrustraMPNN comparison and result authority surfaces', () => {
    it('renders exact domain statuses, reasons, alignment, and safe override wording', async () => {
        const { container, root } = await mount(<FrustraMpnnComparisonCompatibility comparison={unsafeComparison} />);
        expect(container.textContent).toContain('Raw-score compatibility: hard_incompatible');
        expect(container.textContent).toContain('Raw score deltas are unsafe and remain null');
        expect(container.textContent).toContain('Classification transitions are unsafe and remain null');
        expect(container.textContent).toContain('8 aligned of 10 reference and 9 target identities');
        expect(container.textContent).toContain('checkpoint_mismatch');
        expect(container.textContent).toContain('does not authorize raw-score deltas or classification transitions');
        await act(async () => root.unmount());
    });

    it('renders v2 authority, effective settings, canonical statistics, and statistical missingness', async () => {
        const { container, root } = await mount(<FrustraMpnnResultAuthoritySurface detail={baseDetail} />);
        expect(container.textContent).toContain('Result authority: v2');
        expect(container.textContent).toContain('Requested: all_protein_entities');
        expect(container.textContent).toContain('Resolved chains: 1');
        expect(container.textContent).toContain('Requested model');
        expect(container.textContent).toContain('Effective model');
        expect(container.textContent).toContain('Requested altloc');
        expect(container.textContent).toContain('Effective altloc');
        expect(container.textContent).toContain('Highly frustrated ≤');
        expect(container.textContent).toContain('Minimally frustrated ≥');
        expect(container.textContent).toContain('Field-level value origins');
        expect(container.textContent).toContain('selected model number');
        expect(container.textContent).toContain('Canonical statistics');
        expect(container.textContent).toContain('Historical/statistical missingness: 1 residues and 3 slots');
        expect(container.textContent).not.toContain('/private/');
        await act(async () => root.unmount());
    });

    it('keeps ordered multi-target selection and renders per-target domains and safe rows', async () => {
        const { default: Workbench, FrustraMpnnMultiComparisonView } = await import(
            '../../src/components/FrustraMpnnComparisonWorkbench'
        );
        const availableTargets = [
            { parent_job_id: 'job-2', invocation_id: 'invoke-2', label: 'Candidate two' },
            { parent_job_id: 'job-3', invocation_id: 'invoke-3', label: 'Candidate three' },
        ];
        const mountedWorkbench = await mount(<Workbench
            referenceJobId="job-1"
            referenceInvocationId="invoke-1"
            availableTargets={availableTargets}
        />);
        const mode = mountedWorkbench.container.querySelector<HTMLSelectElement>('[aria-label="Comparison mode"]')!;
        await act(async () => {
            const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!;
            setter.call(mode, 'multi');
            mode.dispatchEvent(new Event('change', { bubbles: true }));
        });
        const targetSelect = mountedWorkbench.container.querySelector<HTMLSelectElement>('[aria-label="Available comparison target"]')!;
        const add = Array.from(mountedWorkbench.container.querySelectorAll('button')).find((button) => button.textContent === 'Add target')!;
        await act(async () => add.click());
        await act(async () => {
            const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!;
            setter.call(targetSelect, 'job-3\u0000invoke-3');
            targetSelect.dispatchEvent(new Event('change', { bubbles: true }));
        });
        await act(async () => add.click());
        const selectedLabels = () => Array.from(mountedWorkbench.container.querySelectorAll('[data-frustrampnn-selected-target]')).map((item) => item.textContent);
        expect(selectedLabels()).toEqual([expect.stringContaining('Candidate two'), expect.stringContaining('Candidate three')]);
        const moveUp = mountedWorkbench.container.querySelector<HTMLButtonElement>('[aria-label="Move Candidate three up"]')!;
        await act(async () => moveUp.click());
        expect(selectedLabels()).toEqual([expect.stringContaining('Candidate three'), expect.stringContaining('Candidate two')]);
        await act(async () => mountedWorkbench.root.unmount());

        const identity = { status: 'exact', reasons: [], differences: [], reference_identity_count: 1, target_identity_count: 1, aligned_identity_count: 1 };
        const pairs = [
            {
                target_label: 'target-0001', target_id: 'candidate-3', override_used: false,
                compatibility_domains: {
                    raw_score: { status: 'compatible', reasons: [], differences: [] },
                    classification: { status: 'compatible', reasons: [], differences: [] }, identity_alignment: identity,
                },
            },
            {
                target_label: 'target-0002', target_id: 'candidate-2', override_used: true,
                compatibility_domains: {
                    raw_score: { status: 'hard_incompatible', reasons: ['checkpoint_mismatch'], differences: [] },
                    classification: { status: 'unknown', reasons: ['raw_score_incompatible'], differences: [] }, identity_alignment: identity,
                },
            },
        ];
        const comparison = {
            schema_name: 'frustrampnn_multistate_comparison', target_labels: ['target-0001', 'target-0002'], pair_compatibility: pairs,
            source_result_references: [
                { role: 'reference', target_label: null, parent_job_id: 'job-1', invocation_id: 'invoke-1' },
                { role: 'target', target_label: 'target-0001', parent_job_id: 'job-3', invocation_id: 'invoke-3' },
                { role: 'target', target_label: 'target-0002', parent_job_id: 'job-2', invocation_id: 'invoke-2' },
            ],
            summary: {
                target_count: 2, total_rows: 1, biologically_scored: 0, partially_scored: 1,
                missing: 0, unmapped: 0, incompatible: 0, transitions: 1,
                browser_metric_must_not_render: 777,
            }, comparability: { status: 'incompatible', reasons: ['checkpoint_mismatch'] }, override_used: true,
            rows: [{
                residue_key: { entity_instance_id: 'entity-1', auth_asym_id: 'A', auth_seq_id: 10, insertion_code: '' }, sequence_index: 1,
                mutation_aa: 'A', mapping_state: 'mapped', missingness_state: 'none', missingness_by_target: ['none', 'none'], biological_status: 'partially_scored',
                reference: { score: 0, class: 'neutral', status: 'ok' },
                targets: [{ score: 0.25, class: 'minimal', status: 'ok' }, { score: 7.77, class: 'high', status: 'ok' }],
                raw_score_deltas: [0.25, 9.99], classification_transitions: ['neutral→minimal', 'unsafe-transition'],
            }],
        } as never;
        const mountedView = await mount(<FrustraMpnnMultiComparisonView comparison={comparison} />);
        expect(mountedView.container.textContent).toContain('target-0001 · candidate-3');
        expect(mountedView.container.textContent).toContain('target-0002 · candidate-2');
        expect(mountedView.container.textContent).toContain('checkpoint_mismatch');
        expect(mountedView.container.textContent).toContain('0.2500');
        expect(mountedView.container.textContent).toContain('neutral→minimal');
        expect(mountedView.container.textContent).not.toContain('9.9900');
        expect(mountedView.container.textContent).not.toContain('unsafe-transition');
        expect(mountedView.container.textContent).not.toContain('browser metric must not render');
        expect(mountedView.container.textContent).not.toContain('777');
        expect(mountedView.container.textContent).toContain('Partially scored');
        expect(mountedView.container.textContent).toContain('Unsafe delta and transition hidden');
        await act(async () => mountedView.root.unmount());
    });

    it('renders historical authority and missingness without reconstructing absent settings or statistics', async () => {
        const historical = {
            ...baseDetail,
            authority_version: 'historical_v1',
            availability: false,
            missing_fields: ['effective_settings_json', 'statistics_json'],
            effective_settings_json: null,
            statistics_json: null,
            execution_receipt: null,
        } as unknown as FrustraMpnnResultDetail;
        const { container, root } = await mount(<FrustraMpnnResultAuthoritySurface detail={historical} />);
        expect(container.textContent).toContain('Result authority: historical_v1');
        expect(container.textContent).toContain('Missing authority: effective_settings_json, statistics_json');
        expect(container.textContent).toContain('Effective settings were not recorded');
        expect(container.textContent).toContain('predates persisted statistics authority');
        expect(container.textContent).toContain('not reconstructed');
        await act(async () => root.unmount());
    });
});
