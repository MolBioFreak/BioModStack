import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, expect, it } from 'vitest';
import { MethodFields, MethodValueField } from '../../src/components/BioXpMethodFields';
import { BioXpMethodExpression } from '../../src/components/BioXpMethodExpression';
import { methodNumber } from '../../src/lib/bioxpMethodNumber';

let host: HTMLDivElement, root: Root;
afterEach(async () => { await act(async () => root?.unmount()); host?.remove(); });
async function change(label: string, value: string) {
    const el = host.querySelector<HTMLInputElement | HTMLSelectElement>(`[aria-label="${label}"]`)!;
    expect(el, label).toBeTruthy();
    await act(async () => { Object.getOwnPropertyDescriptor(el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(el, value); el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); });
}
it.each(['', '9007199254740993', '0002.34000000000000000001', '-1.2500e+10'])('retains explicit numeric kind and raw %s through render, serialization and cold reopen', async raw => {
    let saved: unknown;
    function Editor({ initial }: { initial: unknown }) { const [value, setValue] = useState(initial); saved = value; return <MethodFields label="Extension" value={value} onChange={setValue} />; }
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => root.render(<Editor initial={undefined} />));
    await change('Extension type', 'number');
    expect((host.querySelector('[aria-label="Extension type"]') as HTMLSelectElement).value).toBe('number');
    await change('Extension', raw);
    expect(saved).toEqual(methodNumber(raw));
    const readback = JSON.parse(JSON.stringify(saved));
    await act(async () => root.unmount()); root = createRoot(host);
    await act(async () => root.render(<Editor initial={readback} />));
    expect((host.querySelector('[aria-label="Extension type"]') as HTMLSelectElement).value).toBe('number');
    expect((host.querySelector('[aria-label="Extension"]') as HTMLInputElement).value).toBe(raw);
    await change('Extension type', 'null'); expect(saved).toBeNull();
    await change('Extension type', 'omitted'); expect(saved).toBeUndefined();
});
it('never infers numeric kinds from unknown strings, nulls or objects and only edits a legacy number on explicit input', async () => {
    let current: unknown = { raw: '9007199254740993', blank: '', nil: null, old: 12, future: { expr: { version: 1, op: 'literal', type: 'number', value: '1', future: true } } };
    function Editor() { const [value, setValue] = useState(current); current = value; return <MethodValueField label="Unknown" value={value} onChange={setValue} />; }
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => root.render(<Editor />));
    expect((host.querySelector('[aria-label="Unknown.raw type"]') as HTMLSelectElement).value).toBe('string');
    expect((host.querySelector('[aria-label="Unknown.future type"]') as HTMLSelectElement).value).toBe('object');
    expect((current as any).old).toBe(12);
    await change('Unknown.old', '12.000');
    expect(current).toMatchObject({ raw: '9007199254740993', blank: '', nil: null, old: methodNumber('12.000'), future: { expr: { future: true } } });
});
it('retains an explicitly selected empty object variant and infers its declared fields after cold reopen', async () => {
    const schema = { oneOf: [
        { type: 'object', additionalProperties: false, properties: { speed: { type: 'number' } } },
        { type: 'object', additionalProperties: false, properties: { liquid: { type: 'object', properties: { volume: { type: 'number' } } } } },
    ] };
    let saved: unknown = {};
    function Editor({ initial }: { initial: unknown }) { const [value, setValue] = useState(initial); saved = value; return <MethodFields label="Transfer" schema={schema} value={value} onChange={setValue} />; }
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => root.render(<Editor initial={saved} />));
    await change('Transfer variant', '1');
    expect((host.querySelector('[aria-label="Transfer variant"]') as HTMLSelectElement).value).toBe('1');
    expect(saved).toEqual({});
    await change('Transfer.liquid presence', 'value'); await change('Transfer.liquid.volume presence', 'value');
    await change('Transfer.liquid.volume', '020.000');
    const readback = JSON.parse(JSON.stringify(saved));
    await act(async () => root.unmount()); root = createRoot(host);
    await act(async () => root.render(<Editor initial={readback} />));
    expect((host.querySelector('[aria-label="Transfer variant"]') as HTMLSelectElement).value).toBe('1');
    expect((host.querySelector('[aria-label="Transfer.liquid.volume"]') as HTMLInputElement).value).toBe('020.000');
    expect(saved).toEqual({ liquid: { volume: '020.000' } });
});
it('edits numeric expression literals without nesting raw envelopes, and replaces operator-specific fields only on explicit selection', async () => {
    let current: any = { expr: { version: 1, op: 'literal', value: '', future: null } };
    function Editor() { const [value, setValue] = useState(current); current = value; return <BioXpMethodExpression label="Formula" value={value} onChange={setValue} literal={(v, change) => <MethodValueField label="Value" value={v} onChange={change} />} />; }
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => root.render(<Editor />));
    await change('Value type', 'number'); await change('Value', '9007199254740993.000');
    expect(current).toEqual({ expr: { version: 1, op: 'literal', type: 'number', value: '9007199254740993.000', future: null } });
    await change('Formula unit', 's'); await change('Formula unit', '');
    expect(Object.hasOwn(current.expr, 'unit')).toBe(false);
    await change('Formula operator', 'param'); await change('Formula reference', 'duration');
    expect(current).toEqual({ expr: { version: 1, op: 'param', id: 'duration', future: null } });
    await change('Formula operator', 'literal');
    expect(current).toEqual({ expr: { version: 1, op: 'literal', future: null } });
});
