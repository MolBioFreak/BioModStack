import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { describe, it } from 'node:test';

const frontendRoot = process.cwd();
const src = (relativePath: string) => fs.readFileSync(path.join(frontendRoot, 'src', relativePath), 'utf8');


describe('De Novo Design product boundary', () => {
    it('offers explicit modes under one parent launcher', () => {
        const template = src('components/ProteinModificationTemplate.tsx');

        assert.equal(template.includes("import { DE_NOVO_MODIFICATION_MODE_CARDS"), true);
        assert.equal(template.includes("model_id: 'protein_modification_experimental'"), true);
        assert.equal(template.includes("mode: 'de_novo_design'"), true);
        assert.equal(template.includes("mode === 'rfd3_iteration'"), true);
        assert.equal(template.includes("initialMode === 'rfd3_local_redesign' || initialMode === 'region_redesign'"), true);
        assert.equal(template.includes("submissionModelId={reopenValidatedPipeline ? 'protein_modification_experimental' : 'protein_local_redesign'}"), true);
        assert.equal(template.includes('submissionMode='), false);
        assert.equal(template.includes('Protein Hunter'), false);
        assert.equal(template.includes('Iterative Binder Design'), false);
    });

    it('keeps legacy templates hidden and routes legacy model IDs to the parent', () => {
        const submission = src('components/JobSubmission.tsx');

        assert.equal(submission.includes("protein_local_redesign: 'protein_modification_experimental'"), true);
        assert.equal(submission.includes("protein_cad_experimental: 'protein_modification_experimental'"), true);
        assert.equal(submission.includes("\n            id: 'protein_local_redesign'"), false);
        assert.equal(submission.includes("selectedTemplateId === 'protein_local_redesign'"), false);
        assert.equal(submission.includes('!LEGACY_PROTEIN_MODIFICATION_TEMPLATE_IDS.has(t.id)'), true);
    });

    it('documents all engines on the parent rather than separate product inventory entries', () => {
        const inventory = src('components/workflowModelInventory.ts');

        assert.match(
            inventory,
            /workflowId: 'protein_modification_experimental'[\s\S]*label: 'De Novo Design'[\s\S]*modelTopics: \['laproteina', 'disco', 'rfdiffusion', 'fampnn', 'proteinmpnn', 'boltz2'\]/,
        );
        assert.equal(inventory.includes("workflowId: 'protein_local_redesign'"), false);
        assert.equal(inventory.includes("workflowId: 'protein_cad_experimental'"), false);
    });
});
