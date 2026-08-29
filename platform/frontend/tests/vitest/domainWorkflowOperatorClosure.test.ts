import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import {
    parameterIsVisible,
    parameterValuesFromDraft,
    type ParameterField,
} from '../../src/components/molbio-ngs/DomainWorkflowOperator';

function field(overrides: Partial<ParameterField> & Pick<ParameterField, 'name' | 'kind'>): ParameterField {
    return {
        name: overrides.name,
        label: overrides.name,
        kind: overrides.kind,
        required: true,
        nullable: false,
        ...overrides,
    };
}

describe('NGS/MolBio schema-driven Workflow Plan settings', () => {
    it('hydrates explicit and contextual schema defaults without replacing persisted requests', () => {
        const fields: ParameterField[] = [
            field({ name: 'basecalling_mode', kind: 'string', defaultValue: 'simplex' }),
            field({
                name: 'batch_size',
                kind: 'integer',
                defaultPolicy: {
                    kind: 'contextual_defaults',
                    canonicalText: 'simplex=64;duplex=32',
                    entries: [
                        { context: 'simplex', value: 64 },
                        { context: 'duplex', value: 32 },
                    ],
                },
            }),
        ];

        expect(parameterValuesFromDraft({}, fields)).toEqual({
            basecalling_mode: 'simplex',
            batch_size: 64,
        });
        expect(parameterValuesFromDraft({
            parameters: { basecalling_mode: 'duplex', batch_size: 24 },
        }, fields)).toEqual({
            basecalling_mode: 'duplex',
            batch_size: 24,
        });
    });

    it('uses exact field or native-key conditions and keeps authority-bound prose visible', () => {
        const fields: ParameterField[] = [
            field({ name: 'basecalling_mode', kind: 'string' }),
            field({ name: 'quality_mode', kind: 'string' }),
            field({ name: 'barcode_kit', kind: 'string', nullable: true }),
            field({ name: 'wf_clone_assembly_tool', kind: 'string', nativeKey: 'assembly_tool' }),
            field({ name: 'validation_sample_limit', kind: 'integer', applicability: 'basecalling_mode=duplex' }),
            field({ name: 'model_path', kind: 'string', applicability: 'basecalling_mode=simplex and quality_mode=hac and barcode_kit=null' }),
            field({ name: 'wf_canu_reads_raw', kind: 'boolean', nativeKey: 'canu_reads_raw', applicability: 'assembly_tool=canu' }),
            field({ name: 'target_sequence', kind: 'string', applicability: 'target receipt present' }),
        ];
        const values = {
            basecalling_mode: 'simplex',
            quality_mode: 'hac',
            barcode_kit: null,
            wf_clone_assembly_tool: 'flye',
        };

        expect(parameterIsVisible(fields[4], fields, values)).toBe(false);
        expect(parameterIsVisible(fields[5], fields, values)).toBe(true);
        expect(parameterIsVisible(fields[6], fields, values)).toBe(false);
        expect(parameterIsVisible(fields[6], fields, { ...values, wf_clone_assembly_tool: 'canu' })).toBe(true);
        expect(parameterIsVisible(fields[7], fields, values)).toBe(true);
    });
});

describe('NGS/MolBio frontend closure wiring', () => {
    it('routes Project Manager selection directly to the shared Plans & Runs operator', () => {
        const manager = readFileSync(resolve(process.cwd(), 'src/pages/ProjectManager.tsx'), 'utf8');
        const inspector = readFileSync(resolve(process.cwd(), 'src/components/project-manager/ProjectInspector.tsx'), 'utf8');
        const workspace = readFileSync(resolve(process.cwd(), 'src/components/molbio-ngs/DomainExperimentWorkspace.tsx'), 'utf8');

        expect(manager).toContain("section: 'workflow-plans'");
        expect(inspector).toContain("selection.summary.schema === 'bms.protein-in-silico-experiment.v3'");
        expect(inspector).toContain('Open Plans &amp; Runs workspace');
        expect(workspace).toContain('<DomainWorkflowOperator');
    });

    it('routes the selected NGS Domain to its ordinary persisted Run Inspector', () => {
        const manager = readFileSync(resolve(process.cwd(), 'src/pages/ProjectManager.tsx'), 'utf8');
        const inspector = readFileSync(resolve(process.cwd(), 'src/components/project-manager/ProjectInspector.tsx'), 'utf8');

        expect(manager).toContain("section: 'analyses'");
        expect(manager).toContain('onOpenNgsRuns={openNgsRunInspector}');
        expect(inspector).toContain('Open NGS Run Inspector');
        expect(inspector).toContain('selection.summary.domain_payload');
    });

    it('uses one pinned typed destination and retains exact return/reopen context', () => {
        const operator = readFileSync(resolve(process.cwd(), 'src/components/molbio-ngs/DomainWorkflowOperator.tsx'), 'utf8');

        expect(operator).toContain('canonical_source_destination');
        expect(operator).toContain('appendLaunchContext(sourceDestination, launchContext.launch_context_id)');
        expect(operator).toContain('Open exact typed native launcher');
        expect(operator).toContain("section: 'datasets'");
        expect(operator).toContain('requested={selection.preparation.requested_settings}');
        expect(operator).toContain('effective={selection.preparation.effective_settings}');
        expect(operator).toContain('launch_context_id: launchContextId');
        expect(operator).toContain('params: { ...parameterValues, workflow_adapter: draft.adapter_id }');
        expect(operator).not.toContain("appendLaunchContext('/molbio'");
        expect(operator).not.toContain("appendLaunchContext('/ngs'");
    });
});
