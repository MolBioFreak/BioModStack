# S05 connected molecular construction — CPU leaf qualification

## Delivered boundary

`services/molecular_construction_recipe.py` is the closed, versioned preparation
contract. `services/molecular_construction.py` constructs real connected chemical
graphs with RDKit and explicitly selected native CCD components. The new
`routers/molecular_construction.py` exposes stateless `POST
/api/molecular-construction/graph` and `POST /api/molecular-construction/complement`.
The application/custody owner must register this router; this lane does not edit
`main.py`, `files.py`, `binder_source_materialization.py`, parent forms, or stores.

These are **unpositioned authoring companions**, not molecular coordinate
files. Results contain actual atoms, bond orders, formal charge, aromaticity,
absolute CIP assignments, ordered native chiral tags, hydrogen counts,
strand/copy/residue/atom correspondence, connected-component membership, exact
recipe, isomeric SMILES, and provider/component fingerprints. `coordinates` is
explicitly null. No PDB/CIF with zero or invented coordinates is emitted. Existing
imported structure custody/geometry proceeds independently and is unchanged.

No model, scientific geometry/form, protonation, placement, predictor, inference,
force field, or worker is selected. Graph dependencies are lazy and optional;
missing RDKit reports this leaf unavailable rather than picking a substitute or
changing existing launch behavior. `requirements-molecular-construction.txt`
pins the separately qualified candidate, not an installation into the shared API.

## Explicit chemical intent

* Ordered residues are individually identified and stored 5′→3′. Strand, copy,
  residue and atom IDs are stable across roster reorder. Copies are individually
  named; two copies are not a duplex.
* Each end is explicitly hydroxyl or a native neutral-acid monophosphate. Both
  DNA and RNA cap flavors were exercised, including one-residue molecules and
  both ends phosphorylated. RDKit's synthetic 3′ cap residue maps back to the
  actual terminal residue using distinct `3P`/`3OP*` atom names, retaining native
  atom name, native residue number, and RDKit's `2PO` cap component label.
* Nicks split the actual covalent graph into connected fragments. Every new nick
  end has independently authored chemistry. Explicit atom-qualified covalent
  links can reconnect appropriate native attachment atoms.
* Duplex and intramolecular pairing are explicit residue-qualified tables with
  orientation and canonical/mismatch/unspecified labels. The table encodes exact
  register; unpaired residues remain overhangs, gaps or bulges. Pairing is never
  translated into covalent bonds or a claim of Watson–Crick geometry. No mismatch
  is repaired and no second strand is generated implicitly.
* Complement is a separate action returning a new canonical strand with new IDs
  and separately specified ends; it does not alter its source, copy source
  modifications, or infer nick chemistry.
* `CCDReplacement` replaces an explicitly mapped full residue span using actual
  bundled CCD atom/bond/charge/stereochemistry data. Every retained heavy atom
  has an explicit source→document map; omitted heavy atoms/leaving groups are
  explicitly enumerated. Existing external bonds reconnect only to their named
  retained attachment atoms. There is no methyl-label-only modification or
  handwritten O→S chemistry shortcut.
* Base/sugar/linkage operations do not silently change the separately authored
  phosphate topology. Terminal changes use a distinct explicit terminal
  replacement. All replacement intent remains in the resulting recipe.
* Results deep-copy their input recipe. A retained pre-edit result and a retained
  edited result carry their matching graph and annotations; readback parses the
  frozen result without construction. Studio transaction/Undo wiring belongs to
  the document owner and is **not** claimed here.

Hydrogens follow native graph valence and explicitly recorded neutral canonical
phosphate chemistry, not a pH calculation. The exported total hydrogen count is
necessary for aromatic `[nH]` fidelity; independent reconstruction tests set that
count, disable re-inference, and preserve graph-array/neighbor order for native
CW/CCW tags. CIP labels provide a second absolute-stereo check.

## Real provider qualification

### Canonical graph and modified components

Test environment: task-owned `$TMPDIR/s05-molecular-venv`, CPython 3.12.13,
RDKit wheel **2026.9.1** (native `rdBase.rdkitVersion` **2026.09.1**), Biotite
**1.7.1**. No shared environment was installed into or modified.

Read installed RDKit `MolFromSequence` documentation and exercised DNA flavors
6–9 and RNA flavors 2–5. These produce connected native phosphodiester graphs,
not dot-separated nucleosides; they have no conformers. All eight canonical DNA/
RNA monomers were independently compared against the bundled native CCD graphs,
including atom valence, formula and stereochemistry via isomeric SMILES.

Additional real component cases:

| Component | Actual tested chemistry |
|---|---|
| `5CM` | 5-methyl-deoxycytidine monophosphate; explicit polymerization leaving group omission; modified either independently specified DNA strand |
| `A2M` | 2′-O-methyladenosine 5′-(dihydrogen phosphate); RNA sugar modification and explicit 5′ phosphate |
| `DC` | Native deoxycytidine monophosphate as explicit terminal graph replacement, and nick/reconnection attachment component |
| `CSQ` | Complete C→U RNA phosphorothioate dinucleotide, native phosphorus **R** stereochemistry |
| `CSV` | Corresponding complete dinucleotide with native phosphorus **S** stereochemistry |

`CSQ`/`CSV` use exact atom correspondence qualified from their native CCD graphs.
They retain actual P, S, nonbridging O, complete sugar/base atoms and native
chirality; their formulas match while their isomeric SMILES differ. This does
**not** establish arbitrary DNA phosphorothioate construction, every sequence or
every charge state. The generic mapping is useful only when its exact selected
component and correspondence are qualified. CCD E/Z bond mapping and deuterated
components currently return explicit unsupported-operation errors rather than
silently dropping that chemistry. Arbitrary custom component upload and pH
state assignment are not implemented.

Installed license evidence: `rdkit/license.txt` is BSD-3-Clause; the wheel's
packaging `LICENSE.md` is MIT. Biotite's distribution license is BSD-3-Clause.
wwPDB usage policy states PDB archive data are CC0:
<https://www.wwpdb.org/about/usage-policies>. Component provenance includes the
native atom/bond definition hash and provider version; no component coordinate
columns are used to position atoms.

### Actual managed Amber geometry candidate (read-only inspection)

The configured container root points to `/mnt/BioModStack/apptainer`.
Inspected the existing `md-preparation-v1.sif` read-only via `apptainer exec
--cleanenv --containall`. Its SHA-256 is
`625d81608118af006295601b8275389e7681a1d98a6fb0622e6241c5d101df4b`.

* Conda metadata identifies **ambertools 24.8**, build
  `cuda_None_nompi_py311hb98b808_104`.
* `tleap` exists at `/opt/md-preparation/bin/tleap`.
* No `nab` or `nabc` executable is present; no `cc`/`gcc` compiler is available
  inside this image. This is not evidence that the native functionality vanished.
* `/opt/md-preparation/include/nabc.h` declares
  `MOLECULE_T *fd_helix(STRING_T*, STRING_T*)`, plus native get/put-PDB functions.
* `/opt/md-preparation/lib/libnabc.a` is installed and contains `fd_helix` symbol
  text. Library SHA-256:
  `c5c339ab2601e28368bea84b0bbd099c8cada70019c2aa2e793f306ef91fdbf9`.
  No compiled wrapper, successful link, executable helix construction or geometry
  capability is claimed.
* Conda package license metadata is
  `GPL-3.0-or-later AND LGPL-3.0-or-later AND BSD-3-Clause AND MIT`.
  `/opt/md-preparation/LICENSE` says AmberTools code defaults to GPLv3 except its
  enumerated exceptions; the inspected exceptions do not identify `nabc` as an
  exception. A native `nabc` wrapper needs GPLv3-compatible packaging review,
  rather than treating the whole AmberTools package as BSD.

**Concrete unresolved alternatives:** (1) keep exact imported geometry and use
this graph/recipe only for unpositioned authoring; immediately independent of
new geometry decisions; (2) explicitly choose a supported idealized helix form
and commission/qualify a small native Amber24.8 `libnabc` wrapper, with proper
GPLv3 packaging and exact native feature tests. The library's presence alone
qualifies neither independently authored mismatched duplexes nor ss geometry,
RNA/modification coverage, register, nick or terminal geometry. No option, A/B
form, complement, folded ss pose or native provider switch was selected here.

## Executed acceptance and reproduction

Focused test file: `platform/api/tests/test_molecular_construction.py`.
Final native run: **46 passed, zero skipped**; `git diff --check` passed.
Run in a task-owned isolated environment with the API's test dependencies and
the optional graph pins installed, keeping both repository and API on
`PYTHONPATH`:

```sh
PYTHONPATH="$PWD:$PWD/platform/api" "$S05_PYTHON" -m pytest \
  platform/api/tests/test_molecular_construction.py -q -rs -s
```

The repository conftest enters a route-free network namespace. The HTTP test
mounts the actual unregistered router in a scratch FastAPI application and
exercises real native chemistry, closed OpenAPI contracts, invalid geometry,
explicit complement, and unavailable dependency reporting. It deliberately
loads the leaf without the package's eager import of every unrelated router;
this is not a claim of whole-application registration acceptance.

Coverage includes ssDNA, explicit dsDNA, ssRNA, explicit dsRNA, unequal lengths,
register/overhang/mismatch annotations, intramolecular pairing/bulges, physical
nicks, named copies/reorder stability, all cap combinations, eight canonical
CCD differentials, modified either strand, native sugar/terminal/linkage
components, external attachment validation, exact export/JSON reopen and
independent atom/bond graph reconstruction. Native chemistry tests explicitly
skip when the optional RDKit environment has not been provisioned; such a skip
is not native qualification.

Initial collection exposed absent dependencies from the API's eager services
package; only the task venv received those test dependencies. Initial independent
receiver tests also exposed the need to honor exported aromatic hydrogen counts;
the final receiver uses those real counts instead of recomputing a different
protonation/tautomer. These were test-environment/receiver fixes, not relaxation
of chemical construction or introduction of model launch gates.

## Explicit remaining boundaries

Not complete S05: no selected geometry constructor, positioned CIF/Studio import,
Studio-native mutation/Undo application, mounted browser controls, actual
Save-twice/Template/Project cold reopen, parent router registration, worker
packaging/closure or model-native parser acceptance for this new constructor.
JSON reopen is tested and distinct from those boundaries. No live stores,
weights, inference, rentals, worker changes, service restart or deployment.
Existing imported structure processing is intentionally untouched.
