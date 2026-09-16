import { expect, it } from 'vitest';
import { bioXpErrorPresentation } from '../../src/lib/bioxpClient';

it('bounds validation arrays and location arrays without visiting their tails', () => {
    const detail = Array.from({ length: 100_000 }, () => ({ loc: ['body', 'target'], msg: 'invalid' }));
    Object.defineProperty(detail, 100, { get: () => { throw new Error('visited detail tail'); } });
    const error = { response: { status: 422, data: { detail } } };
    const result = bioXpErrorPresentation(error);
    expect(result.summary).toContain('body.target: invalid');
    expect(result.summary.length).toBeLessThanOrEqual(2048);
    expect(error.response.data.detail).toBe(detail);
    const loc = Array(100_000).fill('body');
    Object.defineProperty(loc, 100, { get: () => { throw new Error('visited location tail'); } });
    expect(() => bioXpErrorPresentation({ response: { data: { detail: [{ loc, msg: 'invalid' }] } } })).not.toThrow();
});

it('previews only budgeted known fields without enumerating or serializing the full response', () => {
    const body = { detail: 'refused', evidence: 'x'.repeat(1_000_000), toJSON: () => { throw new Error('must not call toJSON'); } };
    const data = new Proxy(body, { ownKeys: () => { throw new Error('must not enumerate body'); } });
    const result = bioXpErrorPresentation({ response: { data } });
    expect(result.rawJson).toContain('refused');
    expect(result.rawJson).toContain('preview');
    expect(result.rawJson.length).toBeLessThanOrEqual(8192);
    expect(body.evidence.length).toBe(1_000_000);
});

it('bounds deep and cyclic error graphs and huge fallback strings while retaining the source', () => {
    const data: Record<string, unknown> = { message: 'cycle refusal' };
    data.detail = data;
    let deep: Record<string, unknown> = data;
    for (let i = 0; i < 10_000; i++) deep = { detail: deep };
    const cycle = bioXpErrorPresentation({ response: { data } });
    expect(cycle.summary).toContain('cycle refusal');
    expect(cycle.rawJson).toContain('[circular]');
    expect(data.detail).toBe(data);
    expect(() => bioXpErrorPresentation({ response: { data: deep } })).not.toThrow();
    expect(bioXpErrorPresentation('x'.repeat(1_000_000)).summary.length).toBeLessThanOrEqual(2048);
});
