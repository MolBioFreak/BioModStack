# S05 opt-in AmberTools native geometry adapter

## Qualified outcome and boundary

`services/molecular_geometry.py`, `services/molecular_geometry_recipe.py` and
`routers/molecular_geometry.py` add a **create-new-only, opt-in preparation
leaf**, `POST /api/molecular-geometry/construct`. It constructs real native
AmberTools coordinates, not an unpositioned descriptor or handcrafted helix.
The application owner registers the router behind existing identity/CSRF
ownership; no main/router-package registration, parent forms, graph recipe,
source custody, model submission, worker, image or native pin was changed here.

The result carries exact ASCII PDB stdout (`coordinate_text.encode('ascii')`),
SHA256, size, every native-to-authored atom identity and position, the separate
connected graph, complete retained request and typed provider/build provenance.
There are no session URLs. The parent can adopt these exact bytes through its
ordinary immutable PDB/file custody, retaining the graph/request/provenance as
companions. The native PDB does **not** encode graph bond orders, formal charges,
implicit hydrogens or pairing annotations; it is not alone a chemistry receipt.
Positioned CIF export, Studio import/transaction/Undo and actual Project cold
reopen remain parent integration work, not qualified by this stateless leaf.

## Scientific choices and supported scope

Every request explicitly authors operation, provider, form, topology, hydrogen
policy and construction graph recipe. No omitted form is replaced with a
provider default. Each strand has independently authored sequence, stable
residue/copy IDs, polymer and both termini. Pairing must explicitly cover the
entire reverse register with canonical antiparallel pairs. The native routine
internally produces a Watson–Crick complement; the adapter uses it **only after
both independently authored strands and their exact register match**. It does
not generate a missing second strand, repair mismatches, convert T/U, or modify
an existing document.

| Case | This optional geometry adapter |
|---|---|
| Canonical dsDNA | Explicit `abdna` (Arnott right-handed B-DNA) or `adna` (Arnott right-handed A-DNA) |
| Canonical dsRNA | Explicit `arna` (Arnott right-handed A-RNA) |
| Single base pair | Real native coordinates tested for DNA A/T, C/G and RNA U/A |
| ssDNA / ssRNA | Not supported; no automatic duplex, strand extraction, folding or pose prediction |
| Mismatches / overhangs / bulges / partial register / parallel pairing | Not supported; neither strand nor pairing is repaired |
| Multiple copies / nicks / custom covalent links | Not supported by this geometry leaf |
| Modifications on either strand, including base/sugar/linkage/terminal replacements | Not supported; canonicalization/stripping is not performed. Existing connected-graph support is separate |
| Ends | Both 5-prime and 3-prime hydroxyl explicitly selected on **each** strand; terminal phosphates/caps unsupported |
| Charge / hydrogen | Explicit graph `neutral_acid`; native heavy-atom coordinates only, with graph-owned implicit H. No pH/protonation choice, explicit H positioning, ions or force field |
| Other native forms (`aprna`, `lbdna`, `sbdna`) | Not exposed/qualified; no form fallback |
| DNA–RNA hybrid / noncanonical bases | Not supported; no T/U substitution |
| Length | Wrapper PDB representation bound: 999 residues/strand. Not a model admission rule; this is not a guarantee of every sequence/length's empirical geometry quality |
| Imported coordinates | Not accepted as a construction input; existing import/manual/launch routes are untouched |

Unsupported choices return an error **only from this optional construction
request**. Missing configuration/dependencies/native execution returns 503;
unsupported construction returns 422; a native coordinate/graph identity
mismatch returns 502. Existing graph/import/manual launches acquire no gate.
The result is idealized fiber-diffraction geometry along the native z-axis,
not a minimized structure, folded ss molecule, experimentally observed complex,
bound pose, inference output or validated function.

## Native API, source and licenses: actual evidence

The existing configured `md-preparation-v1.sif` was read/hash-verified, not
modified. SHA256:
`625d81608118af006295601b8275389e7681a1d98a6fb0622e6241c5d101df4b`.
Its conda package identifies AmberTools **24.8**,
`cuda_None_nompi_py311hb98b808_104`. The image has libnabc/include/nabc.h but no
compiler or nab executable; host gcc was used without installing anything.

* Native library SHA256:
  `c5c339ab2601e28368bea84b0bbd099c8cada70019c2aa2e793f306ef91fdbf9`.
* Only `fd_helix.c.o` is pulled from the archive by the static link. Object SHA256:
  `3a80863cf156a7c9ff6a347e5112dad7e63704005cca1e94f9c2170972be45ee`.
* **Important API finding:** installed `nabc.h` declares a `MOLECULE_T*` return,
  but the actual source implementation writes PDB to stdout and returns void.
  The launcher declares the actual void source signature rather than using the
  stale header return type; it must not dereference/free a return value or call
  a PDB writer on it. Successful link/header presence alone missed this behavior.
* Exact upstream archive downloaded and checked:
  <https://ambermd.org/downloads/AmberTools24_rc5.tar.bz2>, SHA256
  `52fb4fb3370a89b7ce738a2dc3e513c2fc1943fde4b4381846d9e75cc48d840f`.
  Source `fd_helix.c` SHA256:
  `ba210a6b58edf04e19314b977fca7549962555b1941cf4530583f449f5a37261`.
* Source inspection establishes native complement generation, first-residue
  phosphate omission, native residue order/numbering, helix rise/twist tables
  and stdout semantics. An independent source build reproduced byte-identical
  output to the managed-library wrapper for the five receipt cases (all bases
  and three exposed forms plus single-base DNA/RNA).
* Package-level AmberTools license defaults GPLv3 except enumerated exceptions;
  its conda metadata lists GPL/LGPL/BSD/MIT. The individual fd_helix source has
  David A. Case's 2018 BSD-style notice. The launcher/build are GPL-3.0-or-later.
  Generated bundles include full installed AmberTools LICENSE, original source
  and headers, all launcher/build source, original fd notice, glibc/GCC runtime
  notices, LGPL text and the relinkable object. See `scripts/molecular_geometry/NOTICE.md`.
  The unlinked remainder of libnabc.a is **not** redistributed in the bundle.

The actual static executable ran **inside the unchanged managed image** through
`--cleanenv --containall` with a read-only bundle bind. Runtime uses the existing
`paths.get_container_path()` and `scripts.lib.container_runtime.container_executable()`
owners. Set the trusted server-side `BMS_MOLECULAR_GEOMETRY_BUNDLE` explicitly;
it accepts no client executable/image/path. No new runtime manager, model,
worker cache or acquisition service was introduced. Deployment/acquisition and
portable-closure registration of this new ordinary artifact remain parent-owned.

## Build, package and qualification reproduction

From this worktree (baseline `60d65adad604ddcba9ed4e220141271131daae84`):

```sh
mkdir -p "$TMPDIR/s05-geometry-build"
curl -fL https://ambermd.org/downloads/AmberTools24_rc5.tar.bz2 \
  -o "$TMPDIR/s05-geometry-build/AmberTools24_rc5.tar.bz2"
python scripts/molecular_geometry/build.py \
  --image /mnt/BioModStack/apptainer/md-preparation-v1.sif \
  --source "$TMPDIR/s05-geometry-build/AmberTools24_rc5.tar.bz2" \
  --output "$TMPDIR/s05-geometry-bundle-qualified"
```

Choose a **new** output directory for every build: it refuses to overwrite an
existing bundle. The image argument above records the inspected installation,
not a path hardcoded in runtime code. The builder verifies image/library/source
hashes and never modifies the shared SIF/cache. It requires existing gcc/ar and
Apptainer, not new system installs. The executed toolchain was GCC 11.4.0,
Ubuntu 22.04 package `11.4.0-1ubuntu1~22.04.3`, GNU ld 2.38; receipt records exact
compiler/system archive hashes and `-O2 -static -Wl,--build-id=none`. Rebuilding
in independent output directories produced **identical executable and receipt
hashes**. Different toolchains must be independently qualified, not assumed
byte-reproducible. Full preferred native source can be rebuilt or relinked
using the generated notices' commands.

Actual final locations (under this profile's scratch, so copy to the ordinary
managed artifact owner before scratch expiry):

* `$TMPDIR/s05-geometry-bundle-qualified/`: executable, relinkable object, corresponding
  sources/notices, source-built comparison executable, five real PDB files,
  build receipt, actual `native-api-result.json` and JUnit qualification.
* `$TMPDIR/s05-geometry-bundle-qualified-repeat/`: independent second build.
* `$TMPDIR/s05-geometry-qualified-v2.tar.gz`: complete final bundle with sources,
  notices and test evidence, **850820 bytes**, SHA256
  `7583b16ca66a286538a7ab928ae8572e33ee1d2046920511c48e87f1bd1d23f6`.
* Executable SHA256:
  `f0e2af445e0fdc9df48c18ee890092fbc1e63421166d2922610b38497216e564`.
* Build receipt SHA256:
  `8c8b69488d03c969a1f14abba49afdd02677a804c283a7f3120af2e648b0fd36`.
* `qualification.junit.xml` SHA256:
  `615be606efa06c67170f3805501c2eade52bae47a3af95a1eafb308e4100735d`.
* Actual typed native response example SHA256:
  `89d90914af1d41eaba4adc014cbe207526573dd40ed86c5636d365a4d6189a01`.

Here `$TMPDIR` was `/home/dalab/.hermes/profiles/fresh/cache/scratch`.
Existing isolated graph test environment was reused; no shared API install:

```sh
BMS_MOLECULAR_GEOMETRY_BUNDLE="$TMPDIR/s05-geometry-bundle-qualified" \
PYTHONPATH="$PWD:$PWD/platform/api" \
"$TMPDIR/s05-molecular-venv/bin/python" -m pytest \
  platform/api/tests/test_molecular_geometry.py \
  platform/api/tests/test_molecular_construction.py -q -rs -s \
  --junitxml="$TMPDIR/s05-geometry-bundle-qualified/qualification.junit.xml"
```

Executed result: **85 passed, zero skipped**: 39 new geometry cases and 46
existing connected-graph cases. JUnit totals were parsed independently.
Key test identities in `test_molecular_geometry.py`:

* `test_actual_native_coordinates_graph_form_and_exact_readback`: all exposed
  forms and single-base pairs; independent Biotite PDB parse, exact direct-native
  stdout, atom inventory, two connected components, every bond's geometric
  distance, sugar rise/twist, coordinate-inferred absolute CIP stereochemistry,
  complementary contact distances, frozen JSON/bytes readback.
* `test_independently_authored_nonpalindromic_duplex`: dsDNA and dsRNA output
  both match independently authored nonpalindromic sequences.
* `test_private_typed_http_actual_native_and_errors`: actual unregistered router
  mounted privately in ASGI, **real Apptainer/native runner**, closed OpenAPI,
  success/error paths, existing original-coordinate file remains byte-identical.
  This is not whole-application authentication/import/cold-reopen acceptance.
* Sparse scientific choices, modifications on either strand, unsupported topology,
  exact wrapper-byte tamper, unconfigured optional dependency, T/U/import extras,
  and real wrapper invalid-input/no-coordinate cases.

An initial atom-map test failed because the graph owner already canonicalizes
OP1/OP2: the final adapter uses its actual exported names rather than assuming
raw RDKit O1P/O2P. All initial native cases were rerun successfully. The only
remaining test warning is the existing pytest-asyncio loop-scope deprecation.
No inference, rental, live service, store change, restart or deployment occurred.
