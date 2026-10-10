import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { webcrypto, createHash } from 'node:crypto';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { BioXpCalibrationSettings } from '../../src/components/BioXpCalibrationSettings';
import { BioXpPipetteSettings } from '../../src/components/BioXpPipetteSettings';
import { BioXpWellPipettingPanel } from '../../src/components/BioXpWellPipettingPanel';
import { BioXpSchemaInput } from '../../src/components/BioXpSchemaInput';
import { BioXpMeasurementReview } from '../../src/components/BioXpMeasurementReview';
import { BioXpPipetteResults } from '../../src/components/BioXpPipetteResults';
import fixture from '../../../api/tests/fixtures/bioxp_calibration_settings.json';

let host: HTMLDivElement, root: Root, client: QueryClient;
const original = api.defaults.adapter;
const calls: {method: string; url: string; body: any}[] = [];
async function tick() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 25)); }); }
async function mount(node: React.ReactNode) { await act(async () => root.render(<QueryClientProvider client={client}>{node}</QueryClientProvider>)); await tick(); }
async function click(name: string) { const el = [...host.querySelectorAll('button')].find(button => button.textContent === name); expect(el, name).toBeDefined(); await act(async () => el!.click()); await tick(); }
async function change(label: string, value: string) { const el = host.querySelector(`[aria-label="${label}"]`) as HTMLInputElement; expect(el).not.toBeNull(); await act(async () => { Object.getOwnPropertyDescriptor(el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(el, value); el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); }); }
beforeEach(() => { vi.stubGlobal('crypto', webcrypto); host = document.createElement('div'); document.body.append(host); root = createRoot(host); client = new QueryClient({defaultOptions: {queries: {retry: false, gcTime: Infinity}}}); calls.length = 0; });
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); api.defaults.adapter = original; vi.unstubAllGlobals(); });

it('shares generation observations, refreshes the sibling, preserves dirty exact drafts, and retains failed read observations', async () => {
    let settings = structuredClone(fixture.before), fail = false;
    api.defaults.adapter = async config => {
        calls.push({method: config.method!, url: config.url!, body: config.data ? JSON.parse(config.data) : config.params});
        if (config.url!.includes('manual-tip-set')) { settings = structuredClone(fixture.after); return {data: {measured_z_steps: 0, committed_revision_id: settings.saved_revision_id}, status: 200, statusText: 'inert', headers: {}, config}; }
        if (fail) throw new Error('observation offline');
        return {data: config.url!.includes('operation-parameters') ? {runtime_values: {LogPressure: false, CheckForStaticTipLoss: true}} : settings, status: 200, statusText: 'inert', headers: {}, config};
    };
    const nodes = (visible: boolean, generation = 7) => <><BioXpPipetteSettings generation={generation} connected visible={visible}/><BioXpCalibrationSettings generation={generation} connected visible={visible}/></>;
    await mount(nodes(false)); expect(calls).toHaveLength(0); expect(host.textContent).toContain('not read');
    await mount(nodes(true)); expect(calls.filter(c => c.url.endsWith('/calibration-settings'))).toHaveLength(1);
    await change('Saved x', '-017');
    await click('Set current Z for selected tray pair');
    expect((host.querySelector('[aria-label="Saved x"]') as HTMLInputElement).value).toBe('-017');
    expect(host.textContent).toContain(settings.saved_revision_id!);
    await mount(nodes(false)); const count = calls.length; await tick(); expect(calls).toHaveLength(count);
    await mount(nodes(true)); expect((host.querySelector('[aria-label="Saved x"]') as HTMLInputElement).value).toBe('-017');
    fail = true; await click('Read saved and active settings'); expect(host.textContent).toContain('read error — last observation retained'); expect(host.textContent).toContain(settings.saved_revision_id!);
    fail = false; await mount(nodes(true, 8)); expect(calls.at(-1)!.body.expected_connection_generation).toBe(8); expect((host.querySelector('[aria-label="Saved x"]') as HTMLInputElement).value).not.toBe('-017');
});

it('removes the Pipettes sequence editor, retains drafts across categories and emits physical tray pickup rather than host assignment', async () => {
    let job: any;
    api.defaults.adapter = async config => {
        if (config.method === 'get') return {data: job ?? {}, status: 200, statusText: 'inert', headers: {}, config};
        const body = JSON.parse(config.data); calls.push({method: config.method!, url: config.url!, body});
        const id = `protocol-live-${createHash('sha256').update(body.idempotency_key).digest('hex')}`;
        job = {job_id: id, status: 'completed', command: {command_id: id, idempotency_key: body.idempotency_key, terminal: true, status: 'completed'}, execution: {dry_run: false, runtime_state: {workflow: {phase: 'terminal'}, action_results: []}}};
        return {data: job, status: 200, statusText: 'inert', headers: {}, config};
    };
    await mount(<BioXpWellPipettingPanel generation={7} connected/>);
    expect(host.textContent).not.toContain('Ordered well-to-well program'); expect(host.textContent).not.toContain('Append step');
    await change('Volume (µL)', '12.50'); await click('Tips'); await change('Tip tray', '3'); await change('Tip well', 'B4'); await click('Pick up from tray');
    expect(calls).toHaveLength(1); expect(calls[0].url).toBe('/api/bioxp/protocols/submit'); expect(calls[0].body.document.stages[0].actions[0].params).toEqual({operation: 'load_tip', tray: 3, well: 'B4', overpress: false, lift_z: false});
    await click('Liquid strokes'); expect((host.querySelector('[aria-label="Volume (µL)"]') as HTMLInputElement).value).toBe('12.50');
    await click('Diagnostics'); expect(host.querySelectorAll('[aria-label="Diagnostic action"] option')).toHaveLength(11); expect(calls).toHaveLength(1);
});

it('keeps an in-flight failure outside hidden forms when changing test category', async () => {
    let reject!: (error: Error) => void;
    api.defaults.adapter = async config => { if (config.method === 'get') throw new Error('not found'); return new Promise((_resolve, fail) => { reject = fail; }); };
    await mount(<BioXpWellPipettingPanel generation={7} connected/>); await click('Tips'); await click('Pick up from tray'); await click('Liquid strokes');
    const pending = [...host.querySelectorAll('[role="status"]')].find(n => n.textContent!.includes('Submitting')); expect(pending?.closest('[hidden], details')).toBeNull();
    await act(async () => reject(new Error('pickup transport failure'))); await tick();
    const failure = [...host.querySelectorAll('[role="alert"]')].find(n => n.textContent!.includes('pickup transport failure')); expect(failure).toBeDefined(); expect(failure?.closest('[hidden], details')).toBeNull();
});

it('uses documentary source values only after explicit selection and preserves existing null/false/zero', async () => {
    let value: any = {keepFalse:false,keepZero:0,keepNull:null};
    await mount(<BioXpSchemaInput rawDraft label="Source" schema={{type:'object',additionalProperties:false,properties:{prefill:{type:'boolean',source_default:true},keepFalse:{type:'boolean',source_default:true},keepZero:{type:'number',source_default:5},keepNull:{type:'null',source_default:7}}}} value={value} onChange={v=>{value=v;}} fallback={()=>null}/>);
    expect(value).toEqual({keepFalse:false,keepZero:0,keepNull:null});
    expect(host.querySelectorAll('[aria-label^="Use source value for"]')).toHaveLength(1);
    await click('Use source value: true');
    expect(value).toEqual({keepFalse:false,keepZero:0,keepNull:null,prefill:true});
    expect(calls).toHaveLength(0);
});

it.each([false, true])('renders standalone native report outside disclosures, retaining partial=%s and unknown indices', async partial => {
    // Native standalone query shape, distinct from diagnostic/program `reports`.
    const report = { parsed: true, raw_ascii: '1.25', raw_bytes: [32, 96, 49, 46, 50, 53], report_kind: 'pressure', units: 'psi', value: 1.25 };
    const outcome = { channels: [{ channel: 0, result: { report, completion_verified: false, controller_acknowledged: false, physical_effect_verified: false } }] };
    const event = { operation: 'device_command', status: partial ? 'failed' : 'completed', ...(partial ? { partial: { pipette_partial_result: outcome } } : { result: outcome }) };
    await mount(<BioXpPipetteResults value={{ pipette_result: { kind: 'cavro_application', events: [event] } }} />);
    const panel = host.querySelector('[aria-label="Device command reports"]')!;
    const table = panel.querySelector('table')!;
    expect(table.closest('details')).toBeNull();
    expect([...table.querySelectorAll('tbody td')].map(cell => cell.textContent)).toEqual(['Unknown', 'Unknown', 'pressure', '1.25', 'psi', 'true', 'Unknown', 'Unknown', 'Unknown']);
    const term = [...panel.querySelectorAll('dt')].find(node => node.textContent === 'Completion verified')!;
    expect(term.nextElementSibling?.textContent).toBe('No');
    expect(calls).toHaveLength(0);
});

it('external measurement fields retain exact decimals and zero independently of commanded/controller values', async () => {
    let values: Record<string, string[]> = {};
    const render = () => <BioXpMeasurementReview form={{fields: [{field_id:'probe', label:'Probe', unit:'degC', module:'tc', zone:'lid', repetitions:2, target:'37.0', commanded:'37.1', controller_reported:'36.9'}]}} values={values} onChange={v => { values=v; }}/>;
    await mount(render()); await change('Probe reading 1 (degC)', '36.9000'); await mount(render()); await change('Probe reading 2 (degC)', '0'); await tick();
    expect(values).toEqual({probe:['36.9000','0']}); expect(host.textContent).toContain('Target: 37.0'); expect(host.textContent).toContain('Controller reported: 36.9'); expect(calls).toHaveLength(0);
});
