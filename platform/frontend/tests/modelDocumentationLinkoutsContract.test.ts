import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';

import {
  MODEL_DOCUMENTATION_LINKS,
  getModelDocumentationLinks,
  type ModelDocumentationTopic,
} from '../src/components/modelDocumentationRegistry.js';
import {
  UNIQUE_WORKFLOW_MODEL_TOPICS,
  WORKFLOW_MODEL_INVENTORY,
  getUniqueWorkflowModelInventory,
  getWorkflowModelTopics,
} from '../src/components/workflowModelInventory.js';

const readSource = (...parts: string[]) => readFileSync(join(process.cwd(), ...parts), 'utf8');

const requireSnippet = (source: string, snippet: string) => {
  assert.ok(source.includes(snippet), `missing source snippet: ${snippet}`);
};

const rejectSnippet = (source: string, snippet: string) => {
  assert.ok(!source.includes(snippet), `stale source snippet still present: ${snippet}`);
};

const sourceBlock = (source: string, startNeedle: string, endNeedle: string): string => {
  const start = source.indexOf(startNeedle);
  assert.notEqual(start, -1, `missing source block start: ${startNeedle}`);
  const end = source.indexOf(endNeedle, start);
  assert.notEqual(end, -1, `missing source block end: ${endNeedle}`);
  return source.slice(start, end);
};

const expectedUniqueTopics: ModelDocumentationTopic[] = [
  'alphafold2',
  'boltz2',
  'boltzgen',
  'caliby',
  'chai1',
  'confornets',

  'disco',
  'esmfold2',
  'fampnn',
  'fold_cp',
  'laproteina',
  'ligandmpnn',
  'ppiflow',
  'protein_hunter',
  'proteinmpnn',
  'protenix',
  'rf3',
  'rfantibody',
  'rfdiffusion',
  'rfdpoly',

];

test('model documentation linkout registry exposes compact shared DOI/GitHub/preprint contracts', () => {
  const componentSource = readSource('src', 'components', 'ModelDocumentationLinks.tsx');
  const registrySource = readSource('src', 'components', 'modelDocumentationRegistry.ts');

  requireSnippet(componentSource, 'data-bms-model-doc-linkouts="true"');
  requireSnippet(componentSource, 'data-doc-topics={topics.join');
  requireSnippet(componentSource, 'target="_blank"');
  requireSnippet(componentSource, 'rel="noreferrer"');

  requireSnippet(registrySource, 'export type ModelDocumentationTopic =');
  requireSnippet(registrySource, 'export const MODEL_DOCUMENTATION_LINKS');
  requireSnippet(registrySource, 'export const getModelDocumentationLinks');
  requireSnippet(registrySource, 'if (seen.has(link.href)) return;');

  for (const topic of expectedUniqueTopics) {
    assert.ok(MODEL_DOCUMENTATION_LINKS[topic]?.length > 0, `${topic} should have at least one linkout`);
  }

  const requiredLinkouts = [
    'https://docs.boltz.bio/',
    'https://doi.org/10.1101/2025.06.14.659707',
    'https://github.com/RosettaCommons/RFantibody',
    'https://doi.org/10.1101/2024.03.14.585103',
    'https://github.com/HannesStark/boltzgen',
    'https://github.com/MolBioFreak/BioModStack/blob/main/docs/plans/2026-04-24-fold-cp-dram-context-spill-additional-work-spec.md',
    'https://github.com/aqlaboratory/confornets',
    'https://arxiv.org/abs/2604.18559',
    'https://github.com/richardshuai/fampnn',
    'https://doi.org/10.1101/2025.02.13.637498',
    'https://github.com/dauparas/ProteinMPNN',
    'https://doi.org/10.1126/science.add2187',
    'https://github.com/dauparas/LigandMPNN',
    'https://doi.org/10.1038/s41592-025-02626-1',
    'https://github.com/bytedance/Protenix',
    'https://doi.org/10.1101/2025.01.08.631967',
    'https://github.com/gcorso/DiffDock',
    'https://arxiv.org/abs/2210.01776',
    'https://github.com/dptech-corp/Uni-Dock',
    'https://doi.org/10.1021/acs.jctc.2c01145',
    'https://github.com/RosettaCommons/RFDpoly',
    'https://doi.org/10.1101/2025.10.01.679929',
    'https://github.com/NVIDIA-Digital-Bio/la-proteina',
    'https://arxiv.org/abs/2507.09466',
    'https://github.com/DISCO-design/DISCO',
    'https://arxiv.org/abs/2604.05181',
    'https://github.com/Biohub/esm',
    'https://huggingface.co/biohub/ESMFold2-Fast',
    'https://biohub.ai/papers/esm_protein.pdf',
    'https://github.com/chaidiscovery/chai-lab',
    'https://doi.org/10.1101/2024.10.10.615955',
    'https://doi.org/10.1101/2025.09.30.679633',
    'https://doi.org/10.1101/2025.10.10.681530',
    'https://doi.org/10.1101/2025.11.28.691195',
  ];
  const allHrefs = new Set<string>(Object.values(MODEL_DOCUMENTATION_LINKS).flat().map((link) => link.href));
  for (const href of requiredLinkouts) {
    assert.ok(allHrefs.has(href), `missing required model linkout: ${href}`);
  }

  const deduped = getModelDocumentationLinks(['boltz2', 'fold_cp', 'boltz2']);
  assert.equal(deduped.filter((link) => link.href === 'https://docs.boltz.bio/').length, 1);
});

test('workflow model inventory is source-grounded and exposes the total unique model list', () => {
  const inventorySource = readSource('src', 'components', 'workflowModelInventory.ts');

  requireSnippet(inventorySource, 'WORKFLOW_MODEL_INVENTORY');
  requireSnippet(inventorySource, 'UNIQUE_WORKFLOW_MODEL_TOPICS');
  requireSnippet(inventorySource, 'platform/api/config/templates/structure_prediction.yaml');
  requireSnippet(inventorySource, 'platform/frontend/src/components/JobSubmission.tsx');
  requireSnippet(inventorySource, 'main.nf');
  requireSnippet(inventorySource, 'nextflow.config');

  assert.deepEqual(UNIQUE_WORKFLOW_MODEL_TOPICS, expectedUniqueTopics);
  assert.deepEqual(getUniqueWorkflowModelInventory().map((entry) => entry.topic), expectedUniqueTopics);
  for (const entry of getUniqueWorkflowModelInventory()) {
    assert.ok(entry.links.length > 0, `${entry.topic} should carry linkouts`);
  }

  const workflowsById = new Map(WORKFLOW_MODEL_INVENTORY.map((entry) => [entry.workflowId, entry]));
  assert.deepEqual(workflowsById.get('mutagenesis')?.modelTopics, ['boltz2', 'rf3', 'esmfold2']);
  assert.deepEqual(workflowsById.get('structure_prediction')?.modelTopics, ['boltz2', 'rf3', 'protenix', 'esmfold2']);
  assert.deepEqual(workflowsById.get('antibody_denovo')?.modelTopics, ['rfantibody', 'boltzgen', 'ppiflow', 'fampnn', 'caliby', 'proteinmpnn', 'protenix', 'boltz2', 'esmfold2']);
  assert.deepEqual(workflowsById.get('protein_local_redesign')?.modelTopics, ['rfdiffusion', 'fampnn', 'proteinmpnn', 'boltz2']);
  assert.deepEqual(workflowsById.get('protein_cad_experimental')?.modelTopics, ['laproteina', 'disco']);
  assert.deepEqual(workflowsById.get('protein_hunter_experimental')?.modelTopics, ['protein_hunter', 'boltz2', 'chai1']);
  assert.equal(workflowsById.has('esmfold2'), false);
  assert.deepEqual(getWorkflowModelTopics('esmfold2_experimental'), ['boltz2', 'rf3', 'protenix', 'esmfold2']);

  for (const workflow of WORKFLOW_MODEL_INVENTORY) {
    assert.ok(workflow.sourceFiles.length > 0, `${workflow.workflowId} should name source files`);
    assert.ok(workflow.modelTopics.length > 0, `${workflow.workflowId} should name model topics`);
  }
});

test('JobSubmission keeps workflow cards concise, hides Advanced Models, and routes method detail into linkouts', () => {
  const source = readSource('src', 'components', 'JobSubmission.tsx');

  requireSnippet(source, "import { ModelDocumentationLinks, getModelDocumentationLinks, type ModelDocumentationTopic } from './ModelDocumentationLinks';");
  requireSnippet(source, 'const compactUiCopy = (value: unknown, maxLength = 118): string => {');
  requireSnippet(source, 'compactUiCopy(param.description, 112)');
  requireSnippet(source, 'compactUiCopy(selectedMode.description, 120)');

  const modeToggle = sourceBlock(source, '{/* 2. Mode Toggle: workflow cards only; the raw model-picker tab stays hidden for now. */}', '{/* Templates Mode */}');
  requireSnippet(modeToggle, 'Workflows');
  requireSnippet(modeToggle, 'Experimental');
  rejectSnippet(modeToggle, 'Advanced (Models)');
  rejectSnippet(modeToggle, "setWizardMode('manual')");

  requireSnippet(source, 'const getTemplateDocumentationTopics = (');
  requireSnippet(source, 'launchParams?.workflow_model_topic');
  requireSnippet(source, 'template?.preset_params?.workflow_model_topic');
  requireSnippet(source, "return ['fold_cp', 'boltz2'];");
  requireSnippet(source, "return ['confornets'];");
  requireSnippet(source, "return ['esmfold2'];");
  requireSnippet(source, "return ['rfdiffusion', 'fampnn', 'proteinmpnn', 'boltz2'];");
  requireSnippet(source, "return ['rfantibody', 'boltzgen', 'ppiflow', 'fampnn', 'caliby', 'proteinmpnn', 'protenix', 'boltz2', 'esmfold2'];");
  requireSnippet(source, "return ['boltz2', 'rf3', 'protenix', 'esmfold2'];");

  requireSnippet(source, "return 'Experimental Fold-CP path for large Boltz-2 folds.';");
  requireSnippet(source, "return 'Experimental conformational mapping; ConforNets backend first.';");
  rejectSnippet(source, "return 'Standalone ESMFold2 protein/complex fold.';");

  rejectSnippet(source, "if (template.id === 'esmfold2' || template.id === 'esmfold2_experimental') return 'EF';");
  requireSnippet(source, "return 'Backbone generation and local redesign.';");
  requireSnippet(source, "return 'Structure and complex prediction validator.';");
  requireSnippet(source, 'getModelDocumentationLinks(docTopics)');
  requireSnippet(source, 'data-bms-workflow-doc-hover="true"');
  requireSnippet(source, 'data-bms-model-doc-hover="true"');
  requireSnippet(source, 'group-hover/docs:flex');
  requireSnippet(source, 'Docs ({docLinks.length})');
  rejectSnippet(source, 'data-bms-workflow-doc-table="true"');
  rejectSnippet(source, "{docsExpanded ? 'Hide docs' : `Docs (${docLinks.length})`}");
  rejectSnippet(source, 'Hide docs');
  rejectSnippet(source, 'Docs available');
  requireSnippet(source, 'const visibleTemplateParams = useMemo(() => {');
  requireSnippet(source, 'const groupedTemplateParams = useMemo(() => {');
  requireSnippet(source, 'data-bms-template-config-shell="true"');
  requireSnippet(source, "'Model Orchestration'");
  requireSnippet(source, "'Outputs'");
  requireSnippet(source, 'templateManagerParams');
  requireSnippet(source, "data.model_id === 'confornets_experimental'");
  requireSnippet(source, "setSelectedTemplateId('confornets_experimental')");
  requireSnippet(source, 'const matchedApiTemplate = apiTemplateId');
  requireSnippet(source, 'setSelectedTemplateId(apiTemplateId)');
  rejectSnippet(source, '{templateDetail.name} - Configuration');
  rejectSnippet(source, 'Stage Explanation');
  requireSnippet(source, 'data-bms-sequence-pdb-import-modal="true"');
  requireSnippet(source, "import { TargetAntigenSelector, type SelectedTarget } from './TargetAntigenSelector';");
  requireSnippet(source, "import { parsePDBFile, type Chain, type ParsedPDB } from '../utils/pdbUtils';");
  requireSnippet(source, 'Import from PDB');
  requireSnippet(source, 'handlePdbSequenceImportSelect');
  requireSnippet(source, 'applySequenceImport(chain.sequence');
  requireSnippet(source, 'updateParam(\'chain_id\', chainId)');
  requireSnippet(source, 'data-bms-sequence-pdb-import-modal="true"');
  requireSnippet(source, 'topics={getTemplateDocumentationTopics(templateDetail, params)}');
  requireSnippet(source, 'summary="Model docs update from the selected workflow model; launch controls stay here."');
  rejectSnippet(source, 'topics={getTemplateDocumentationTopics(templateDetail)}');
  rejectSnippet(source, 'summary="Docs carry method detail."');
  requireSnippet(source, 'topics={getModelDocumentationTopics(selectedModel)}');
  requireSnippet(source, 'summary="Docs linked; launch controls here."');

  rejectSnippet(source, 'Choose a preset workflow for your experiment goal:');
  rejectSnippet(source, 'Choose an active alpha workflow:');
  rejectSnippet(source, 'This template runs the following stages:');
  rejectSnippet(source, 'Method detail lives in maintained docs, not inline launcher prose.');
  rejectSnippet(source, 'Method background is linked out; this panel stays focused on launch controls.');
  rejectSnippet(source, 'Experimental workflows are isolated here on purpose');
  rejectSnippet(source, 'Frontier mode:');
  rejectSnippet(source, 'real integrations, but they are still alpha-grade systems intended for iterative frontier work');
  rejectSnippet(source, 'Launch RFantibody, BoltzGen nanobody, or seeded PPIFlow generation from one toolkit');
  rejectSnippet(source, 'Predict 3D protein, RNA, DNA, or complex structures from sequences using Boltz-2');
  rejectSnippet(source, 'Use RFdiffusion3 to locally remodel a selected region of an existing structure');
});

test('dedicated model launchers expose compact documentation linkouts instead of explainer panels', () => {
  const structureSource = readSource('src', 'components', 'StructurePredictionTemplate.tsx');
  const antibodySource = readSource('src', 'components', 'AntibodyDenovoTemplate.tsx');
  const localRedesignSource = readSource('src', 'components', 'ProteinLocalRedesignTemplate.tsx');

  requireSnippet(structureSource, "import { ModelDocumentationLinks, type ModelDocumentationTopic } from './ModelDocumentationLinks';");
  requireSnippet(structureSource, 'const structureDocumentationTopics = useMemo<ModelDocumentationTopic[]>(() => {');
  requireSnippet(structureSource, "if (usesRf3) topics.push('rf3');");
  requireSnippet(structureSource, "if (usesProtenix) topics.push('protenix');");
  requireSnippet(structureSource, 'topics={structureDocumentationTopics}');
  requireSnippet(structureSource, 'Logical topology and DTensor details are linked out; launch controls stay action-first here.');
  requireSnippet(structureSource, 'Method background is linked out; this panel only exposes runtime knobs.');
  requireSnippet(structureSource, 'Single-fold Boltz launcher with Fold-CP runtime controls below.');
  rejectSnippet(structureSource, 'This workflow stays on single-fold Boltz mode and reuses the standard structure input flow.');

  requireSnippet(antibodySource, "import { ModelDocumentationLinks } from './ModelDocumentationLinks';");
  requireSnippet(antibodySource, "topics={['rfantibody', 'boltzgen', 'ppiflow', 'fampnn', 'caliby', 'proteinmpnn', 'protenix', 'boltz2', 'esmfold2']}");
  requireSnippet(antibodySource, 'Generator and validator background is linked out; this launcher keeps controls and review gates up front.');
  requireSnippet(antibodySource, 'Generator-only first pass. Shortlist outputs');
  requireSnippet(antibodySource, 'Optional coarse contact screen before expensive downstream stages.');
  rejectSnippet(antibodySource, 'Coarse pre-FAMPNN screen for obviously bad backbones');
  rejectSnippet(antibodySource, 'Recommended coarse screen:');
  rejectSnippet(antibodySource, 'Interactive mode pauses the BoltzGen batch after generation and filtering');
  rejectSnippet(antibodySource, 'Interactive mode pauses the seeded PPIFlow batch after backbone generation and filtering');
  rejectSnippet(antibodySource, 'Protenix inference controls live in Quality Settings. Flexible co-fold remains the default');
  rejectSnippet(antibodySource, 'Seeded refinement preserves user-imposed residues during downstream redesign');

  requireSnippet(localRedesignSource, "import { ModelDocumentationLinks } from './ModelDocumentationLinks';");
  requireSnippet(localRedesignSource, "topics={['rfdiffusion', 'fampnn', 'proteinmpnn', 'boltz2']}");
  requireSnippet(localRedesignSource, 'Visual region pick → local remodeling → sequence redesign → optional validator.');
  requireSnippet(localRedesignSource, 'Real structure-driven loop; inspect outputs before reuse.');
  requireSnippet(localRedesignSource, 'Method background and upstream references are linked out; the launcher stays focused on source, region, and validation controls.');
  rejectSnippet(localRedesignSource, 'Choose an existing complex visually');
  rejectSnippet(localRedesignSource, 'make constrained local protein editing a first-class BMS workflow');
  rejectSnippet(localRedesignSource, 'Current status: early alpha');
});
