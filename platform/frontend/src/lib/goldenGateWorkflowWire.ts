// Paired with golden_gate_workflow_wire.py. This is a wire projection, not a
// scientific DTO or cache. Identity-bearing states stay separate from DNA text.
export type WorkflowWireKind = 'request' | 'result' | 'save' | 'saved' | 'portable';
type Value = null | boolean | number | string | Value[] | { [key: string]: Value };
type ObjectValue = { [key: string]: Value };
export interface WorkflowWire {
  schema_version: 'bms.golden-gate-wire.v1';
  kind: WorkflowWireKind;
  payload: ObjectValue;
  sequences: string[];
  materials: ObjectValue[];
}
const tag = 'bms.golden-gate-wire.v1';
const object = (v: Value): ObjectValue => {
  if (!v || typeof v !== 'object' || Array.isArray(v)) throw new Error('Malformed Golden Gate wire object');
  return v;
};
const array = (v: Value | undefined): Value[] => {
  if (v === undefined) return [];
  if (!Array.isArray(v)) throw new Error('Malformed Golden Gate wire array');
  return v;
};
function visit(payload: ObjectValue, kind: WorkflowWireKind, dna: (v: Value) => Value, material: (v: Value) => Value) {
  function fields(obj: ObjectValue, ...names: string[]) {
    for (const name of names) if (Object.hasOwn(obj, name) && obj[name] !== null) obj[name] = dna(obj[name]);
  }
  function source(value: Value) {
    const s = object(object(value).source);
    if (s.kind === 'inline') fields(s, 'sequence');
  }
  function edits(value: Value | undefined) {
    for (const e of array(value)) fields(object(e), 'accepted_sequence');
  }
  function request(obj: ObjectValue) {
    for (const s of array(obj.sources)) source(s);
    if (obj.task === 'split_target') source(obj.target);
    else if (obj.target !== undefined) fields(object(obj.target), 'exact_sequence');
    edits(obj.domestication);
  }
  function evidence(value: Value | undefined) {
    for (const e of array(value)) {
      const edit = object(e);
      fields(object(edit.original), 'sequence');
      fields(object(edit.proposal), 'original_sequence', 'proposed_sequence');
    }
  }
  function result(obj: ObjectValue) {
    request(object(obj.requested));
    evidence(obj.edits);
    for (const c of array(obj.solutions)) {
      const candidate = object(c), design = object(candidate.design);
      request(object(candidate.fixed_request));
      design.materials = array(design.materials).map(material);
      for (const p of array(design.solutions)) fields(object(p), 'sequence');
      for (const d of array(design.digests)) for (const f of array(object(d).fragments)) fields(object(f), 'top_strand_sequence');
      for (const p of array(design.primers)) fields(object(p), 'full_sequence', 'annealing_sequence');
    }
  }
  function selection(obj: ObjectValue) {
    request(object(obj.request));
    if (obj.authored_request != null) request(object(obj.authored_request));
    for (const s of array(obj.original_sources)) source(s);
    edits(obj.accepted_edits);
    evidence(obj.edit_evidence);
  }
  switch (kind) {
    case 'request': request(payload); break;
    case 'result': result(payload); break;
    case 'save': selection(object(payload.selection)); break;
    case 'saved': selection(object(payload.selection)); result(object(payload.result)); break;
    case 'portable': result(object(payload.result)); break;
    default: throw new Error('Unknown Golden Gate wire kind');
  }
}
// Sorting emits JSON directly, never assigning untrusted keys to a prototype.
function canonical(value: Value): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${canonical(value[k])}`).join(',')}}`;
  return JSON.stringify(value);
}
export function projectGoldenGateWire(value: unknown, kind: WorkflowWireKind): WorkflowWire {
  // Match actual JSON transport's omission of optional undefined fields.
  const payload = object(JSON.parse(JSON.stringify(value)) as Value);
  const sequences: string[] = [], materials: ObjectValue[] = [];
  const sequenceIds = new Map<string, number>(), materialIds = new Map<string, number>();
  function dna(v: Value): Value {
    if (typeof v !== 'string') throw new Error('Expected native Golden Gate DNA string');
    if (!sequenceIds.has(v)) { sequenceIds.set(v, sequences.length); sequences.push(v); }
    return { sequence_ref: sequenceIds.get(v)! };
  }
  function material(v: Value): Value {
    const key = canonical(v);
    if (!materialIds.has(key)) {
      materialIds.set(key, materials.length);
      const state = object(structuredClone(v));
      state.sequence = dna(state.sequence);
      materials.push(state);
    }
    return { material_ref: materialIds.get(key)! };
  }
  visit(payload, kind, dna, material);
  return { schema_version: tag, kind, payload, sequences, materials };
}
export function expandGoldenGateWire<T>(value: T | WorkflowWire, kind: WorkflowWireKind): T {
  // Full responses remain compatible (including full fixtures and old servers).
  if (!value || typeof value !== 'object' || !('schema_version' in value) || value.schema_version !== tag) return value as T;
  const wire = value as WorkflowWire;
  if (wire.kind !== kind || Object.keys(wire).sort().join(',') !== 'kind,materials,payload,schema_version,sequences'
      || !Array.isArray(wire.sequences) || !wire.sequences.every(s => typeof s === 'string') || !Array.isArray(wire.materials)) {
    throw new Error('Malformed Golden Gate wire envelope');
  }
  function lookup(v: Value, key: string, table: Value[]): Value {
    const ref = object(v), index = ref[key];
    if (Object.keys(ref).length !== 1 || !Object.hasOwn(ref, key) || typeof index !== 'number'
      || !Number.isSafeInteger(index) || index < 0 || index >= table.length) throw new Error(`Invalid Golden Gate ${key}`);
    return structuredClone(table[index]);
  }
  const dna = (v: Value) => lookup(v, 'sequence_ref', wire.sequences);
  function material(v: Value): Value {
    const state = object(lookup(v, 'material_ref', wire.materials));
    state.sequence = dna(state.sequence);
    return state;
  }
  const payload = object(structuredClone(wire.payload));
  visit(payload, kind, dna, material);
  return payload as T;
}
