"""Optional AmberTools preparation leaf. Never handles imported structures.

No installation, download, inference, store writes or registration on import.
The server explicitly configures BMS_MOLECULAR_GEOMETRY_BUNDLE; images resolve
through the existing configured container root. No client-supplied executable.
"""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
from functools import lru_cache

from services.molecular_construction import ConstructionUnavailable, construct_graph
from services.molecular_geometry_recipe import (
    GeometryProvenance, MolecularGeometryRequest, MolecularGeometryResult, PositionedAtom,
)

IMAGE_SHA = '625d81608118af006295601b8275389e7681a1d98a6fb0622e6241c5d101df4b'
LIB_SHA = 'c5c339ab2601e28368bea84b0bbd099c8cada70019c2aa2e793f306ef91fdbf9'
OBJECT_SHA = '3a80863cf156a7c9ff6a347e5112dad7e63704005cca1e94f9c2170972be45ee'
SOURCE_SHA = '52fb4fb3370a89b7ce738a2dc3e513c2fc1943fde4b4381846d9e75cc48d840f'


class GeometryUnavailable(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@lru_cache(maxsize=8)
def _file_hash(path: str, size: int, mtime: int, ctime: int) -> str:
    with open(path, 'rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def file_hash(path: Path) -> str:
    stat = path.stat()
    return _file_hash(str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _supported(request: MolecularGeometryRequest) -> str:
    r = request.construction
    if r.replacements or r.covalent_links:
        raise ValueError('native geometry adapter does not support modifications or custom covalent links; graph/import paths are independent')
    if len(r.strands) != 2:
        raise ValueError('native geometry currently supports only two explicit duplex strands, not ss folding or automatic complement')
    a,b = r.strands
    polymer = 'RNA' if request.form == 'arna' else 'DNA'
    if any(s.polymer != polymer for s in (a,b)):
        raise ValueError('explicit helix form and polymer differ; no DNA/RNA or T/U conversion')
    if any(len(s.copy_ids) != 1 or s.nicks for s in (a,b)):
        raise ValueError('native geometry does not support multiple copies or nicks')
    if any(s.five_prime != 'hydroxyl' or s.three_prime != 'hydroxyl' for s in (a,b)):
        raise ValueError('native geometry supports only explicit 5-prime and 3-prime hydroxyl termini')
    first = ''.join(x.base for x in a.residues)
    second = ''.join(x.base for x in b.residues)
    comp = dict(zip('ACGT' if polymer == 'DNA' else 'ACGU', 'TGCA' if polymer == 'DNA' else 'UGCA'))
    if second != ''.join(comp[x] for x in first[::-1]):
        raise ValueError('native geometry cannot construct mismatches, overhangs, unequal lengths or noncanonical duplexes; neither strand is changed')
    if len(first) > 999:
        raise ValueError('native PDB wrapper supports at most 999 residues per strand')
    # fd_helix internally generates a complement. Permit this only when BOTH
    # independently authored sequences AND exact register agree with its output.
    def key(e):
        return (e.strand_id,e.copy_id,e.residue_id)
    expected = {frozenset(((a.id,a.copy_ids[0],x.id),(b.id,b.copy_ids[0],y.id)))
                for x,y in zip(a.residues,reversed(b.residues))}
    actual = []
    if any(p.orientation != 'antiparallel' for p in r.pairing):
        raise ValueError('explicit antiparallel pairing is required for native geometry')
    for group in r.pairing:
        for pair in group.pairs:
            if pair.relation != 'canonical':
                raise ValueError('native geometry supports only explicitly canonical base pairs')
            actual.append(frozenset((key(pair.first),key(pair.second))))
    if len(actual) != len(expected) or set(actual) != expected:
        raise ValueError('explicit complete reverse-register pairing is required; no inferred pairing')
    return first


def _runtime():
    from paths import get_container_path
    from scripts.lib.container_runtime import container_executable
    configured = os.environ.get('BMS_MOLECULAR_GEOMETRY_BUNDLE')
    if not configured:
        raise GeometryUnavailable('opt-in AmberTools wrapper bundle is not configured')
    root = Path(configured).resolve()
    image = get_container_path('md-preparation-v1.sif').resolve()
    try:
        receipt_bytes = (root/'receipt.json').read_bytes()
        receipt = json.loads(receipt_bytes)
        if (receipt['image_sha256'],receipt['library_sha256'],receipt['native_object_sha256'],receipt['source_archive_sha256']) != (IMAGE_SHA,LIB_SHA,OBJECT_SHA,SOURCE_SHA):
            raise GeometryUnavailable('wrapper build does not match qualified native identities')
        if file_hash(image) != IMAGE_SHA or file_hash(root/'amber-fd-helix') != receipt['wrapper_sha256']:
            raise GeometryUnavailable('selected optional adapter bytes differ from their build receipt')
        command = [container_executable('apptainer'),'exec','--cleanenv','--containall',
                   '--bind',f'{root}:/bms-geometry:ro',str(image),'/bms-geometry/amber-fd-helix']
    except (OSError,ValueError,KeyError) as exc:
        raise GeometryUnavailable('optional native geometry bundle/image is unavailable or invalid') from exc
    return command, receipt, sha(receipt_bytes)


def _map_coordinates(data: bytes, request, graph):
    # Parse fixed-column native stdout; no coordinate regeneration/format rewrite.
    rows = []
    strands = request.construction.strands
    n = len(strands[0].residues)
    native = {}
    for line in data.decode('ascii').splitlines():
        if line == 'TER':
            continue
        if not line.startswith('ATOM  '):
            raise RuntimeError('unexpected native coordinate record')
        chain = line[21]
        if chain not in ('A','B'):
            raise RuntimeError('unexpected native chain')
        s = strands[0 if chain == 'A' else 1]
        number = int(line[22:26])
        index = number-1 if chain == 'A' else number-n-1
        if not 0 <= index < n:
            raise RuntimeError('native residue index does not map to authored strand')
        residue = s.residues[index]
        component = ('D' if s.polymer == 'DNA' else '')+residue.base
        if line[17:20].strip() != component:
            raise RuntimeError('native sequence differs from authored strand')
        name = line[12:16].strip()
        # The construction owner's canonical export already uses OP1/OP2.
        atom_id = f'{s.id}:{s.copy_ids[0]}:{residue.id}:{name}'
        xyz = [float(line[i:i+8]) for i in (30,38,46)]
        if atom_id in native or not all(math.isfinite(x) for x in xyz):
            raise RuntimeError('invalid/duplicate native atom or coordinate')
        native[atom_id] = line[76:78].strip()
        rows.append(PositionedAtom(graph_atom_id=atom_id,pdb_serial=int(line[6:11]),
                                   native_chain=chain,native_residue_number=number,
                                   native_component=component,native_atom_name=name,
                                   x=xyz[0],y=xyz[1],z=xyz[2]))
    if native != {a.id:a.element for a in graph.atoms}:
        raise RuntimeError('native heavy-atom inventory differs from the independently constructed connected graph')
    return rows


def construct_geometry(request: MolecularGeometryRequest) -> MolecularGeometryResult:
    sequence = _supported(request)
    try:
        graph = construct_graph(request.construction)
    except ConstructionUnavailable as exc:
        raise GeometryUnavailable(str(exc)) from exc
    command, receipt, receipt_sha = _runtime()
    try:
        run = subprocess.run([*command,request.form,sequence],capture_output=True,timeout=60,check=True)
    except (OSError,subprocess.SubprocessError) as exc:
        raise GeometryUnavailable('optional AmberTools coordinate construction failed') from exc
    atom_map = _map_coordinates(run.stdout,request,graph)
    return MolecularGeometryResult(
        request=request.model_copy(deep=True),graph=graph,coordinate_text=run.stdout.decode('ascii'),
        coordinate_sha256=sha(run.stdout),coordinate_size_bytes=len(run.stdout),atom_map=atom_map,
        provenance=GeometryProvenance(provider=request.provider,ambertools_version='24.8',
            image_sha256=IMAGE_SHA,library_sha256=LIB_SHA,native_object_sha256=OBJECT_SHA,
            wrapper_sha256=receipt['wrapper_sha256'],build_receipt_sha256=receipt_sha,
            source_sha256=SOURCE_SHA,license='GPL-3.0-or-later',
            recipe_sha256=sha(json.dumps(request.model_dump(mode='json'),sort_keys=True,separators=(',',':')).encode())),
        observations=['Idealized fiber-diffraction helix, not folded ss geometry, a bound pose, or functional validation.',
                      'Exact native heavy-atom PDB bytes; no explicit hydrogen positions, minimization, pH/protonation assignment or force field.',
                      'Phosphate chemistry/implicit hydrogens belong to the separate explicit neutral-acid graph; native PDB alone does not encode bond orders or charge.',
                      'Imported geometry is not an input to this create-new-only adapter and is never altered.'])
