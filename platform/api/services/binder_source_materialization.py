"""Exact source snapshots and the existing checked PDB-only receiving boundary.

No launch policy or display conversion lives here. Native bytes remain retained.
"""
from __future__ import annotations

import json
import shutil
import stat
import tempfile
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException
from paths import get_allowed_roots
from sqlalchemy import select

import asyncio
from contextlib import asynccontextmanager
import fcntl
import os
import hashlib
import io
from pathlib import Path
from typing import Literal, TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, model_validator

if TYPE_CHECKING:
    from schemas import CandidateRef
from database import Design, Job
from paths import resolve_allowed_path, to_allowed_relative
from services.binder_diagnostic_selection import CandidateDocument, selected_document


class StructureSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str | None = None
    design_id: str | None = None
    job_id: str | None = None
    request_id: str | None = None
    candidate_id: str | None = None
    document: CandidateDocument | None = None
    output_format: Literal["native", "pdb", "cif"] = "native"
    model_number: int | None = None
    expected_sha256: str | None = None
    candidate: CandidateRef | None = None

    @model_validator(mode="after")
    def exact_candidate(self):
        if self.candidate is not None:
            if any(getattr(self, key) is not None for key in
                   ("design_id", "job_id", "request_id", "candidate_id", "document")):
                raise ValueError("Choose candidate or legacy source identity, not both")
            ref = self.candidate
            model = ref.model if ref.kind == "native_document" else (
                ref.document.model if ref.kind == "design" and ref.document else None)
            if model is not None:
                if self.model_number is not None and self.model_number != model:
                    raise ValueError("Selected model differs from candidate reference")
                self.model_number = model
        return self


def _cif_ledger(columns: dict) -> dict:
    """Observed positions and declared chemistry; never guess bonds from geometry."""
    from Bio.Data.PDBData import protein_letters_3to1_extended

    def category(name):
        fields = {k.split('.', 1)[1]: v for k, v in columns.items() if k.startswith(name + '.')}
        return [dict(zip(fields, values)) for values in zip(*fields.values())] if fields else []

    atoms = category('_atom_site')
    entities = {r['entity_id']: r.get('type') for r in category('_entity_poly')}
    entity_types = {r['id']: r.get('type') for r in category('_entity')}
    positions = {}
    local_indices = {}
    for atom in atoms:
        model = atom.get('pdbx_PDB_model_num', '1')
        local_index = local_indices.get(model, 0)
        local_indices[model] = local_index + 1
        fields = ('label_asym_id', 'auth_asym_id', 'label_seq_id', 'auth_seq_id',
                  'pdbx_PDB_ins_code', 'label_comp_id', 'auth_comp_id', 'label_entity_id')
        key = (model, *(atom.get(k) for k in fields))
        if key not in positions:
            component = atom.get('label_comp_id') or atom.get('auth_comp_id')
            polymer_type = entities.get(atom.get('label_entity_id'))
            label_seq = atom.get('label_seq_id')
            entity_type = entity_types.get(atom.get('label_entity_id'))
            polymer = (entity_type == 'polymer' if entity_type is not None else
                       True if polymer_type or label_seq not in {None, '.', '?'} else None)
            canonical = protein_letters_3to1_extended.get(component.upper()) if component else None
            positions[key] = dict(model_number=int(model), **{k: atom.get(k) for k in fields},
                component_id=component, polymer_membership=polymer, polymer_type=polymer_type,
                polymer_evidence_origin=('_entity' if entity_type is not None else
                    '_entity_poly' if polymer_type else '_atom_site.label_seq_id' if polymer else None),
                canonical_amino_acid=canonical,
                canonical_mapping_origin='Bio.Data.PDBData.protein_letters_3to1_extended' if canonical else None,
                evidence_origin='_atom_site', atoms=[])
        positions[key]['atoms'].append({**atom, 'model_atom_index': local_index})
    # Retain every native column, including explicit unknowns and supplier bond
    # orders. This is a ledger, not a second chemistry normalization authority.
    chemistry = {name[1:]: category(name) for name in (
        '_chem_comp', '_chem_comp_atom', '_chem_comp_bond', '_struct_conn',
        '_pdbx_struct_mod_residue', '_entity', '_entity_poly', '_entity_poly_seq', '_struct_asym',
        '_pdbx_entity_nonpoly', '_pdbx_poly_seq_scheme', '_pdbx_nonpoly_scheme',
        '_pdbx_struct_assembly', '_pdbx_struct_assembly_gen', '_pdbx_struct_oper_list')
        if any(k.startswith(name + '.') for k in columns)}
    return {'source_positions': list(positions.values()), 'declared_chemistry': chemistry}


def _select_cif_model(columns: dict, number: int) -> dict:
    """Select a full model, preserving AtomWorks local annotation indices.

    AtomWorks serializes vector/pair annotations as idx0[/idx1], val0..N
    loops, NOT atom_site.id references. Multi-model writers add model_num;
    legacy loops without it apply to each model's local atom order. A complete
    model selection leaves that local order intact, so indices are not rebased
    against the document-global atom IDs. Saved targets are never regenerated.
    """
    result = dict(columns)
    models = columns.get('_atom_site.pdbx_PDB_model_num', ['1'] * len(columns['_atom_site.id']))
    rows = [i for i, value in enumerate(models) if int(value) == number]
    for key, values in columns.items():
        if key.startswith('_atom_site.'):
            result[key] = [values[i] for i in rows]
    categories = {key.split('.')[0] for key in columns
                  if key.startswith(('_annotation_', '_condition_'))}
    # Native aliases can also appear as category names in historical files.
    categories.update(key.split('.')[0] for key in columns
                      if key.split('.')[0] in {'_SA_CWI', '_C_CRD', '_C_INTCRD', '_C_CRDGRP'})
    for category in categories:
        model_key = category + '.model_num'
        if model_key not in columns:
            continue
        kept = [i for i, value in enumerate(columns[model_key]) if int(value) == number]
        for key, values in columns.items():
            if key.startswith(category + '.'):
                if kept:
                    result[key] = [values[i] for i in kept]
                else:
                    result.pop(key, None)
    return result


def structure_suffix(path: Path) -> str:
    """Keep the encoding suffix with its declared native format."""
    name = path.name.lower()
    for suffix in ('.cif.gz', '.mmcif.gz', '.cif.zst', '.mmcif.zst', '.bcif.gz', '.bcif.zst'):
        if name.endswith(suffix):
            return suffix
    return path.suffix.lower()


def _select_pdb_model(raw: bytes, number: int) -> bytes:
    """Project supplied records, not rewritten coordinates or inferred chemistry.

    Keep atom serials/altlocs and declarations verbatim when their endpoints
    survive. CONECT multiplicity is significant (e.g. explicit double bonds).
    Global headers describe the deposited source; MODEL-local records belonging
    to other frames are never borrowed, even when atom serials overlap.
    """
    lines = raw.splitlines(keepends=True)
    scoped = []
    current = None
    explicit = any(line[:6].strip() == b'MODEL' for line in lines)
    for line in lines:
        record = line[:6].strip()
        if record == b'MODEL':
            current = int(line[10:14])
        if current is None or current == number:
            scoped.append(line)
        if record == b'ENDMDL':
            current = None
    # Implicit PDB MODEL identity is zero at the existing Bio.PDB owner.
    if not explicit and number != 0:
        raise ValueError('Requested model is not present in this exact document')
    atoms = [line for line in scoped if line[:6].strip() in {b'ATOM', b'HETATM'}]
    serials = {line[6:11].strip() for line in atoms}
    identities = {(line[12:16], line[16:17], line[17:20], line[21:22],
                   line[22:26], line[26:27]) for line in atoms}

    def endpoint(atom, alt, residue, chain, seq, insertion):
        return any(a == atom and (not alt.strip() or b == alt) and
                   (c, d, e, f) == (residue, chain, seq, insertion)
                   for a, b, c, d, e, f in identities)

    kept = []
    for line in scoped:
        record = line[:6].strip()
        if record == b'CONECT':
            body = line.rstrip(b'\r\n')
            fields = [body[i:i + 5] for i in range(6, len(body), 5)]
            if not fields or fields[0].strip() not in serials:
                continue
            valid = [field for field in fields[1:] if field.strip() in serials]
            if not valid:
                continue
            # An unchanged record, including padding/newline, stays byte exact.
            if any(field.strip() and field.strip() not in serials for field in fields[1:]):
                ending = line[len(body):]
                line = body[:6] + fields[0] + b''.join(valid) + ending
        elif record == b'LINK':
            if not (endpoint(line[12:16], line[16:17], line[17:20], line[21:22], line[22:26], line[26:27])
                    and endpoint(line[42:46], line[46:47], line[47:50], line[51:52], line[52:56], line[56:57])):
                continue
        elif record == b'SSBOND':
            if not (endpoint(b' SG ', b' ', line[11:14], line[15:16], line[17:21], line[21:22])
                    and endpoint(b' SG ', b' ', line[25:28], line[29:30], line[31:35], line[35:36])):
                continue
        elif record == b'NUMMDL':
            line = line[:10] + b'   1' + line[14:]
        elif record == b'MASTER':
            # Deposited record counts no longer describe this projection.
            continue
        kept.append(line)
    return b''.join(kept)


def _chem_comp_type(component_id: str, gemmi) -> str:
    """CCD-style ``_chem_comp.type`` for a component name.

    Gemmi writes ``.`` for every component unless a monomer library is loaded.
    Mol* and other mmCIF readers decide which residues are polymer from this
    column, so a blank column leaves a protein with zero polymer residues and
    no ribbon. Gemmi's own residue table is the same classification the CCD uses
    for the standard polymer types; anything it cannot place stays a
    non-polymer, which is also the CCD default for ligands, ions and water.
    """
    if component_id == 'GLY':
        return 'peptide linking'  # the CCD types achiral glycine without an L/D prefix
    info = gemmi.find_tabulated_residue(component_id)
    if info is None:
        return 'non-polymer'
    if info.is_amino_acid():
        return 'D-peptide linking' if str(info.kind).rsplit('.', 1)[-1] == 'AAD' else 'L-peptide linking'
    if info.is_nucleic_acid():
        return 'DNA linking' if str(info.kind).rsplit('.', 1)[-1] == 'DNA' else 'RNA linking'
    return 'non-polymer'


def _fill_blank_chem_comp_types(columns: dict, gemmi) -> dict:
    """Fill only blank (``.``/``?``) component types; keep every supplied value."""
    ids = columns.get('_chem_comp.id')
    if not ids:
        return columns
    existing = columns.get('_chem_comp.type') or []
    columns['_chem_comp.type'] = [
        (existing[i] if i < len(existing) and existing[i] not in ('.', '?') else _chem_comp_type(component, gemmi))
        for i, component in enumerate(ids)
    ]
    return columns


def materialize_source_bytes(raw: bytes, suffix: str, destination: Path, *, output_format: str,
                             model_number: int | None = None,
                             verified_original_sha256: str | None = None) -> dict:
    """Retain stored/native bytes and expose explicitly identified derivatives."""
    from Bio.PDB import MMCIFIO, PDBParser
    from Bio.PDB.MMCIF2Dict import MMCIF2Dict
    from Bio.PDB.PDBExceptions import PDBConstructionException, PDBIOException
    from biotite import InvalidFileError

    class RetainedCifIO(MMCIFIO):
        def _requires_quote(self, value):
            # Legal CIF empty text (e.g. PDB assembly details) is distinct
            # from unknown/inapplicable. Bio.PDB indexes value[0] otherwise.
            return value == '' or super()._requires_quote(value)

    suffix = suffix.lower()
    original = raw
    encoding = 'identity'
    if suffix.endswith(('.gz', '.zst')):
        encoding = 'gzip' if suffix.endswith('.gz') else 'zstd'
        if encoding == 'gzip':
            import gzip
            stream = gzip.GzipFile(fileobj=io.BytesIO(raw))
        else:
            # Arrow is already a locked API dependency with streaming Zstd.
            import pyarrow as pa
            stream = pa.input_stream(pa.BufferReader(raw), compression='zstd')
        with stream:
            raw = stream.read(64 * 1024 * 1024 + 1)
        if len(raw) > 64 * 1024 * 1024:
            raise ValueError('Structure exceeds 64 MiB')
        native_suffix = suffix.rsplit('.', 1)[0]
    else:
        native_suffix = suffix
    if native_suffix not in {'.pdb', '.cif', '.mmcif', '.bcif'}:
        raise ValueError('Select a PDB, mmCIF or BinaryCIF structure document')
    native_format = 'cif' if native_suffix in {'.cif', '.mmcif'} else native_suffix[1:]
    source = destination / f'native.{native_format}'
    source.write_bytes(raw)
    original_path = source
    if encoding != 'identity':
        original_path = destination / ('original' + suffix)
        original_path.write_bytes(original)
    consumed, consumed_bytes, consumed_format = source, raw, native_format
    decoded, decoded_bytes = source, raw
    columns = None
    inspection_error = None
    ledger = {'source_positions': [], 'declared_chemistry': {}}
    structure = []
    numbers = []
    try:
        if native_format == 'bcif':
            from biotite.structure.io.pdbx import BinaryCIFFile
            binary = BinaryCIFFile.read(io.BytesIO(raw))
            block = binary.block
            columns = {'data_': next(iter(binary))}
            columns.update({'_' + name + '.' + field: col.as_array(str).tolist()
                            for name, category in block.items() for field, col in category.items()})
            writer = RetainedCifIO(); writer.set_dict(columns)
            out = io.StringIO(); writer.save(out)
            decoded_bytes = out.getvalue().encode()
            decoded = destination / 'decoded.cif'
            decoded.write_bytes(decoded_bytes)
        elif native_format == 'cif':
            columns = MMCIF2Dict(io.StringIO(raw.decode('utf-8')))
        if columns is not None:
            numbers = list(dict.fromkeys(int(n) for n in columns.get(
                '_atom_site.pdbx_PDB_model_num', ['1'] * len(columns['_atom_site.id']))))
            if model_number is not None and model_number not in numbers:
                raise ValueError('Requested model is not present in this exact document')
            selected_columns = _select_cif_model(columns, model_number) if model_number is not None else columns
            ledger = _cif_ledger(selected_columns)
            if model_number is not None:
                consumed = destination / f'model-{model_number}.cif'
                writer = RetainedCifIO(); writer.set_dict(selected_columns)
                out = io.StringIO(); writer.save(out)
                consumed_bytes = out.getvalue().encode()
                consumed.write_bytes(consumed_bytes)
                consumed_format = 'cif'
            # Legacy author summary remains compatible, but full positional
            # identity and nonpolymer atoms live in source_positions.
            identity = []
            for r in ledger['source_positions']:
                author = r.get('auth_seq_id')
                identity.append({'model_number': r['model_number'], 'auth_asym_id': r.get('auth_asym_id'),
                    'auth_seq_id': int(author) if author not in {None, '.', '?'} else author,
                    'insertion_code': '' if r.get('pdbx_PDB_ins_code') in {None, '.', '?'} else r['pdbx_PDB_ins_code'],
                    'residue_name': r['component_id']})
        else:
            structure = PDBParser(QUIET=True).get_structure('source', io.StringIO(raw.decode('utf-8')))
            numbers = [int(m.serial_num) for m in structure]
            if model_number is not None:
                if model_number not in numbers:
                    raise ValueError('Requested model is not present in this exact document')
                consumed = destination / f'model-{model_number}.pdb'
                consumed_bytes = _select_pdb_model(raw, model_number)
                consumed.write_bytes(consumed_bytes)
            identity = [{'model_number': int(m.serial_num), 'auth_asym_id': c.id,
                         'auth_seq_id': r.id[1], 'insertion_code': r.id[2].strip(), 'residue_name': r.resname}
                        for m in structure if model_number is None or int(m.serial_num) == model_number
                        for c in m for r in c]
            from Bio.Data.PDBData import protein_letters_3to1_extended
            for m in structure:
                if model_number is not None and int(m.serial_num) != model_number:
                    continue
                index = 0
                for c in m:
                    for r in c.get_unpacked_list():
                        atoms = []
                        for a in r.get_unpacked_list():
                            atoms.append({'id': str(a.serial_number), 'auth_atom_id': a.name,
                                'alt_id': a.altloc.strip(), 'type_symbol': a.element,
                                'model_atom_index': index})
                            index += 1
                        canonical = protein_letters_3to1_extended.get(r.resname)
                        ledger['source_positions'].append({'model_number': int(m.serial_num),
                            'auth_asym_id': c.id, 'auth_seq_id': str(r.id[1]),
                            'pdbx_PDB_ins_code': r.id[2].strip(), 'component_id': r.resname,
                            'hetero_flag': r.id[0], 'polymer_membership': None,
                            'canonical_amino_acid': canonical,
                            'canonical_mapping_origin': 'Bio.Data.PDBData.protein_letters_3to1_extended' if canonical else None,
                            'evidence_origin': 'PDB ATOM/HETATM', 'atoms': atoms})
            ledger['declared_chemistry'] = {'pdb_records': [line for line in raw.decode('utf-8').splitlines()
                if line[:6].strip() in {'CONECT', 'LINK', 'SSBOND', 'MODRES', 'SEQRES'}]}
    except (ValueError, KeyError, TypeError, UnicodeError, InvalidFileError, PDBConstructionException) as exc:
        if output_format != 'native' or model_number is not None:
            raise ValueError(f'Could not prepare the requested structure representation: {exc}') from exc
        inspection_error, identity = str(exc), []
    from services.protenix_chemistry import recipe_from_source_positions
    try:
        ledger['native_recipe'] = recipe_from_source_positions(ledger['source_positions'], ledger['declared_chemistry'])
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        # Recipe enrichment is observational. Never turn incomplete chemical
        # metadata into a refusal of an otherwise unchanged native acquisition.
        ledger['native_recipe'] = {'complex_components': [], 'covalent_bonds': [], 'entries': [],
            'unresolved': [{'inspection_error': str(exc)}, *ledger['source_positions']],
            'declared_chemistry': ledger['declared_chemistry']}
    selected_path, selected_bytes = consumed, consumed_bytes
    if output_format == 'pdb' and native_format != 'pdb':
        try:
            consumed = _cif_selection_pdb(decoded if consumed_format == 'bcif' else consumed, destination / 'derived.pdb')
        except (PDBConstructionException, PDBIOException) as exc:
            raise ValueError(f'Selected CIF cannot be represented in PDB: {exc}') from exc
        consumed_bytes, consumed_format = consumed.read_bytes(), 'pdb'
    if output_format == 'cif':
        if native_format == 'pdb':
            # Convert the ORIGINAL, not PDBIO's coordinate-only MODEL slice:
            # it drops SEQRES, LINK/SSBOND and assembly records. Gemmi is the
            # existing locked structural converter. Keep its full categories,
            # then use the same exact MODEL selector as annotated native CIF.
            import gemmi
            converted = gemmi.read_pdb_string(raw.decode('utf-8'))
            # Bio.PDB names an implicit (no MODEL record) frame 0, while
            # Gemmi names it 1. Preserve this service's source MODEL identity.
            for model, number in zip(converted, numbers, strict=True):
                model.num = number
            converted.setup_entities()
            converted.assign_label_seq_id()
            converted_columns = _fill_blank_chem_comp_types(
                MMCIF2Dict(io.StringIO(converted.make_mmcif_document().as_string())), gemmi)
            if model_number is not None:
                converted_columns = _select_cif_model(converted_columns, model_number)
            writer = RetainedCifIO(); writer.set_dict(converted_columns)
            out = io.StringIO(); writer.save(out)
            consumed_bytes = out.getvalue().encode()
            consumed = destination / 'derived.cif'
            consumed.write_bytes(consumed_bytes)
        elif consumed_format == 'bcif':
            consumed, consumed_bytes = decoded, decoded_bytes
        consumed_format = 'cif'
    # Hash each distinct byte domain once within this acquisition.
    hashes = {original: verified_original_sha256} if verified_original_sha256 is not None else {}
    def digest(payload):
        if payload not in hashes:
            hashes[payload] = hashlib.sha256(payload).hexdigest()
        return hashes[payload]
    return {'original_path': to_allowed_relative(original_path), 'original_sha256': digest(original),
            'stored_encoding': encoding, 'path': to_allowed_relative(consumed), 'format': consumed_format,
            'sha256': digest(consumed_bytes), 'native_path': to_allowed_relative(source),
            'native_sha256': digest(raw), 'native_format': native_format,
            'decoded_path': to_allowed_relative(decoded), 'decoded_sha256': digest(decoded_bytes),
            'selected_path': to_allowed_relative(selected_path), 'selected_sha256': digest(selected_bytes),
            'model_numbers': numbers, 'model_number': model_number,
            'author_residues': identity, 'inspection_error': inspection_error, **ledger}


async def prepare_resequence_sources(sources, session, selection_dir: Path,
                                     governed_roots: tuple[Path, ...]) -> list[dict]:
    """Use the files acquisition contract, retaining each byte domain and selector.

    The existing selection directory is the lifecycle owner. Its caller cleans
    the entire preparation on any failure, including later settings validation.
    Only root-level PDBs enter the existing loop input glob.
    """
    import shutil

    sources = list(sources)
    async with resequence_submission_guard(
            [request.model_dump(mode='json', exclude_none=True) for request in sources], session):
        roster = []
        for index, request in enumerate(sources, start=1):
            result = await materialize_structure_source(
                request.model_copy(update={'output_format': 'pdb'}), session, governed_roots,
                destination=selection_dir / '.sources' / f'{index:03d}')
            source_name = Path(result['source_path']).name
            suffix = structure_suffix(Path(source_name))
            stem = source_name[:-len(suffix)] if suffix else source_name
            consumed = selection_dir / f'{index:03d}_{stem}.pdb'
            shutil.copyfile(resolve_allowed_path(result['path']), consumed)
            entry = {**result, 'path': to_allowed_relative(consumed),
                     'source_request': request.model_dump(mode='json', exclude_none=True)}
            # The existing retained acquisition receipt owns the full author/atom
            # ledger. Job params carry only byte identities and this exact binding,
            # not several copies of a potentially enormous molecular graph.
            import json
            custody = selection_dir / '.sources' / f'{index:03d}' / '.source-custody.json'
            payload = json.dumps(entry, sort_keys=True).encode()
            custody.write_bytes(payload)
            roster.append({key: value for key, value in entry.items()
                           if key not in {'source_positions', 'author_residues', 'declared_chemistry', 'native_recipe'}})
            roster[-1].update(source_custody_path=to_allowed_relative(custody),
                              source_custody_sha256=hashlib.sha256(payload).hexdigest())
        return roster


async def prepare_resequence_existing_basket(backbone_input_pdbs: str, retained_params: dict,
                                             session, selection_dir: Path,
                                             governed_roots: tuple[Path, ...]) -> list[dict]:
    """Acquire only sources from a clone; execution/settings params never cross here.

    A retained roster is authoritative (including order and duplicate selections).
    Older baskets without one use only their root-level PDBs, not recursive files.
    Every source still passes the ordinary governed acquisition/digest boundary.
    """
    basket = (resolve_allowed_path(to_allowed_relative(Path(backbone_input_pdbs)))
              if Path(backbone_input_pdbs).is_absolute() else resolve_allowed_path(backbone_input_pdbs))
    retained = retained_params or {}
    async with resequence_submission_guard({'basket': backbone_input_pdbs, 'retained': retained}, session):
        roster = retained.get('resequence_sources')
        if isinstance(roster, list):
            requests = [StructureSourceRequest.model_validate(item['source_structure']) for item in roster]
        elif isinstance(retained.get('source_structure'), dict):
            requests = [StructureSourceRequest.model_validate(retained['source_structure'])]
        else:
            requests = [StructureSourceRequest(path=to_allowed_relative(path))
                        for path in sorted(basket.glob('*.pdb')) if path.is_file()]
        return await prepare_resequence_sources(requests, session, selection_dir, governed_roots)


def _preparation_lock_path(directory: Path) -> Path:
    # A sibling inode survives rmtree and all queued waiters. Never unlink it.
    return directory.with_name('.' + directory.name + '.resequence.lock')


def _source_paths(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _source_paths(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _source_paths(item)
    elif isinstance(value, str) and (value.startswith('inputs/') or value.startswith('/')):
        try:
            yield Path(value).resolve() if Path(value).is_absolute() else resolve_allowed_path(value).resolve()
        except ValueError:
            return


def _preparation_directories(value):
    try:
        root = resolve_allowed_path('inputs/design_selections/protein_local_redesign').resolve()
    except ValueError:
        # No inputs root: no preparation can be referenced; legacy no-op.
        return []
    found = set()
    for path in _source_paths(value):
        if root not in path.parents:
            continue
        directory = root / path.relative_to(root).parts[0]
        if directory.name.endswith('_designer.json'):
            directory = directory.with_name(directory.name[:-len('_designer.json')])
        if ((directory / '.resequence-preparation.json').is_file() or
                _preparation_lock_path(directory).is_file()):
            found.add(directory)
    return sorted(found)


async def _acquire_preparation_lock(directory):
    path = _preparation_lock_path(directory)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except BlockingIOError:
                # No blocking flock in either the event loop or an orphaned worker.
                await asyncio.sleep(0.01)
    except BaseException:
        os.close(fd)
        raise


@asynccontextmanager
async def resequence_submission_guard(value, session):
    """Hold referenced preparations through the *outer database transaction*.

    Canonical create must enter this before reading/copying any source, passing
    the full incoming params/provenance. _commit=False retains locks after return;
    commit, rollback and session close release them. No marker means legacy no-op.
    Reentrant within one session for prepared-child batches. Callers must acquire
    a batch's union before creating its children to keep cross-session ordering.
    """
    from sqlalchemy import event

    key = '_resequence_source_locks'
    directories = _preparation_directories(value)
    sync = getattr(session, 'sync_session', None)
    if not directories and (sync is None or not sync.info.get(key)):
        yield
        return
    locks = sync.info.setdefault(key, {})
    depth_key = '_resequence_source_guard_depth'

    def release_locks(owner):
        for fd in owner.info.pop(key, {}).values():
            os.close(fd)

    if not sync.info.get('_resequence_source_lock_listener'):
        def release(owner, transaction):
            # A create may commit before its last source read, or an outer
            # prepared batch may start another transaction. Both the lexical
            # scope AND the outer transaction must finish before custody ends.
            if transaction.parent is None and not owner.info.get(depth_key):
                release_locks(owner)
        event.listen(sync, 'after_transaction_end', release)
        sync.info['_resequence_source_lock_listener'] = True
    sync.info[depth_key] = sync.info.get(depth_key, 0) + 1
    try:
        for directory in directories:
            if directory not in locks:
                locks[directory] = await _acquire_preparation_lock(directory)
        if locks and not session.in_transaction():
            await session.begin()
        yield
    except BaseException:
        # A cancelled create must not leave a flushed, uncommitted claimant or a
        # lock behind. Rollback also handles a cancellation while waiting.
        await asyncio.shield(session.rollback())
        raise
    finally:
        sync.info[depth_key] -= 1
        if not sync.info[depth_key]:
            sync.info.pop(depth_key)
            if not session.in_transaction():
                release_locks(sync)


def begin_resequence_preparation(selection_dir: Path) -> dict:
    """Issue a cleanup capability for this newly allocated staging directory."""
    import json
    import secrets

    root = resolve_allowed_path('inputs/design_selections/protein_local_redesign')
    if selection_dir.resolve().parent != root.resolve() or selection_dir.is_symlink():
        raise ValueError('Not a Resequence preparation directory')
    receipt = {'path': to_allowed_relative(selection_dir), 'token': secrets.token_hex(32)}
    with (selection_dir / '.resequence-preparation.json').open('x') as handle:
        json.dump(receipt, handle)
    return receipt


async def cleanup_resequence_preparation(receipt: dict, session) -> bool:
    """Capability cleanup serialized with source acquisition/Job persistence."""
    import json
    import secrets

    root = resolve_allowed_path('inputs/design_selections/protein_local_redesign')
    directory = resolve_allowed_path(receipt['path'])
    if directory.parent != root.resolve() or directory.is_symlink():
        raise ValueError('Not a Resequence preparation directory')
    marker = directory / '.resequence-preparation.json'
    if not marker.exists():
        return False
    if marker.is_symlink():
        raise ValueError('Invalid preparation receipt')
    bound = json.loads(marker.read_text())
    if (bound.get('path') != to_allowed_relative(directory) or
            not secrets.compare_digest(str(bound.get('token', '')), str(receipt.get('token', ''))) or
            not bound.get('token')):
        raise ValueError('Invalid preparation receipt')
    # Never deadlock cleanup against this session's own prepared children.
    if directory in session.sync_session.info.get('_resequence_source_locks', {}):
        return False
    fd = await _acquire_preparation_lock(directory)
    try:
        if not marker.exists():
            return False
        return await _cleanup_unclaimed_preparation(directory, session)
    finally:
        os.close(fd)


async def _cleanup_unclaimed_preparation(directory, session):
    import shutil
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession
    from services.scientific_artifacts import resolve_json_value

    def references(value):
        return any(path == directory or directory in path.parents or
                   path == directory.with_name(directory.name + '_designer.json')
                   for path in _source_paths(value))

    # Project just ownership payloads, streaming bounded batches. Selecting typed
    # columns preserves ScientificArtifactJSON result processors; Job JSON may
    # also contain externalized top-level references. Never SQL-filter JSON text:
    # that would miss paths stored in governed scientific artifacts.
    # A fresh read transaction sees claims committed while cleanup waited.
    async with AsyncSession(bind=session.bind) as reader:
        rows = await reader.stream(select(Job.params, Job.provenance).execution_options(yield_per=64))
        try:
            async for params, provenance in rows:
                if references(resolve_json_value(params)) or references(resolve_json_value(provenance)):
                    return False
        finally:
            await rows.close()
    template = directory.with_name(directory.name + '_designer.json')
    template.unlink(missing_ok=True)
    shutil.rmtree(directory)
    return True


async def resolve_structure_source(request: StructureSourceRequest, session):
    import json

    # A frozen input is an allowed-path resource; producer identity is ancestry,
    # not an authorization dependency. Only an exact saved acquisition binding
    # takes this path. Unretained artifact selectors keep their registry checks.
    if request.path and request.expected_sha256:
        retained_path = resolve_allowed_path(request.path)
        inputs = resolve_allowed_path('inputs')
        if inputs in retained_path.parents:
            receipt_path = retained_path.parent / '.source-custody.json'
            if receipt_path.is_file() and not receipt_path.is_symlink():
                try:
                    receipt = json.loads(receipt_path.read_text())
                except (OSError, ValueError):
                    receipt = {}
                if not isinstance(receipt, dict):
                    receipt = {}
                saved = receipt.get('source_structure') or {}
                if not isinstance(saved, dict):
                    saved = {}
                supplied = request.model_dump(mode='json', exclude_none=True)
                # Output representation may change on reopening; original
                # selector, digest and MODEL must remain the saved binding.
                def binding(value):
                    return {k: v for k, v in value.items() if k != 'output_format'}
                if binding(saved) == binding(supplied):
                    return retained_path, receipt.get('source_identity') or {}
    identity = {}
    if request.candidate is not None:
        ref = request.candidate
        if ref.kind not in {'design', 'native_document'}:
            raise ValueError('Selected candidate has no structure document; use its native sequence/observation owner')
        document = None
        if ref.kind == 'design' and ref.document is not None:
            document = CandidateDocument(**ref.document.model_dump(exclude={'model'}, exclude_none=True))
        elif ref.kind == 'native_document':
            document = CandidateDocument(artifact_id=ref.artifact_id, target_state=ref.target_state)
        legacy = StructureSourceRequest(path=request.path, design_id=ref.design_id, job_id=ref.job_id,
            document=document, model_number=request.model_number, output_format=request.output_format,
            expected_sha256=request.expected_sha256)
        path, identity = await resolve_structure_source(legacy, session)
        return path, {**identity, 'candidate_ref': ref.model_dump(exclude_none=True),
                      'model': request.model_number}
    if request.request_id is not None or request.candidate_id is not None:
        raise ValueError('Local-redesign request/candidate sources were retired; select the Design instead')
    elif request.design_id:
        design = await session.get(Design, request.design_id)
        if design is None:
            raise ValueError("Source Design not found")
        job = await session.get(Job, design.job_id)
        if job is None or (request.job_id is not None and request.job_id != job.id):
            raise ValueError("Source Design does not belong to the requested Job")
        path, identity = await selected_document(job, design, request.document, session)
        # Preserve existing artifact-root containment; never trust a client path.
        from services.job_result_roots import resolve_persisted_job_result_root
        path = path.resolve() if path.is_absolute() else resolve_allowed_path(str(path))
        try:
            path.relative_to(resolve_persisted_job_result_root(job).resolve())
        except ValueError:
            # A remote Resequence continuation publishes its finals in the reviewed selection folder, which sits
            # above the continuation folder child_output_dir names. Accept that folder when the job itself names
            # it and it lies inside the job's result root (same allowance the finalizer applies).
            from services.result_state_integrity import _in_reviewed_final_dir
            if not _in_reviewed_final_dir(job, path):
                raise
    elif request.job_id and request.document and request.document.artifact_id:
        from services.binder_native_selection import resolve_native_source
        path, identity = await resolve_native_source(session, request.job_id, request.document.artifact_id)
        if request.document.target_state is not None and request.document.target_state != identity.get("target_state"):
            raise ValueError("Requested state does not match the selected native document")
    elif request.path and not request.document and not request.job_id:
        path = resolve_allowed_path(request.path)
    else:
        raise ValueError("Choose a governed path or an exact Design document")
    if request.path and identity:
        # Retained originals are byte-bound to the producer, not reacquired from
        # the historical output tree. Consumed derivatives remain model inputs.
        path = resolve_allowed_path(request.path)
    return path, identity


def _cif_selection_pdb(source: Path, destination: Path) -> Path:
    """Materialize only representable CIF coordinates for a PDB-only consumer."""
    from Bio.PDB import PDBIO
    from Bio.PDB.PDBParser import PDBParser
    from services.md.starting_structures import _parse_mmcif_structure
    structure = _parse_mmcif_structure(source.read_bytes())
    def identity(model):
        return [(chain.id, residue.id, residue.resname,
                 tuple((atom.id, atom.coord.tolist()) for atom in residue))
                for chain in model for residue in chain]
    models = list(structure)
    if len(models) != 1:
        raise ValueError('Selected CIF has no single PDB-representable model')
    original = identity(models[0])
    if not original or any(len(chain) != 1 or not (-999 <= residue_id[1] <= 9999)
                           or len(residue_id[2]) > 1
                           for chain, residue_id, _, _ in original):
        raise ValueError('Selected CIF chain/residue identifiers cannot be represented in PDB')
    io = PDBIO()
    io.set_structure(structure)
    io.save(str(destination))
    converted = PDBParser(QUIET=True).get_structure('selected', str(destination))
    after = identity(next(iter(converted)))
    if len(after) != len(original) or any(
            (a[0], a[1], a[2], [atom[0] for atom in a[3]]) !=
            (b[0], b[1], b[2], [atom[0] for atom in b[3]]) or
            any(any(abs(x - y) > 0.0011 for x, y in zip(atom_a[1], atom_b[1]))
                for atom_a, atom_b in zip(a[3], b[3]))
            for a, b in zip(original, after)):
        destination.unlink(missing_ok=True)
        raise ValueError('Selected CIF to PDB conversion lost chain, residue or atom mapping')
    return destination


IMMUTABLE_STRUCTURE_UPLOAD_MAX_BYTES = 64 * 1024 * 1024


GOVERNED_NGS_MANIFEST_SCHEMAS = frozenset(
    {"sequence_qc.manifest.v1", "biomodstack.construct_verification.v2"}
)


GOVERNED_NGS_MODEL_IDS = frozenset(
    {"nanopore", "ont_fastq_qc", "ont_plasmid_qc", "ont_construct_screening", "wf_clone_validation"}
)


def _lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def _contained_lexically(path: Path, root: Path) -> bool:
    try:
        _lexical_absolute(path).relative_to(_lexical_absolute(root))
        return True
    except ValueError:
        return False


async def governed_ngs_result_roots(session: AsyncSession) -> tuple[Path, ...]:
    """Return stable lexical authority from persisted canonical NGS job rows."""
    configured_root = _lexical_absolute(get_allowed_roots()["bms_results"])
    result = await session.execute(
        select(Job.output_dir, Job.child_output_dir).where(Job.model_id.in_(GOVERNED_NGS_MODEL_IDS))
    )
    roots: set[Path] = set()
    for output_dir, child_output_dir in result.all():
        for raw in (output_dir, child_output_dir):
            if not isinstance(raw, str) or not raw.strip():
                continue
            declared = Path(raw.strip()).expanduser()
            candidate = _lexical_absolute(declared if declared.is_absolute() else configured_root / declared)
            if _contained_lexically(candidate, configured_root):
                roots.add(candidate)
    return tuple(sorted(roots, key=os.fspath))


def _under_persisted_ngs_root(path: Path, governed_roots: tuple[Path, ...]) -> bool:
    return any(_contained_lexically(path, root) for root in governed_roots)


def _read_json_nofollow(path: Path, *, max_bytes: int = 10 * 1024 * 1024) -> dict:
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        absolute = Path(os.path.abspath(path))
        for index, component in enumerate(absolute.parts[1:]):
            final = index == len(absolute.parts[1:]) - 1
            flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
            if not final:
                flags |= os.O_DIRECTORY
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > max_bytes:
            raise ValueError("manifest is unsafe")
        raw = b""
        while len(raw) <= max_bytes:
            chunk = os.read(descriptor, min(1024 * 1024, max_bytes + 1 - len(raw)))
            if not chunk:
                break
            raw += chunk
        if len(raw) > max_bytes:
            raise ValueError("manifest is too large")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("manifest root is invalid")
        return payload
    finally:
        os.close(descriptor)


def _sha256_regular_file_nofollow(path: Path) -> tuple[str, int]:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("immutable structure path is not a regular file")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        return digest.hexdigest(), metadata.st_size
    finally:
        os.close(descriptor)


def is_path_allowed(path: Path) -> bool:
    """Check if path is within allowed roots."""
    try:
        resolved = path.resolve()
        for root in get_allowed_roots().values():
            try:
                resolved.relative_to(root.resolve())
                return True
            except ValueError:
                continue
        return False
    except (ValueError, RuntimeError):
        return False


def _is_governed_ngs_artifact(path: Path) -> bool:
    resolved = path.resolve()
    for parent in resolved.parents:
        manifest = parent / "qc_manifest.json"
        if not manifest.is_file() or manifest.is_symlink():
            continue
        try:
            payload = _read_json_nofollow(manifest)
            if payload.get("schema") in GOVERNED_NGS_MANIFEST_SCHEMAS:
                return True
        except (OSError, ValueError, json.JSONDecodeError):
            return True
    return False


def _reject_governed_ngs_artifact(path: Path) -> None:
    if _is_governed_ngs_artifact(path):
        raise HTTPException(status_code=403, detail="Use the job-scoped governed artifact route")


async def materialize_structure_source(
    payload: StructureSourceRequest, session: AsyncSession,
    governed_roots: tuple[Path, ...], *, destination: Path | None = None,
):
    """Shared exact acquisition for files and Resequence; destination is caller-owned.

    A supplied destination must not exist. Failure removes only the directory
    created by this acquisition, never a source or a prior preparation.
    """
    directory = None
    try:
        source, identity = await resolve_structure_source(payload, session)
        if not is_path_allowed(source) or _under_persisted_ngs_root(source, governed_roots):
            raise HTTPException(403, "Access denied to this structure")
        _reject_governed_ngs_artifact(source)
        descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError("Source is not a regular structure file")
            raw = handle.read(IMMUTABLE_STRUCTURE_UPLOAD_MAX_BYTES + 1)
        if len(raw) > IMMUTABLE_STRUCTURE_UPLOAD_MAX_BYTES:
            raise HTTPException(413, "Structure exceeds 64 MiB")
        digest = hashlib.sha256(raw).hexdigest()
        expected = identity.get("artifact_sha256") or payload.expected_sha256
        if expected is not None and digest != expected:
            raise ValueError("Selected source bytes differ from the exact document digest")
        if payload.expected_sha256 is not None and digest != payload.expected_sha256:
            raise ValueError("Selected source bytes changed")
        parent = resolve_allowed_path("inputs")
        parent.mkdir(parents=True, exist_ok=True)
        if destination is None:
            directory = Path(tempfile.mkdtemp(prefix="structure-", dir=parent))
        else:
            destination.resolve().relative_to(parent.resolve())
            destination.mkdir(parents=True, exist_ok=False)
            directory = destination
        result = materialize_source_bytes(raw, structure_suffix(source), directory,
            output_format=payload.output_format, model_number=payload.model_number,
            verified_original_sha256=digest)
        retained = payload.model_copy(update={"path": result["original_path"],
                                              "expected_sha256": result["original_sha256"]})
        result = {**result, "source_identity": identity, "source_path": to_allowed_relative(source),
                  "source_structure": retained.model_dump(mode="json", exclude_none=True)}
        # Keep the already governed acquisition's ancestry beside its retained
        # bytes. Replay access is still governed by the inputs path and digest,
        # not by the continued existence of a historical producer row.
        import json
        (directory / '.source-custody.json').write_text(json.dumps({
            'source_structure': result['source_structure'], 'source_identity': identity}, sort_keys=True))
        return result
    except HTTPException:
        if directory is not None:
            shutil.rmtree(directory)
        raise
    except (ValueError, OSError, EOFError, KeyError) as exc:
        if directory is not None:
            shutil.rmtree(directory)
        raise HTTPException(422, str(exc)) from exc
