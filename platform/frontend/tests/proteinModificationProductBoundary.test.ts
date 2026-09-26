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
        assert.match(template, /model_id: 'protein_modification_experimental'/);
        assert.match(template, /mode: 'de_novo_design'/);
        assert.match(template, /generator: 'rfd3'/);
        assert.match(template, /generation_mode: 'unconditional_monomer'/);
        assert.match(template, /min_length: minLength/);
        assert.match(template, /max_length: maxLength/);
        assert.match(template, /num_designs: numDesigns/);
        assert.match(template, /seed/);
        assert.match(template, /dump_trajectories: dumpTrajectories/);
        assert.doesNotMatch(template, /Experimental backup methods/);
        assert.match(template, /aria-label="Design task"/);
        assert.match(template, /DISCO/);
        assert.match(template, /La-Proteina/);
        assert.equal(template.includes("mode === 'rfd3_iteration'"), true);
        assert.match(template, /submissionModelId=\{validatedRedesign \? 'protein_modification_experimental' : 'protein_local_redesign'\}/);
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
        const catalog = src('lib/launcherCatalog.ts');
        assert.match(submission, /visibleLauncherTemplates/);
        assert.match(catalog, /name: 'De Novo Design'[\s\S]*Generate · RFD3[\s\S]*Redesign structure[\s\S]*Shape[\s\S]*DISCO · La-Proteina/);
        assert.match(catalog, /'protein_cad_experimental', 'protein_local_redesign'/);
        assert.match(submission, /isDeNovoModel\(data.model_id\)/);
    });

    it('documents all engines on the parent rather than separate product inventory entries', () => {
        const inventory = src('components/workflowModelInventory.ts');

        assert.match(
            inventory,
            /workflowId: 'protein_modification_experimental'[\s\S]*label: 'De Novo Design'[\s\S]*modelTopics: \['rfdiffusion', 'laproteina', 'disco', 'fampnn', 'proteinmpnn', 'boltz2'\]/,
        );
        assert.equal(inventory.includes("workflowId: 'protein_local_redesign'"), false);
        assert.equal(inventory.includes("workflowId: 'protein_cad_experimental'"), false);
    });
});
