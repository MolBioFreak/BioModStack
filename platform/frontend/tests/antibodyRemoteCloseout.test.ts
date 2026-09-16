import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { prepareJobSubmission } from '../src/lib/api';
import { initialExecutionPolicy, setDraftExecutionPolicy } from '../src/lib/executionPolicy';

// Execute the component's actual closures, not a duplicate request builder.
// Network/upload/rendering are deliberately outside this offline boundary.
const source = fs.readFileSync(new URL('../src/components/AntibodyDenovoTemplate.tsx', import.meta.url), 'utf8');
const ast = ts.createSourceFile('AntibodyDenovoTemplate.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const declarations = new Map<string, ts.VariableDeclaration>();
function collect(node: ts.Node) {
    if (ts.isVariableDeclaration(node)) declarations.set(node.name.getText(ast), node);
    ts.forEachChild(node, collect);
}
collect(ast);
function closure(name: string, context: Record<string, unknown>) {
    const initializer = declarations.get(name)?.initializer;
    assert.ok(initializer, name);
    const script = ts.transpileModule(`const fn = ${initializer.getText(ast)}; fn;`, {
        compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
    }).outputText;
    return vm.runInNewContext(script, {...context});
}
const plain = (value: unknown) => JSON.parse(JSON.stringify(value));
const context = {
    jobName: 'native antibody consumer', pinnedGpus: [2], lockGpus: true,
    selectedChain: 'T', selectedTargetModel: 2, interactiveWorkflow: false,
    customOutputDir: '', customFrameworkPath: null, serializeSabdabFramework: () => undefined,
    numDesigns: 17, boltzgenBatchSize: 3, boltzgenScaffoldLength: '95-110',
    boltzgenCdrH1Length: '6-8', boltzgenCdrH2Length: '7-9', boltzgenCdrH3Length: '12-17',
    boltzgenUseFrameworkTemplate: true, boltzgenScaffoldSource: 'sequence_template',
    boltzgenNanobodyFramework: 'QVQLV', boltzgenCheckpointMode: 'adherence',
    boltzgenSkipInverseFolding: false, boltzgenInverseFoldNumSequences: 3,
    buildBoltzgenInverseFoldAvoid: () => 'C', boltzgenAvoidCysteine: true,
    boltzgenStepScale: 1.31, boltzgenNoiseScale: 0.83, boltzgenBudget: 12,
    boltzgenAlpha: 0.13, boltzgenMaxRmsd: 3.2, boltzgenMinPlddt: '',
    boltzgenMinConfScore: 0.63, boltzgenFilterBiased: false, boltzgenMetricsOverride: '',
    boltzgenAdditionalFilters: '', boltzgenSizeBuckets: '', boltzgenParallelMode: false,
    boltzgenDesignsPerJob: 5, boltzgenReuseExisting: false,
    ppiflowSeedAntibodyChains: 'H', ppiflowSeedAntigenChains: 'T',
    effectivePpiFlowBackboneLoopScope: 'H1,H3', selectedLoopList: ['H1','H3'],
    ppiflowBackboneRegionMode: 'selected_cdrs', resolvedPpiFlowCheckpoint: 'seeded.ckpt',
    structureValidator: 'boltz2', qualitySettings: {
        ppiflow_samples_per_target: 7, ppiflow_start_t: 0.61, ppiflow_retry_limit: 3,
        ppiflow_config: '', ppiflow_weights_dir: '', ppiflow_checkpoint_path: '',
        ppiflow_require_anchors: false, ppiflow_rotamer_enrichment_enabled: true,
        ppiflow_rotamer_shell_cutoff: 7.5, ppiflow_objective_mode: 'loop_epitope',
        ppiflow_objective_threshold: -0.25,
    },
};
const storage = new Map<string,string>();
Object.assign(globalThis, {window: {
    location: {search: ''}, dispatchEvent: () => true,
    localStorage: { getItem: (key: string) => storage.get(key) ?? null,
                    setItem: (key: string,value: string) => storage.set(key,value),
                    removeItem: (key: string) => storage.delete(key) },
}});
const emitted: unknown[] = [];
for (const [generator, mode, builder, paramsBuilder] of [
    ['boltzgen','nanobody_binder','buildBoltzgenWorkflowRequest','buildStandaloneBoltzgenParams'],
    ['ppiflow','generator_backbone_refine','buildPpiFlowWorkflowRequest','buildStandalonePpiFlowGeneratorParams'],
]) {
    test(`${generator}: actual component request, typed submission, and clone policy preserve settings`, () => {
        const buildParams = closure(paramsBuilder, context);
        const build = closure(builder, {...context, [paramsBuilder]: buildParams});
        const args = ['/fixture/target.pdb', 'T12,T15', {seedComplexPath: '/fixture/seed.pdb'}];
        const nativeParams = plain(buildParams(...args));
        const request = plain(build(...args));
        assert.equal(request.model_id, 'antibody_denovo');
        assert.equal(request.mode, mode);
        assert.deepEqual(request.params, nativeParams);
        assert.equal(request.pinned_gpu, 2);
        if (generator === 'boltzgen') {
            assert.equal(nativeParams.boltzgen_min_plddt, null);
            for (const value of [0, '']) {
                const optional = closure(paramsBuilder, {...context, boltzgenStepScale:value,
                    boltzgenNoiseScale:value, boltzgenBudget:value, boltzgenMaxRmsd:value,
                    boltzgenMinConfScore:value});
                const params = plain(optional(...args));
                for (const key of ['step_scale','noise_scale','budget','max_rmsd','min_conf_score']) {
                    assert.equal(params['boltzgen_'+key], value === '' ? null : value);
                }
            }
            // Explicit incompatible saved settings remain visible for API rejection.
            const unsupported = closure(paramsBuilder, {...context, boltzgenMinPlddt:70});
            assert.equal(plain(unsupported(...args)).boltzgen_min_plddt, 70);
        }
        // The existing clone generator initializer must reopen the same engine.
        const init = declarations.get('[deNovoGenerator, setDeNovoGenerator]')!.initializer! as ts.CallExpression;
        const initializer = ts.transpileModule(`const f = ${init.arguments[0].getText(ast)}; f();`, {
            compilerOptions: {target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None},
        }).outputText;
        assert.equal(vm.runInNewContext(initializer, {initialValues: request.params}), generator);
        for (const target of [null, 'worker-one']) {
            for (const policy of ['manual','automatic'] as const) {
                const saved = {...request, execution_target_id: target, execution_policy: {remote_result_policy: policy}};
                storage.set('clonedJobData', JSON.stringify(saved));
                assert.deepEqual(initialExecutionPolicy(), saved.execution_policy);
                setDraftExecutionPolicy({remote_result_policy: policy === 'manual' ? 'automatic' : 'manual'});
                const submitted = prepareJobSubmission(saved, {launchContext: false});
                assert.deepEqual(plain(submitted), saved);
                emitted.push(submitted);
            }
        }
        setDraftExecutionPolicy(undefined);
        storage.delete('clonedJobData');
        const fresh = prepareJobSubmission(request, {launchContext: false});
        assert.equal(fresh.execution_policy?.remote_result_policy, 'manual');
        assert.deepEqual(plain(fresh.params), nativeParams);
        if (process.env.BMS_ANTIBODY_REQUEST_FIXTURE) fs.writeFileSync(process.env.BMS_ANTIBODY_REQUEST_FIXTURE, JSON.stringify(emitted, null, 2));
    });
}
