# Follow a Project from setup to result

Use a Project when the experiment and its outputs need to be found together later. Use Job detail when the question is about a particular execution. The two screens read different records.

1. **Start in the right context.** Open Projects, select the Project, then its experiment and domain. A saved workflow setup belongs to that context. Reopen the setup to continue authoring; a setup ID is not a Job ID.
2. **Distinguish saving from preparing.** Saving keeps the editable setup. Preparing resolves the launch request and gives the launcher its Project context. Neither operation, by itself, proves that a Job exists. The [setup service](../platform/api/services/global_experiments/workflow_setups.py) and [launch-context service](../platform/api/services/global_experiments/launch_contexts.py) own these records.
3. **Find the actual execution.** After submission, use the bound Job ID. Job detail shows run state; the Project shows the binding and its projection. If a submission response is interrupted, inspect the existing Project binding and Job before starting a separate submission. Do not identify a run by its display name or by the newest output directory.
4. **Open the right result.** A structure/design result opens in Results; a family alignment has its own result reader; sequencing runs use the NGS Toolkit. Not every successful Job produces a Design row. For a Project attachment, follow the recorded result or artifact identity rather than substituting another candidate from the same run.

## Which record answers my question?

| Question | Record to inspect |
|---|---|
| What experiment was I working on? | Project → experiment → domain, then its saved setup or attached resource |
| Did launch produce a Job? | Launch-context binding and the referenced core Job |
| Is the run waiting, running or terminal? | Job detail and the applicable queue, attempt or specialist status |
| What did the Project retain? | The Project resource, revision or receipt and the source identity it references |
| Did a remote result arrive locally? | The remote retrieval and publication records, not the mere existence of a terminal Job |

For maintainers: start with the [Project API](../platform/api/routers/project_manager.py), [Job API](../platform/api/routers/jobs.py), [Project reconciliation](Project_Run_Reconciliation.md) and [result readers](Results_and_Analysis.md). A Project status is a projection of the Job; it is not a second scheduler.
