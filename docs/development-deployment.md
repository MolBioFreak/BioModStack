# One-off Development deployment

Normal timer polls use `--once`: they respect the automatic-deployment pause and defer while jobs are active.

To deploy the current `origin/test` once without changing the automatic-deployment pause:

```sh
python3 ~/.local/libexec/biomodstack/biomodstack_dev_sync.py --deploy-now
```

For an explicitly authorized deployment despite active jobs:

```sh
python3 ~/.local/libexec/biomodstack/biomodstack_dev_sync.py --deploy-now --allow-active-work
```

`--allow-active-work` applies only to this invocation. It does not cancel jobs, change job/attempt/lease records, restart remote workers, or enable future automatic deployment. The existing deployment lock, clean-tree/ancestry checks, health verification and rollback remain in use. The deployment receipt records the override and actual active-job count.

The normal managed transaction still restarts local Development services. Already launched, detached remote computation can continue while BMS monitoring reconnects to its retained attempt. This is not a promise that local computations, staging transfers or every other active operation tolerate a restart. Check the affected workload and compare the remote attempt/process identity before and after; do not cancel or resubmit a run merely to deploy.

The receipt is `~/.local/state/biomodstack/dev-sync.json` by default. Verify its `status` and deployed revision against `/api/health` and the served frontend build identity. A successful CLI exit with `paused` or `deferred-active-work` is not a deployment.
