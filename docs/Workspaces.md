# Find a BMS workspace

Paths below are relative to the hosted `/bms/` application. For example, the Projects workspace opens at `/bms/projects`. Use the configured frontend origin; these are not API URLs. The [application router](../platform/frontend/src/App.tsx) defines the destinations.

| What you need | Where to go |
|---|---|
| Queue, recent work or workstation telemetry | `/` opens the Dashboard. `/infra` is an old bookmark that redirects there. |
| Experiments and their saved work | `/projects` lists Projects. `/projects/:projectId`, `/projects/:projectId/experiments/:experimentId` and `/projects/:projectId/experiments/:experimentId/domains/:domainId` reopen progressively narrower contexts. |
| Submit a model or workflow | `/submit` opens the launcher. When working from a Project setup, open the launcher through that setup so the Project context accompanies the request. |
| Inspect one run | `/jobs/:jobId` opens Job detail. `/jobs` by itself redirects to `/designs`; it is not the queue. |
| Review structures or designs | `/results`, `/designs` and `/designs/:jobId` open the results viewer. Keep the selected Job and result identity when sharing or reopening a specific output. |
| Browse or compare family alignments | `/alignments` is the workspace; `/alignments/library`, `/alignments/new` and `/alignments/compare` select a task. `/alignments/jobs/:jobId` opens the Job context; `/alignments/results/:resultId` opens a retained result. This is distinct from an MSA prepared as input to a folding Job. |
| Edit molecular records | `/designer` combines the Project hub with the Mol Bio editor. `/molbio` is the historical reopen path; it redirects to the editor and translates recognized revision parameters. |
| Review NGS runs and artifacts | `/ngs` opens the NGS Toolkit. `/molbio-ngs/domain-experiments/:domainExperimentId` is a historical link that carries the experiment ID into the toolkit. |
| Use statistics or manage workstation tools | `/stats` opens the statistics launcher; `/tools` opens the tool installer. Neither is the Job detail page. |
| Open BioXP | `/bioxp` opens the cockpit. `/bioxp/workflows` and `/bioxp/live-deck` select tabs in that same workspace. When the installation is known to have BioXP disabled, the router returns to the Dashboard. |

**If the page opens but the data is missing:** distinguish the route from the record it needs. A Job ID identifies execution, a Project domain identifies organizational context, and a result ID identifies retained output. See [Projects, Jobs and results](Project_to_Result.md) before treating an empty selection as a failed run.
