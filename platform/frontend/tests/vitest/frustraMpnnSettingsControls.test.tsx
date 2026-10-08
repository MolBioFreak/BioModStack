import React, { act, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';

import { ModelIntegrationControl } from '../../src/components/ModelIntegrationControl';
import { FrustraMpnnSettingsPanel } from '../../src/components/frustrampnn/FrustraMpnnSettingsPanel';
import {
    CANONICAL_FRUSTRAMPNN_SETTINGS,
    type FrustraMpnnRequestedSettings,
} from '../../src/components/frustrampnn/frustraMpnnSettingsState';
import type { FrustraMpnnSourceInspection } from '../../src/lib/frustraMpnnApi';

Object.defineProperty(globalThis, 'IS_REACT_ACT_ENVIRONMENT', { configurable: true, value: true });

afterEach(() => document.body.replaceChildren());

const inspection: FrustraMpnnSourceInspection = {
    source_models: [1, 2],
    selected_source_model: 2,
    observed_altlocs: ['', 'A'],
    selected_altloc: 'A',
    protein_entities: [
        {
            entity_instance_id: 'entity-1',
            source_entity_id: '1',
            label_asym_id: 'AA',
            auth_asym_id: 'A',
            pdb_chain_id: 'A',
        },
        {
            entity_instance_id: 'entity-2',
            source_entity_id: '2',
            label_asym_id: 'BB',
            auth_asym_id: 'B',
            pdb_chain_id: 'B',
        },
    ],
    mapped_residues: [
        {
            entity_instance_id: 'entity-1',
            source_entity_id: '1',
            label_asym_id: 'AA',
            auth_asym_id: 'A',
            auth_seq_id: 10,
            insertion_code: '',
            sequence_index: 1,
            wt: 'M',
        },
        {
            entity_instance_id: 'entity-2',
            source_entity_id: '2',
            label_asym_id: 'BB',
            auth_asym_id: 'B',
            auth_seq_id: 20,
            insertion_code: '',
            sequence_index: 2,
            wt: 'G',
        },
        {
            entity_instance_id: 'entity-1',
            source_entity_id: '1',
            label_asym_id: 'AA',
            auth_asym_id: 'A',
            auth_seq_id: 11,
            insertion_code: '',
            sequence_index: 2,
            wt: 'A',
        },
        {
            entity_instance_id: 'entity-1',
            source_entity_id: '1',
            label_asym_id: 'AA',
            auth_asym_id: 'A',
            auth_seq_id: 12,
            insertion_code: '',
            sequence_index: 3,
            wt: 'V',
        },
    ],
};

const dispatchChange = (element: HTMLInputElement | HTMLSelectElement, value: string) => {
    const descriptor = Object.getOwnPropertyDescriptor(
        element instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype,
        'value',
    );
    descriptor?.set?.call(element, value);
    element.dispatchEvent(new Event('change', { bubbles: true }));
};

function PanelHarness(props: {
    sourceInspection?: FrustraMpnnSourceInspection;
    sourceStructurePolicy?: 'operator' | 'derived';
    allowIndividualResidues?: boolean;
}) {
    const sourceInspection = Object.prototype.hasOwnProperty.call(props, 'sourceInspection')
        ? props.sourceInspection
        : inspection;
    const [settings, setSettings] = useState<FrustraMpnnRequestedSettings>(CANONICAL_FRUSTRAMPNN_SETTINGS);
    return (
        <>
            <FrustraMpnnSettingsPanel
                value={settings}
                onChange={setSettings}
                inspection={sourceInspection}
                sourceStructurePolicy={props.sourceStructurePolicy}
                allowIndividualResidues={props.allowIndividualResidues}
            />
            <output data-settings-state>{JSON.stringify(settings)}</output>
        </>
    );
}

describe('typed FrustraMPNN settings controls', () => {
    it('renders a compact analysis summary and supports inspected chain, region, and advanced residue scope', async () => {
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);
        await act(async () => root.render(<PanelHarness />));

        expect(container.textContent).toContain('FrustraMPNN analysis');
        expect(container.textContent).toContain('Scope: All mapped protein residues');
        expect(container.textContent).toContain('Classification: Canonical');
        expect(container.textContent).toContain('Model execution scope');
        expect(container.textContent).toContain('Result classification');
        expect(container.querySelector<HTMLDetailsElement>('[data-frustrampnn-settings-details]')?.open).toBe(false);

        const selectionMode = container.querySelector<HTMLSelectElement>('[data-frustrampnn-selection-mode]');
        expect(selectionMode).toBeTruthy();
        await act(async () => dispatchChange(selectionMode!, 'selected_entities'));
        expect(container.querySelectorAll('[data-frustrampnn-entity-option]')).toHaveLength(2);
        expect(container.querySelector('[data-settings-state]')?.textContent).toContain('"mode":"selected_entities"');
        expect(container.querySelector('[data-settings-state]')?.textContent).toContain('"entity_instance_id":"entity-1"');

        await act(async () => dispatchChange(selectionMode!, 'selected_regions'));
        expect(container.querySelectorAll('[data-frustrampnn-region-row]')).toHaveLength(1);
        const start = container.querySelector<HTMLInputElement>('[data-frustrampnn-region-start]');
        await act(async () => dispatchChange(start!, '3'));
        expect(selectionMode?.value).toBe('selected_regions');
        expect(container.querySelector('[data-settings-state]')?.textContent).toContain('"sequence_start":3');
        const updatedStart = container.querySelector<HTMLInputElement>('[data-frustrampnn-region-start]');
        await act(async () => dispatchChange(updatedStart!, '2'));
        expect(container.querySelector('[data-settings-state]')?.textContent).toContain('"sequence_start":2');
        const addRegion = container.querySelector<HTMLButtonElement>('[data-frustrampnn-add-region]');
        await act(async () => addRegion!.click());
        expect(container.querySelectorAll('[data-frustrampnn-region-row]')).toHaveLength(2);

        await act(async () => dispatchChange(selectionMode!, 'selected_residues'));
        expect(container.querySelectorAll('[data-frustrampnn-residue-option]')).toHaveLength(4);
        expect(selectionMode?.querySelector('option[value="selected_residues"]')?.textContent).toContain('advanced');
        expect(container.querySelector('[data-settings-state]')?.textContent).toContain('"mode":"selected_residues"');
        expect(container.querySelector('[data-settings-state]')?.textContent).toContain('"sequence_index":1');

        await act(async () => dispatchChange(selectionMode!, 'all_protein_entities'));
        expect(container.querySelector('[data-settings-state]')?.textContent).toContain('"mode":"all_protein_entities"');

        await act(async () => root.unmount());
    });

    it('derives generated-conformer normalization and exposes safe advanced sequence positions', async () => {
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);
        await act(async () => root.render(
            <PanelHarness
                sourceStructurePolicy="derived"
                allowIndividualResidues={false}
            />,
        ));

        expect(container.querySelector('[data-frustrampnn-source-model]')).toBeNull();
        expect(container.querySelector('[data-frustrampnn-altloc]')).toBeNull();
        expect(container.textContent).toContain('Derived from each canonical generated conformer');

        const mode = container.querySelector<HTMLSelectElement>('[data-frustrampnn-selection-mode]');
        const advanced = mode?.querySelector<HTMLOptionElement>('option[value="selected_sequence_positions"]');
        expect(advanced?.textContent).toContain('Individual residues (advanced)');
        expect(advanced?.disabled).toBe(false);
        await act(async () => dispatchChange(mode!, 'selected_sequence_positions'));
        expect(container.querySelector('[data-settings-state]')?.textContent).toContain('"mode":"selected_regions"');
        expect(container.querySelector('[data-settings-state]')?.textContent).toContain('"sequence_start":1,"sequence_end":1');
        expect(container.textContent).toContain('Scope: 1 individual sequence position');

        await act(async () => root.unmount());
    });

    it('offers inspected source model and altloc choices and accepts ordered custom finite thresholds', async () => {
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);
        await act(async () => root.render(<PanelHarness />));

        const model = container.querySelector<HTMLSelectElement>('[data-frustrampnn-source-model]');
        expect(Array.from(model?.options ?? []).map((option) => option.value)).toEqual(['1', '2']);
        await act(async () => dispatchChange(model!, '2'));

        const altloc = container.querySelector<HTMLSelectElement>('[data-frustrampnn-altloc]');
        expect(Array.from(altloc?.options ?? []).map((option) => option.value)).toEqual(['', 'A']);
        await act(async () => dispatchChange(altloc!, 'A'));

        const classificationMode = container.querySelector<HTMLSelectElement>('[data-frustrampnn-classification-mode]');
        await act(async () => dispatchChange(classificationMode!, 'custom'));
        const highMax = container.querySelector<HTMLInputElement>('[data-frustrampnn-high-max]');
        const minimalMin = container.querySelector<HTMLInputElement>('[data-frustrampnn-minimal-min]');
        await act(async () => dispatchChange(highMax!, '-0.75'));
        await act(async () => dispatchChange(minimalMin!, '0.25'));

        const state = container.querySelector('[data-settings-state]')?.textContent ?? '';
        expect(state).toContain('"selected_model_number":2');
        expect(state).toContain('"preferred_altloc":"A"');
        expect(state).toContain('"mode":"custom"');
        expect(state).toContain('"high_max":-0.75');
        expect(state).toContain('"minimal_min":0.25');
        expect(container.querySelector('[role="alert"]')).toBeNull();

        const rendered = container.textContent?.toLowerCase() ?? '';
        for (const forbidden of ['raw json', 'command', 'runtime', 'scheduler', 'storage', 'gpu']) {
            expect(rendered).not.toContain(forbidden);
        }

        await act(async () => root.unmount());
    });

    it('keeps exact entity and residue selectors unavailable before inspection metadata exists', async () => {
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);
        await act(async () => root.render(<PanelHarness sourceInspection={undefined} />));

        const selectionMode = container.querySelector<HTMLSelectElement>('[data-frustrampnn-selection-mode]');
        expect(selectionMode?.value).toBe('all_protein_entities');
        expect(selectionMode?.querySelector<HTMLOptionElement>('option[value="selected_entities"]')?.disabled).toBe(true);
        expect(selectionMode?.querySelector<HTMLOptionElement>('option[value="selected_regions"]')?.disabled).toBe(true);
        expect(selectionMode?.querySelector<HTMLOptionElement>('option[value="selected_residues"]')?.disabled).toBe(true);
        expect(container.textContent).toContain('Exact source entity, sequence-region, and residue selectors are unavailable until source inspection is produced.');
        expect(container.querySelector('[data-frustrampnn-source-model]')).toBeTruthy();
        expect(container.querySelector('[data-frustrampnn-altloc]')).toBeTruthy();
        expect(container.querySelector('[data-frustrampnn-classification-mode]')).toBeTruthy();

        await act(async () => root.unmount());
    });

    it('renders a generic settings region only while enabled and preserves its typed state across toggles', async () => {
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);

        function IntegrationHarness() {
            const [enabled, setEnabled] = useState(true);
            const [settings, setSettings] = useState<FrustraMpnnRequestedSettings>(CANONICAL_FRUSTRAMPNN_SETTINGS);
            return (
                <ModelIntegrationControl
                    modelId="frustrampnn"
                    workflowId="structure_prediction"
                    checked={enabled}
                    onChange={setEnabled}
                    fallbackLabel="Frustration analysis"
                    settingsControl={(
                        <FrustraMpnnSettingsPanel
                            value={settings}
                            onChange={setSettings}
                            inspection={inspection}
                        />
                    )}
                />
            );
        }

        await act(async () => root.render(<IntegrationHarness />));
        expect(container.querySelectorAll('[data-frustrampnn-settings-panel]')).toHaveLength(1);
        const mode = container.querySelector<HTMLSelectElement>('[data-frustrampnn-classification-mode]');
        await act(async () => dispatchChange(mode!, 'custom'));
        const highMax = container.querySelector<HTMLInputElement>('[data-frustrampnn-high-max]');
        await act(async () => dispatchChange(highMax!, '-0.75'));

        const enabledToggle = container.querySelector<HTMLInputElement>('input[type="checkbox"]');
        await act(async () => enabledToggle!.click());
        expect(container.querySelector('[data-frustrampnn-settings-panel]')).toBeNull();

        await act(async () => enabledToggle!.click());
        expect(container.querySelectorAll('[data-frustrampnn-settings-panel]')).toHaveLength(1);
        expect(container.querySelector<HTMLSelectElement>('[data-frustrampnn-classification-mode]')?.value).toBe('custom');
        expect(container.querySelector<HTMLInputElement>('[data-frustrampnn-high-max]')?.value).toBe('-0.75');

        await act(async () => root.unmount());
    });
});
