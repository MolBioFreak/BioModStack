"""Optional, pinned BoltzGen residue transport; never a scientific admission gate.

The extra structured-array column travels with native residue selection/insertion/
concatenation. It is not a model feature. A JSON string crosses DataLoader workers;
Structure._from_feat consumes it at its actual token-selector event. Native CIF
atom emission, not sequence/ordinal matching, supplies the output author selector.
Installed files and all scientific array columns/operations remain unchanged.
"""
from __future__ import annotations

import ast
import contextvars
import functools
import hashlib
import importlib.abc
import importlib.machinery
import json
import os
from pathlib import Path
import sys

FIELD = "bms_source"
FEATURE = "bms_source_tokens"
ENV = "BMS_BOLTZGEN_CORRESPONDENCE_DIR"
MODULES = (
    "data.data", "data.parse.mmcif", "data.parse.pdb_parser", "data.parse.schema",
    "data.feature.featurizer", "task.predict.data_from_yaml",
    "task.predict.data_from_generated", "task.predict.writer", "data.write.mmcif",
)
_source = contextvars.ContextVar("boltzgen_source", default=None)
_tokens = contextvars.ContextVar("boltzgen_source_tokens", default={})
_emission = contextvars.ContextVar("boltzgen_emission", default=None)
_last_emission = contextvars.ContextVar("boltzgen_last_emission", default=())


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def selector(chain, number, insertion):
    return {"chain_id": str(chain), "auth_seq_id": int(number),
            "insertion_code": str(insertion).strip()}


def residue_array(values, dtype):
    import numpy as np
    # Existing native tuples get a null source. Copies keep the extra column.
    return np.array([tuple(v) + ("",) if len(v) == len(dtype) - 1 else tuple(v)
                     for v in values], dtype=dtype)


def residue_source(res):
    try:
        return str(res[FIELD])
    except (KeyError, ValueError, TypeError, IndexError):
        return ""


def author_chains(structure, use_assembly):
    state = _source.get()
    if state is not None:
        state['unsupported_assembly'] = bool(use_assembly and structure.assemblies)
        state["chains"] = {r.subchain: c.name for c in structure[0] for r in c}


def capture_residue(parsed, native, chain):
    state = _source.get()
    if (state is not None and not state.get('unsupported_assembly')
            and native is not None and chain in state.get("chains", {})):
        src = selector(state["chains"][chain], native.seqid.num, native.seqid.icode)
        key = (src["chain_id"], src["auth_seq_id"], src["insertion_code"])
        value = ({"source_sha256": state["sha256"], "source": src} if state["prior"] is None
                 else state["prior"].get(key))
        # Hold the object too: id reuse must never associate unrelated residues.
        encoded = json.dumps(value) if value else ""
        state["parsed"][id(parsed)] = (parsed, encoded if len(encoded) <= 1024 else "")
    return parsed


def parsed_source(res, ensembles):
    state = _source.get()
    def source(value):
        item = state["parsed"].get(id(value)) if state else None
        return item[1] if item is not None and item[0] is value else ""
    value = source(res)
    # The native table has one residue axis for all models. A selector is valid
    # for any selected coordinate ensemble only when every model agrees.
    return value if all(source(r) == value for r in ensembles) else ""


def token_sources(data):
    try:
        return json.dumps({str(int(i)): residue_source(data.structure.residues[int(i)])
                           for i in data.token_to_res})
    except Exception:
        return "{}"


def feature_source(token_selector, token_to_res_old):
    try:
        # This is the native explicit token->input-residue relation passed into
        # _from_feat, after its own padding selection; never an ordinal join.
        values = {_tokens.get()[str(int(token_to_res_old[int(i)]))] for i in token_selector}
        return values.pop() if len(values) == 1 else ""
    except (IndexError, KeyError, TypeError, ValueError):
        return ""


def emitted(res, chain, gemmi_res):
    rows = _emission.get()
    if rows is not None and len(gemmi_res):
        try:
            source = json.loads(residue_source(res)) if residue_source(res) else None
        except (TypeError, ValueError):
            source = None
        rows.append({"source": source,
                     "output": selector(chain, gemmi_res.seqid.num, gemmi_res.seqid.icode)})


def _events(directory, sha256):
    if not directory:
        return []
    found = []
    # Content-addressed buckets avoid scanning an entire campaign on each load.
    for path in (Path(directory) / sha256).glob("*.json"):
        try:
            row = json.loads(path.read_text())
            if row.get("output_sha256") == sha256 and row.get("schema") == "boltzgen.source-correspondence.v1":
                found.append(row)
        except (OSError, ValueError, TypeError):
            continue
    return found


def mapping_for_bytes(directory, raw):
    """Exact emitted document identity also survives native rank-file copies."""
    rows = _events(directory, digest(raw))
    if not rows:
        return None
    mappings = [row.get("target_residue_mapping") for row in rows]
    return mappings[0] if mappings[0] and all(m == mappings[0] for m in mappings) else None


def retain_converted(directory, cif, pdb, identity):
    """Bind the actual consolidation event to the emitted bytes, not its filename."""
    try:
        mapping = mapping_for_bytes(directory, Path(cif).read_bytes())
        raw = Path(pdb).read_bytes()
        present = {(line[21:22].strip(), int(line[22:26]), line[26:27].strip())
                   for line in raw.decode().splitlines() if line.startswith(('ATOM  ', 'HETATM'))}
        if not mapping or any((r['output']['chain_id'], r['output']['auth_seq_id'],
                               r['output']['insertion_code']) not in present
                              for group in mapping for r in group['residues']):
            return
        Path(pdb).with_suffix('.correspondence.json').write_text(json.dumps({
            'candidate_key': identity, 'structure_sha256': digest(raw),
            'target_residue_mapping': mapping}))
    except Exception:
        pass


def _prior_sources(raw):
    rows = _events(os.environ.get(ENV), digest(raw))
    if not rows:
        return None
    ledgers = [r.get("residues", []) for r in rows]
    if any(r != ledgers[0] for r in ledgers):
        return {}  # conflicting native emissions are unknown, not an ordinal join
    return {(r["output"]["chain_id"], r["output"]["auth_seq_id"],
             r["output"]["insertion_code"]): r["source"] for r in ledgers[0]}


def record_emission(namespace):
    """Called only after DesignWriter's actual generated-CIF write succeeds."""
    try:
        directory = os.environ.get(ENV)
        if not directory:
            return
        path = Path(namespace["gen_path"])
        rows = list(_last_emission.get())
        grouped = {}
        for row in rows:
            source = row["source"]
            if source:
                grouped.setdefault(source["source_sha256"], []).append(
                    {"source": source["source"], "output": row["output"]})
        output_hash = digest(path.read_bytes())
        mappings = [{"source_sha256": sha, "residues": joins,
                     "native_output": {"candidate_key": namespace["file_name"],
                                       "sha256": output_hash}}
                    for sha, joins in grouped.items()]
        event = {"schema": "boltzgen.source-correspondence.v1",
                 "candidate_key": namespace["file_name"], "output_path": str(path.resolve()),
                 "output_sha256": output_hash, "residues": rows,
                 "target_residue_mapping": mappings or None}
        # Keep every distinct actual emission, including overwritten native paths.
        # Identical bytes with conflicting producer/source joins remain ambiguous.
        target = Path(directory) / output_hash / (digest(json.dumps(event, sort_keys=True).encode()) + ".json")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(event, sort_keys=True))
        temporary.replace(target)
    except Exception:
        # Evidence must never turn an otherwise successful output into a failure.
        pass


def _parse_wrapper(fn):
    @functools.wraps(fn)
    def wrapped(path, *args, **kwargs):
        # parse_pdb invokes mmcif_from_block inside the same context.
        try:
            raw = Path(path).read_bytes()
            state = {"sha256": digest(raw), "path": str(Path(path).resolve()),
                     "parsed": {}, "prior": _prior_sources(raw)}
        except Exception:
            state = None
        token = _source.set(state)
        try:
            result = fn(path, *args, **kwargs)
            if state is not None:
                try:
                    unchanged = digest(Path(path).read_bytes()) == state['sha256']
                except OSError:
                    unchanged = False
                if not unchanged:
                    result.data.residues[FIELD] = ''
            return result
        finally:
            _source.reset(token)
    return wrapped


def _writer_wrapper(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        import inspect
        try:
            bound = inspect.signature(fn).bind(*args, **kwargs)
            batch = bound.arguments.get("batch") or {}
            ledger = batch.get(FEATURE, ["{}"])[0]
            sources = json.loads(ledger)
        except Exception:
            sources = {}
        token = _tokens.set(sources)
        try:
            return fn(*args, **kwargs)
        finally:
            _tokens.reset(token)
    return wrapped


def _cif_wrapper(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        rows = []
        token = _emission.set(rows)
        try:
            result = fn(*args, **kwargs)
            _last_emission.set(rows)
            return result
        finally:
            _emission.reset(token)
    return wrapped


class Instrument(ast.NodeTransformer):
    """Only observational columns and hooks; native science statements are kept."""
    def __init__(self, module):
        self.module = module
        self.function = ""

    def visit_FunctionDef(self, node):
        old, self.function = self.function, node.name
        self.generic_visit(node)
        self.function = old
        return node

    def visit_Assign(self, node):
        self.generic_visit(node)
        if self.module == "data.data" and any(isinstance(t, ast.Name) and t.id == "Residue" for t in node.targets):
            node.value.elts.append(ast.parse('(\"bms_source\", np.dtype(\"U1024\"))', mode="eval").body)
        if self.module == "data.parse.mmcif" and self.function == "mmcif_from_block" and ast.unparse(node.value) == "gemmi.make_structure_from_block(block)":
            return [node, ast.parse("_bms.author_chains(structure, use_assembly)").body[0]]
        return node

    def visit_Call(self, node):
        self.generic_visit(node)
        if ast.unparse(node.func) == "np.array" and any(k.arg == "dtype" and ast.unparse(k.value) == "Residue" for k in node.keywords):
            node.func = ast.parse("_bms.residue_array", mode="eval").body
        if self.module == "data.parse.mmcif" and self.function == "parse_polymer" and ast.unparse(node.func) == "parsed.append":
            node.args[0] = ast.Call(ast.parse("_bms.capture_residue", mode="eval").body,
                                    [node.args[0], ast.Name("res", ast.Load()), ast.Name("chain_id", ast.Load())], [])
        append = ast.unparse(node.func)
        source = None
        if self.module == "data.parse.mmcif" and self.function == "mmcif_from_block" and append == "res_data.append":
            source = "_bms.parsed_source(res, ensemble_residues)"
        elif self.module == "data.data" and self.function == "add_side_chains" and append == "residues_new.append":
            source = "_bms.residue_source(res)"
        elif self.module == "data.data" and self.function == "_from_feat" and append == "res_data.append":
            source = "_bms.feature_source(token_selector, token_to_res_old)"
        if source:
            node.args[0].elts.append(ast.parse(source, mode="eval").body)
        return node

    def visit_Compare(self, node):
        self.generic_visit(node)
        if self.function == "collate" and isinstance(node.left, ast.Name) and node.left.id == "key":
            for comparator in node.comparators:
                if isinstance(comparator, ast.List) and any(isinstance(v, ast.Constant) and v.value == "id" for v in comparator.elts):
                    comparator.elts.append(ast.Constant(FEATURE))
        return node

    def visit_Return(self, node):
        self.generic_visit(node)
        if self.module == "data.feature.featurizer" and self.function == "process" and isinstance(node.value, ast.Dict):
            node.value.keys.append(ast.Constant(FEATURE))
            node.value.values.append(ast.parse("_bms.token_sources(data)", mode="eval").body)
        return node

    def visit_Expr(self, node):
        self.generic_visit(node)
        text = ast.unparse(node)
        if self.module == "data.write.mmcif" and self.function == "to_mmcif" and text == "gemmi_chain.add_residue(gemmi_res)":
            return [node, ast.parse("_bms.emitted(res, chain_id, gemmi_res)").body[0]]
        if self.module == "task.predict.writer" and self.function == "write_on_batch_end" and text.startswith("open(gen_path, 'w').write("):
            return [node, ast.parse("_bms.record_emission(locals())").body[0]]
        if self.module == "task.predict.writer" and self.function == "write_on_batch_end" and text.startswith("open(self.refold_cif_dir /"):
            hook = "_bms.record_emission({'gen_path': self.refold_cif_dir / (str(batch['id'][0]) + '.cif'), 'file_name': batch['id'][0]})"
            return [node, ast.parse(hook).body[0]]
        return node


def compile_instrumented(module, source, filename):
    tree = Instrument(module).visit(ast.parse(source, filename))
    ast.fix_missing_locations(tree)
    return compile(tree, filename, "exec")


class Loader(importlib.abc.Loader):
    def __init__(self, code):
        self.code = code

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        module.__dict__["_bms"] = sys.modules[__name__]
        exec(self.code, module.__dict__)
        name = module.__name__.removeprefix("boltzgen.")
        if name == "data.parse.mmcif":
            module.parse_mmcif = _parse_wrapper(module.parse_mmcif)
        elif name == "data.parse.pdb_parser":
            module.parse_pdb = _parse_wrapper(module.parse_pdb)
        elif name == "task.predict.writer":
            for cls in (module.DesignWriter, module.FoldingWriter):
                cls.write_on_batch_end = _writer_wrapper(cls.write_on_batch_end)
        elif name == "data.write.mmcif":
            module.to_mmcif = _cif_wrapper(module.to_mmcif)


class Finder(importlib.abc.MetaPathFinder):
    def __init__(self, codes):
        self.codes = codes

    def find_spec(self, fullname, path=None, target=None):
        if fullname not in self.codes:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is not None:
            spec.loader = Loader(self.codes[fullname])
        return spec


def install():
    """All-or-nothing pinned-source preflight. Unsupported runtimes run unchanged."""
    try:
        spec = importlib.machinery.PathFinder.find_spec("boltzgen")
        package = Path(spec.origin).parent
        manifest = json.loads((Path(__file__).parent / "lib/boltzgen_native_source.json").read_text())
        codes = {}
        for module in MODULES:
            name = "boltzgen." + module
            if name in sys.modules:
                return False
            relative = module.replace(".", "/") + ".py"
            path = package / relative
            raw = path.read_bytes()
            expected = manifest["files"]["opt/venv/lib/python3.11/site-packages/boltzgen/" + relative]
            if digest(raw) != expected:
                return False
            codes[name] = compile_instrumented(module, raw, str(path))
        sys.meta_path.insert(0, Finder(codes))
        return True
    except Exception:
        return False
