# BNS Plasmid Development Toolkit

This checkout installs the MolBio Viewer, the NGS workspace, and their shared
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

This edition is prepared on `bns-plasmid-development-toolkit`. No changes are pushed to the full upstream BMS branches.
Do not replace it with the full upstream checkout through auto-deployment.


The Control Panel preserves the explicitly selected managed Python/frontend
installation roots while discarding inherited runtime, image and credential
overrides. Database status and backup actions use the selected local lane's
Core, MolBio, NGS and Experiment stores rather than a fixed production path.
