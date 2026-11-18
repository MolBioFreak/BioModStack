# Find the owner of an operational problem

For a Development installation, `./start_ui.sh status --runtime dev --json` reports managed service state. Use its configured frontend origin rather than assuming a port. A healthy web page is not proof that a workflow, remote target or instrument is ready.

| Observation | Where to look | What to distinguish |
|---|---|---|
| The UI or API is unavailable | [Installation](Nonproduction_Installation.md), [desktop and shells](Desktop_Runtime_and_Shell_Architecture.md) | Managed service state versus a stale bookmark |
| A model is listed but preparation fails | [Tool installer](Tool_Installer.md), [selective provisioning](Selective_Workflow_Provisioning.md), [pinned acquisition](Pinned_Setup_Acquisition.md) | Registry entry versus installed dependencies for a particular workflow |
| A setup exists but no run is visible | [Project-to-result guide](Project_to_Result.md) | Draft, preparation and bound Job are different records |
| A Job exists but has not started | Job detail and the [queue and placement owners](Services.md) | Admission, queue state and target placement versus completion |
| A remote Job is terminal but cannot be reviewed locally | [Remote workers](Remote_Worker_Execution_and_Cache.md), [results](Results_and_Analysis.md) | Remote completion versus retrieval and local publication |
| Two screens appear to disagree about a result | [Workspaces](Workspaces.md), [Project reconciliation](Project_Run_Reconciliation.md) | Job status versus a specific retained resource or revision |
| The checked-out branch and UI disagree | [Development deployment](development-deployment.md) | Repository state versus the deployed backend and served frontend |

Capture the runtime, workspace, relevant record ID and observed status first. Starting a second API, retrying a submission or restarting a service changes the situation you are trying to diagnose.
