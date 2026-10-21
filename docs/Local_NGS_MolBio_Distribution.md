# Local NGS / MolBio distribution

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
source identity and browser checks. This does not qualify scientific execution:
model containers, tools and data must be provisioned and verified separately.

This is a local derivative. No changes are pushed to the upstream BMS project.
Do not replace it with the full upstream checkout through auto-deployment.
