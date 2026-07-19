import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { describe, it } from 'node:test';

const frontendRoot = process.cwd();
const repoRoot = path.resolve(frontendRoot, '../..');
const src = (relativePath: string) => fs.readFileSync(path.join(frontendRoot, 'src', relativePath), 'utf8');


describe('workflow-only capability boundaries', () => {
    it('names the ConforNets-backed product Conformational Mapping', () => {
        const inventory = src('components/workflowModelInventory.ts');
        assert.equal(inventory.includes("label: 'Conformational Mapping'"), true);
        assert.equal(inventory.includes('Conformational Mapping Experimental'), false);
    });

    it('does not ship standalone launch surfaces', () => {
        for (const relativePath of [
            'workflows/esmfold2_experimental.nf',
            'workflows/boltzgen_design.nf',
            'platform/api/config/templates/esmfold2.yaml',
            'platform/api/config/templates/esmfold2_experimental.yaml',
            'platform/api/config/templates/boltzgen_ligand.yaml',
            'platform/frontend/src/components/BoltzGenTemplate.tsx',
        ]) {
            assert.equal(fs.existsSync(path.join(repoRoot, relativePath)), false, relativePath);
        }
    });

    it('does not render dedicated ESMFold2 or BoltzGen launchers', () => {
        const jobSubmission = src('components/JobSubmission.tsx');
        assert.equal(jobSubmission.includes('import BoltzGenTemplate'), false);
        assert.equal(jobSubmission.includes("selectedTemplateId === 'esmfold2'"), false);
        assert.equal(jobSubmission.includes("selectedTemplateId === 'esmfold2_experimental'"), false);
        assert.equal(jobSubmission.includes("selectedTemplateId === 'boltzgen_design'"), false);
        assert.equal(jobSubmission.includes("id: 'boltzgen_design'"), false);
    });

    it('routes historical compatibility IDs into enclosing workflows', () => {
        const jobSubmission = src('components/JobSubmission.tsx');
        assert.equal(jobSubmission.includes("esmfold2: 'structure_prediction'"), true);
        assert.equal(jobSubmission.includes("esmfold2_experimental: 'structure_prediction'"), true);
        assert.equal(jobSubmission.includes("boltzgen: 'antibody_denovo'"), true);
        assert.equal(jobSubmission.includes("pred_method: 'esmfold2'"), true);
        assert.equal(jobSubmission.includes("denovo_generator: 'boltzgen'"), true);
    });

    it('exposes ESMFold2 through structure prediction and mutagenesis selectors', () => {
        const structureState = src('components/structurePredictionUiState.ts');
        const structureTemplate = src('components/StructurePredictionTemplate.tsx');
        const mutagenesisTemplate = src('components/MutagenesisTemplate.tsx');
        const jobSubmission = src('components/JobSubmission.tsx');

        assert.equal(structureState.includes("export type StructurePredictorFamily = 'boltz' | 'rf3' | 'protenix' | 'esmfold2'"), true);
        assert.equal(structureState.includes("StructureLaunchVariant = 'default' | 'boltz_cp_experimental' | 'esmfold2'"), false);
        assert.equal(structureTemplate.includes("predictorFamilies.includes('esmfold2')"), true);
        assert.equal(mutagenesisTemplate.includes("setPredictor('esmfold2')"), true);
        assert.equal(jobSubmission.includes("? 'esmfold2'"), true);
    });

    it('keeps BoltzGen and ESMFold2 documented on their parent workflows', () => {
        const inventory = src('components/workflowModelInventory.ts');
        assert.equal(inventory.includes("id: 'boltzgen_design'"), false);
        assert.equal(inventory.includes("id: 'boltzgen_ligand'"), false);
        assert.equal(inventory.includes("id: 'esmfold2'"), false);
        assert.match(inventory, /workflowId: 'antibody_denovo'[\s\S]*modelTopics: \[[^\]]*'boltzgen'[^\]]*'esmfold2'/);
        assert.equal(inventory.includes("modelTopics: ['boltz2', 'rf3', 'protenix', 'esmfold2']"), true);
    });
});
