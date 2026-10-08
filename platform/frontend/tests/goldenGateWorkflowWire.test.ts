import assert from 'node:assert/strict';
import {test} from 'node:test';
import fixture from './fixtures/golden-gate/wire-receiving.json';
import {expandGoldenGateWire,projectGoldenGateWire,type WorkflowWire} from '../src/lib/goldenGateWorkflowWire';

test('real native bodies expand exactly and JS projection equals native projection',()=>{
  for(const [full,wire,kind] of [[fixture.preview,fixture.normalized,'result'],[fixture.saved,fixture.saved_normalized,'saved'],[fixture.split.preview,fixture.split.normalized,'result']] as const){
    assert.deepEqual(expandGoldenGateWire(wire as WorkflowWire,kind),full);
    assert.deepEqual(projectGoldenGateWire(full,kind),wire);
    assert.deepEqual(expandGoldenGateWire(projectGoldenGateWire(full,kind),kind),full);
  }
});
test('reference-like qualifiers, compound segments and alias identities stay opaque',()=>{
  const full=expandGoldenGateWire<typeof fixture.preview>(fixture.normalized as WorkflowWire,'result');
  assert.deepEqual(full,fixture.preview);
  const materials=full.solutions[0].design.materials;
  const original=materials.find(m=>m.id==='source:insert')!;
  const alias=materials.find(m=>m.id==='source:alias')!;
  assert.notEqual(original.id,alias.id);
  assert.equal(original.sequence,alias.sequence);
  assert.equal(original.features[0].segments.length,2);
  assert.equal(original.features[0].strand,-1);
  assert.deepEqual(original.features[0].qualifiers,alias.features[0].qualifiers);
  assert.equal(Object.hasOwn(original.features[0].qualifiers,'__proto__'),true);
  assert.equal(({} as Record<string,unknown>).polluted,undefined);
  // Expansion gives independent values; editing one occurrence cannot mutate
  // another candidate/source occurrence sharing the same wire state.
  alias.features[0].name='changed';
  assert.notEqual(original.features[0].name,alias.features[0].name);
});
test('dangling, mixed, fractional and negative references cannot silently expand',()=>{
  for(const index of [-1,0.5,99999,true,'0',null]){
    const wire=structuredClone(fixture.normalized) as unknown as WorkflowWire;
    const payload=wire.payload as any;
    payload.requested.sources[0].source.sequence={sequence_ref:index};
    assert.throws(()=>expandGoldenGateWire(wire,'result'));
  }
  const wire=structuredClone(fixture.normalized) as unknown as WorkflowWire;
  (wire.payload as any).requested.sources[0].source.sequence={sequence_ref:0,extra:true};
  assert.throws(()=>expandGoldenGateWire(wire,'result'));
  assert.throws(()=>expandGoldenGateWire(fixture.normalized as WorkflowWire,'saved'));
  const material=structuredClone(fixture.normalized) as unknown as WorkflowWire;
  (material.payload as any).solutions[0].design.materials[0]={material_ref:99999};
  assert.throws(()=>expandGoldenGateWire(material,'result'));
});
