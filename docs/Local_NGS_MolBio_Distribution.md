# BNS Plasmid Development Toolkit

This checkout installs Molecular Tools, the NGS workspace, and their shared
Projects. The home route opens MolBio. The native BioModStack Control Panel
remains the supported way to start and stop the managed runtime.

The generic Job Launcher, Data Viewer, Dashboard, Stats toolkit, BioXP and
protein/structure workspaces are removed from this frontend. Unreachable UI
source and its retired tests are removed rather than hidden behind flags.
The MSA server menu and API controller are removed. The native Control Panel
retains runtime controls and removes its BioXP status section.

The API registers MolBio/NGS routes plus the shared project, sequence, job,
file, execution, resource and telemetry services that those components need.
Its model registry contains only the Nanopore workflow definition.
Protein, MD, Boltz, external-result import and FrustraMPNN workers do not start.
The common job engine rejects unrelated model IDs before materialization.
Shared implementation dependencies remain where the existing NGS engine and
project persistence import them; deleting them would require a separate engine
refactor. Dependency locks and scientific parameter schemas are unchanged.

Validation: frontend TypeScript/production build; existing MolBio sequence and
NGS launch/clone tests; distribution API boundary tests; managed runtime health,
source identity and native launcher checks. Browser rendering has not been
verified through the browser approval gate. This does not qualify scientific execution:
model containers, tools and data must be provisioned and verified separately.

This edition is published on `bns-plasmid-development-toolkit-test`. The original local integration history is retained on `bns-plasmid-development-toolkit`. Do not push this edition to the full upstream BMS `test` or `main` branch.
Do not replace it with the full upstream checkout through auto-deployment.


The Control Panel preserves the explicitly selected managed Python/frontend
installation roots while discarding inherited runtime, image and credential
overrides. Database status and backup actions use the selected local lane's
Core, MolBio, NGS and Experiment stores rather than a fixed production path.

Projects expose MolBio, NGS, Add DNA sequence and Add NGS data record directly.
Export to Project requires a Project only; a managed native workspace is reused
or prepared through the existing connector. Multiple workspaces require an
explicit selection. Global experiment setup and Global Project linking are not
part of this edition's intake flow. New draft inventory workspaces carry no invented
analysis capabilities, acceptance criteria or acquisition authorization.
The public attachment catalogue exposes NGS/MolBio adapters only.

The versioned `bns.ngs-data-record.v1` API at
`/api/projects/{project_id}/ngs-data-records` stores operational intake in the
existing append-only Project record authority. It accepts a typed format, use
case and workflow stage, optional sample name, data location, notes, and uploaded
Project input references. A data location is an operator reference, not verified
artifact availability. An intake record or operator-recorded completion is not a
scientific result or pass. Actual processing remains in the NGS runtime.

MolBio Quick Add and its creation dialogs remain above the focused viewer.
Sequence glyph colors adapt their brightness to the active theme while retaining
the selected palette. Theme action/status text and filled-button labels have
separate contrast tokens; selected sequence regions and feature-label halos keep
their text readable without changing stored sequence or annotation data.

Native auto-annotation uses the same pLannotate 2.0.0 explicit Conda lock and
reference bundle as `docker/api.Dockerfile`. Install micromamba using the version
and SHA256 in that recipe, create `plannotate` from
`docker/plannotate-conda-linux-64.lock`, and run `plannotate setupdb` in that
environment. Export `BMS_MICROMAMBA_BIN`, `BMS_MICROMAMBA_ROOT_PREFIX`, and
`BMS_PLANNOTATE_ENV=plannotate` in the installation launcher environment before
starting the managed runtime. The Control Panel and generated API unit preserve
these installation selectors across restarts. An existing direct installation
may use `BMS_PLANNOTATE_BIN` instead. Package versions, reference availability,
and a real API annotation must be checked before accepting the installation.
