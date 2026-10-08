"""Native script bridge to the shared local/worker attempt journal.

The launch owner supplies this context. A configured but invalid context never
falls back to host HTTP. Scientific argv comes only from the shared native compiler.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

# Native scripts and the API import the exact same dependency-free authority.
_ROOT = Path(__file__).resolve().parents[2]
_API = _ROOT / "platform" / "api"
for _path in (_ROOT, _API):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
from component_runtime import ComponentRequest, ComponentRuntime  # noqa: E402


def runtime_from_environment(context_path: str | Path | None = None) -> ComponentRuntime | None:
    if context_path is None:
        context_path = os.environ.get("BMS_COMPONENT_CONTEXT")
    if context_path is None:
        return None
    path = Path(context_path)
    if not context_path or not path.is_absolute():
        raise ValueError("BMS_COMPONENT_CONTEXT must be an absolute trusted context path")
    context = json.loads(path.read_text())
    required = {"ledger_path", "artifact_root", "attempt_id", "root_job_id", "target_id", "lease_id"}
    if not isinstance(context, dict) or not required <= context.keys():
        raise ValueError("incomplete component execution context")
    if any(not Path(context[key]).is_absolute() for key in ("ledger_path", "artifact_root")):
        raise ValueError("component context storage paths must be absolute")
    runtime = ComponentRuntime(Path(context["ledger_path"]),
        attempt_id=context["attempt_id"], root_job_id=context["root_job_id"],
        target_id=context["target_id"], lease_id=context["lease_id"],
        artifact_root=Path(context["artifact_root"]),
        source_identity=context.get("source_identity"), plan_sha256=context.get("plan_sha256"),
        execution_plan=context.get("execution_plan"))
    runtime.context = current_generation_context(context, runtime.root_state())
    return runtime


def current_generation_context(context, state):
    """View the durable authorized edge without rewriting the attempt input."""
    state = state or {}
    edge = state.get('continuation_edge')
    if edge is None:
        return dict(context, generation=state.get('generation', 0))
    return dict(context, generation=state['generation'], parent=dict(edge['parent_snapshot']),
                execution_plan=edge['execution_plan'], plan_sha256=edge['plan_sha256'],
                native_runtime=dict(edge['native_parameters']),
                resources=dict(edge['resources']) if edge.get('resources') is not None else context.get('resources', {}))


def _runtime() -> ComponentRuntime:
    runtime = runtime_from_environment()
    if runtime is None:
        raise RuntimeError("component launch context is not configured")
    return runtime


def await_external_service(service_id: str, native_input: Any) -> tuple[Path, str]:
    """Wait for declared controller data through the existing attempt journal.

    No provider import, credentials, HTTP callback or worker search lives here.
    The attempt supervisor continues to own cancellation of this native waiter.
    """
    import time
    from component_runtime import ResultReference
    runtime = _runtime()
    request_id = runtime.submit_external_service(service_id, native_input)
    while True:
        state = runtime.external_service(request_id)  # includes cancellation fence
        if state['result'] is not None:
            if state['result'].get('error'):
                raise RuntimeError(state['result']['error'])
            reference = ResultReference(**state['result'])
            return reference.resolve(runtime.artifact_root).parent, reference.sha256
        time.sleep(0.2)


def submit_child(payload: Mapping[str, Any], *, parent_job_id: str, stage: str,
                 child_key: str, required: bool = True) -> str:
    return _runtime().submit(ComponentRequest.capture(parent_job_id=parent_job_id,
        stage=stage, child_key=child_key, payload=payload, required=required))


def child_status(child_id: str) -> dict[str, Any]:
    return _runtime().child_status(child_id)


def join_children(child_ids: Sequence[str]) -> tuple[dict[str, Any], ...]:
    return _runtime().join_children(child_ids)


def complete_validated_child(child_id: str, *, result: Mapping[str, Any], references) -> None:
    _runtime().complete_validated_child(child_id, result=result, references=references)


def _process_identity(pid: int) -> dict[str, Any]:
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return dict(pid=pid, start_ticks=int(fields[19]), process_group=int(fields[2]))


def _owned_group_writers(process) -> list[int]:
    from tools.bms_remote_worker import attempt_writers
    identity = getattr(process, "_bms_writer_identity", None)
    if identity is None:
        raise RuntimeError("Native process has no trusted writer identity")
    return attempt_writers(identity)


def _stop_processes(processes: Sequence[Any], timeout: float = 10) -> bool:
    """Use the worker's fenced stop authority, including escaped native writers."""
    from tools.bms_remote_worker import quiesce_writers
    quiet = True
    for process in processes:
        identity = getattr(process, "_bms_writer_identity", None)
        if identity is None:
            raise RuntimeError("Native process has no trusted writer identity")
        quiet = quiesce_writers(identity, timeout_seconds=timeout) and quiet
        process.poll()
    return quiet

def native_resource_config(plan: dict, resources: dict, lock_path: str, source_root: Path) -> str:
    """Bind compiled static tasks to one inherited process-lifetime compute slot.

    Native setup expressions are retained, including task-input closures. Dynamic
    templates are deliberately excluded: a waiter cannot own its child's slot.
    """
    import re
    import shlex
    required = resources['required']
    if any(type(required.get(k)) is not int or required[k] <= 0 for k in ('cpus', 'memory_bytes')):
        raise ValueError('explicit admitted CPU/RAM budget required')
    if not Path(lock_path).is_absolute():
        raise ValueError('shared compute lock must be absolute')
    def groovy(value):
        return "'" + value.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n') + "'"
    # The descriptor stays open in .command.run and its descendants until exit;
    # no explicit unlock, timer or lock-file unlink can free a live writer's slot.
    acquire = f'exec 198>{shlex.quote(lock_path)}\nflock -x 198 || exit 1\n'
    lines = [f'executor.cpus = {required["cpus"]}',
             f'executor.memory = {groovy(str(required["memory_bytes"]) + " B")}', 'process {']
    seen = set()
    for component in plan['metadata']['static_components']:
        from native_components import NATIVE_COORDINATORS, PROCESS_CONTRACTS
        # Waiters must not own the compute slot needed by their descendants.
        # Role and overlap budget come from the shared selected resource policy,
        # not process-name heuristics or a workflow-specific scheduler.
        role = component['resources_json'].get('execution_role', 'compute')
        if role not in {'compute', 'coordinator'}:
            raise ValueError('unknown selected component execution resource role')
        selection = component['selection_json']
        name = component['authority'].rsplit(':', 1)[1]
        if selection.get('native_process', name) != name or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
            raise ValueError('invalid compiled native process authority')
        if name in seen:
            continue
        seen.add(name)
        if role == 'coordinator':
            policy = component['resources_json']
            if (component['authority'] not in NATIVE_COORDINATORS or policy.get('gpu')
                    or policy.get('max_forks') != 1):
                raise ValueError('compute slot exemption requires a bounded native waiter')
            lines += [f'  withName: {groovy(name)} {{', '    maxForks = 1', '  }']
            continue
        directives = PROCESS_CONTRACTS[component['authority']][-1]
        setup = [directive[len('beforeScript '):] for directive in directives
                 if directive.startswith('beforeScript ')]
        if len(setup) > 1:
            raise ValueError('ambiguous compiled native setup authority')
        expression = setup[0] if setup else "''"
        lines += [f'  withName: {groovy(name)} {{', '    beforeScript = {',
                  f'      def nativeSetup = {expression}',
                  f'      return {groovy(acquire)} + (nativeSetup instanceof Closure ? nativeSetup.call() : nativeSetup)',
                  '    }', '  }']
    return '\n'.join([*lines, '}', ''])


def resource_bound_command(command, invocation, context, run_dir) -> list[str]:
    """Append only typed execution config to unchanged compiler-produced argv."""
    resources = context.get('resources', {})
    if not resources.get('required'):
        raise ValueError('component execution requires selected-plan resource reservation')
    if invocation is not None:
        from types import SimpleNamespace
        from services.remote_execution.targets import selected_plan_target_resources
        child = selected_plan_target_resources(SimpleNamespace(id=context['target_id']),
            invocation.execution_plan, gpu_ids=resources['gpu_ids'], scratch_bytes=0)
        if any(child['required'][key] > resources['required'][key] for key in ('cpus', 'memory_bytes')):
            raise ValueError('compiled child exceeds the shared root resource reservation')
        for budget in ('compute', 'coordinator_overlap'):
            if budget not in resources or any(child[budget][key] > resources[budget][key]
                    for key in ('cpus', 'memory_bytes')):
                raise ValueError('compiled child exceeds the admitted compute/coordinator subdivision')
    plan = invocation.execution_plan.to_dict() if invocation is not None else context['execution_plan']
    config = Path(run_dir) / 'component-resources.config'
    config.write_text(native_resource_config(plan, resources, context['resource_lock_path'],
                                           Path(context['working_directory'])))
    return [*command, '-c', str(config)]


def retry_component_workflow(context_path: Path, *, component_id: str,
                             operation_id: str, failure_code: str, actor: str,
                             boot_id: str, continuation_lease_id: str,
                             resources: Mapping[str, Any],
                             native_invocations: list | None = None) -> dict[str, Any]:
    """Shared local/worker retry authorization; caller owns lease and launch.

    Native adapters supply only typed requests/receipts. The same compiler and
    run_component_workflow pump execute replacements and required collectors.
    """
    from component_runtime import digest, file_identity
    from services.nextflow import (compile_component_retry_invocation,
                                   component_checkpoint_parent_snapshot)
    from scripts.bms_md.spawn_replicas import prepare_replica_retry

    runtime = runtime_from_environment(context_path)
    if runtime is None:
        raise ValueError('retry requires its original component execution context')
    # Detach the admitted generation snapshot before any native compilation.
    # The original context file and runtime attempt/source/lease remain immutable.
    renewed_resources = json.loads(json.dumps(dict(resources)))
    required = renewed_resources.get('required', {})
    if (renewed_resources.get('execution_target_id') != runtime.target_id
            or any(type(required.get(key)) is not int or required[key] <= 0
                   for key in ('cpus', 'memory_bytes'))
            or not isinstance(renewed_resources.get('gpu_ids'), list)):
        raise ValueError('retry requires explicit same-target admitted resources')
    prior = runtime.retry_status(operation_id)
    if prior is not None:
        if (prior['component_id'] != component_id or prior['actor'] != actor
                or prior['continuation_lease_id'] != continuation_lease_id
                or runtime.root_state()['boot_id'] != boot_id
                or prior['resources'] != renewed_resources
                or prior['retry_context'].get('resources') != prior['resources']):
            raise ValueError('retry replay ownership/resources conflict')
        if native_invocations is not None:
            for reference in prior['generated_inputs']:
                path = runtime.artifact_root / reference['relative_path']
                if (not path.resolve().is_relative_to(runtime.artifact_root)
                        or file_identity(path) != (reference['sha256'], reference['size_bytes'])):
                    raise ValueError('retained retry compiler input changed')
            context = dict(runtime.context, **prior['retry_context'])
            key = digest([component_id, operation_id])
            invocation = compile_component_retry_invocation(context, component_id=component_id,
                operation_id=operation_id, generation=prior['generation'],
                output_dir=prior['parent_snapshot']['output_dir'],
                working_directory=str(Path(context['working_directory']) / 'component-retries' / key),
                spawn_receipt=json.loads(Path(prior['native_parameters']['md_retry_spawn_receipt']).read_bytes()))
            if (list(invocation.command) != prior['command']
                    or invocation.execution_plan.plan_sha256 != prior['plan_sha256']
                    or [item.reference for item in invocation.generated_inputs] != prior['generated_inputs']):
                raise ValueError('retry compiler replay changed the authorized generation')
            native_invocations.append(invocation)
        return prior
    context = dict(runtime.context, resources=renewed_resources)
    replacement, receipt = prepare_replica_retry(runtime, component_id=component_id,
        operation_id=operation_id, failure_code=failure_code)
    generation = int((runtime.root_state() or {}).get('generation', 0)) + 1
    key = digest([component_id, operation_id])
    output = runtime.artifact_root / 'generations' / ('retry-' + key)
    work = Path(context['working_directory']) / 'component-retries' / key
    invocation = compile_component_retry_invocation(context, component_id=component_id,
        operation_id=operation_id, generation=generation, output_dir=str(output),
        working_directory=str(work), spawn_receipt=receipt)
    from types import SimpleNamespace
    from services.remote_execution.targets import selected_plan_target_resources
    selected = selected_plan_target_resources(SimpleNamespace(id=runtime.target_id),
        invocation.execution_plan, gpu_ids=renewed_resources['gpu_ids'], scratch_bytes=0)
    for budget in ('required', 'compute', 'coordinator_overlap'):
        if budget not in renewed_resources or any(selected[budget][key] > renewed_resources[budget][key]
                for key in ('cpus', 'memory_bytes')):
            raise ValueError('retry compilation exceeds the renewed admitted resource subdivision')
    if selected['minimum_gpu_memory_mb'] > renewed_resources.get('minimum_gpu_memory_mb', 0):
        raise ValueError('retry compilation exceeds the admitted physical GPU floor')
    parent = component_checkpoint_parent_snapshot(invocation, context)
    if native_invocations is not None:
        native_invocations.append(invocation)
    return runtime.retry_component(component_id, replacement=replacement,
        operation_id=operation_id, actor=actor, boot_id=boot_id, invocation=invocation,
        continuation_lease_id=continuation_lease_id, parent_snapshot=parent, resources=renewed_resources,
        retry_context={key: context[key] for key in
            ("parent", "native_runtime", "execution_plan", "plan_sha256", "resources", "generation") if key in context})


def run_component_workflow(context_path: Path) -> int:
    """Pump native root and serialized children on the same selected worker.

    The compiler and native workflow collectors remain scientific authorities.
    Exit zero exposes child bytes for native collection, never fabricated result
    references. Each native process owns a separate process group in this same
    session, so the worker's existing session-wide cancellation remains valid.
    """
    import signal
    import subprocess
    import time
    from component_runtime import NativeInvocation, canonical_bytes, durable_write
    from services.nextflow import compile_component_nextflow_invocation, component_native_parent_snapshot

    context_path = Path(context_path).resolve(strict=True)
    context = json.loads(context_path.read_text())
    command = context["root_command"]
    if not isinstance(command, list) or not command or any(type(x) is not str or "\x00" in x for x in command):
        raise ValueError("trusted root compiler argv is required")
    work = Path(context["working_directory"]).resolve(strict=True)
    os.environ["BMS_COMPONENT_CONTEXT"] = str(context_path)
    os.environ["APPTAINERENV_BMS_COMPONENT_CONTEXT"] = str(context_path)
    runtime = _runtime()
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    identity = _process_identity(os.getpid())
    owner = f"pid:{identity['pid']}:start:{identity['start_ticks']}"
    state_path = Path(context.get("root_state_path", str(context_path.with_suffix(".state.json"))))
    state_path.parent.mkdir(parents=True, exist_ok=True)
    prior = runtime.root_state() or {}
    generation = int(prior.get("generation", 0))
    edge = prior.get("continuation_edge")
    if edge:
        context = current_generation_context(context, prior)
        if prior.get("state") == "resume_ready":
            command = edge["command"]

    def publish(state: str, **detail: Any) -> None:
        if edge is not None:
            detail['continuation_edge'] = edge
        runtime.set_root_state(state, owner_id=owner, boot_id=boot, generation=generation, **detail)
        durable_write(state_path, canonical_bytes(dict(attempt_id=runtime.attempt_id,
            target_id=runtime.target_id, lease_id=runtime.lease_id, **runtime.root_state())))
        if state in {"completed", "failed", "cancelled", "paused"} and detail.get("quiescent"):
            runtime.publish_projection()

    if not runtime.claim_root(owner_id=owner, boot_id=boot):
        # Never overwrite a live predecessor's projection, relaunch an ambiguous
        # root, or convert a paused checkpoint into an automatic continuation.
        previous = runtime.root_state()
        if previous["state"] == "completed":
            return 0
        return 75 if previous["state"] == "paused" else 1

    output_root = Path(context.get("child_output_root", runtime.artifact_root / "components")).resolve()
    child_work_root = Path(context.get("child_work_root", work / "components")).resolve()
    if not output_root.is_relative_to(runtime.artifact_root):
        raise ValueError("child output root must remain in attempt artifacts")
    output_root.mkdir(parents=True, exist_ok=True)
    child_work_root.mkdir(parents=True, exist_ok=True)
    processes = []
    root = None
    active = []
    stopping = False
    old_handlers = {}

    def stop_requested(signum, frame):
        nonlocal stopping
        stopping = True

    for signum in (signal.SIGTERM, signal.SIGINT):
        old_handlers[signum] = signal.signal(signum, stop_requested)

    def start_native(argv, cwd, log_path, *, job_id=None, output_dir=None):
        environment = os.environ.copy()
        values = {'BMS_COMPONENT_CONTEXT': str(context_path),
                  'BMS_COMPONENT_JOB_ID': str(job_id or runtime.root_job_id),
                  'BMS_COMPONENT_OUTPUT_DIR': str(output_dir or context.get('parent', {}).get('output_dir', runtime.artifact_root))}
        for key, value in values.items():
            environment[key] = value
            environment['APPTAINERENV_' + key] = value
            environment['SINGULARITYENV_' + key] = value
        source_root = Path(__file__).resolve().parents[2]
        pythonpath = os.pathsep.join([str(source_root), str(_API), environment.get('PYTHONPATH', '')])
        environment['PYTHONPATH'] = pythonpath
        environment['APPTAINERENV_PYTHONPATH'] = pythonpath
        environment['SINGULARITYENV_PYTHONPATH'] = pythonpath
        # The ledger is an explicit task dependency, not a staged scientific
        # input. Bind its small attempt workspace at the same path in containers.
        directories = dict.fromkeys([context_path.parent, Path(context["ledger_path"]).parent, runtime.artifact_root])
        binds = ','.join(f'{path}:{path}' for path in directories)
        for key in ('APPTAINER_BINDPATH', 'SINGULARITY_BINDPATH'):
            environment[key] = ','.join(value for value in (environment.get(key), binds) if value)
        with log_path.open("ab") as log:
            try:
                process = subprocess.Popen(list(argv), cwd=cwd, env=environment,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, process_group=0)
            except OSError as exc:
                if job_id is not None:
                    # Popen itself rejected before returning a process: genuine
                    # launcher evidence, not a scientific nonzero-exit guess.
                    runtime.fail(str(job_id), owner_id=owner, boot_id=boot, quiescent=True,
                        reason="native process spawn rejected", failure_receipt=dict(
                            code="spawn_rejected", source="scheduler_launch",
                            message="native process spawn rejected"))
                raise RuntimeError("native process spawn rejected") from exc
        processes.append(process)
        process._bms_identity = _process_identity(process.pid)
        process._bms_writer_identity = dict(boot_id=boot, supervisor_pid=process.pid,
            supervisor_start_ticks=process._bms_identity['start_ticks'], component_scope=values)
        return process

    try:
        publish("starting", quiescent=False, owned_writers=[])
        root = start_native(resource_bound_command(command, None, context, state_path.parent), work, work / "component-root.log")
        publish("running", quiescent=False, owned_writers=[dict(_process_identity(root.pid), boot_id=boot)])
        while True:
            if stopping:
                runtime.request_cancel()
            try:
                runtime.check_active()
            except RuntimeError:
                quiet = _stop_processes(processes)
                publish("cancelled" if quiet else "uncertain", quiescent=quiet,
                        reason="cancellation requested; native writers reconciled" if quiet else "native writers remain")
                return 130

            for child_id, process, output_dir in tuple(active):
                code = process.poll()
                if code is not None and not _owned_group_writers(process):
                    runtime.execution_finished(child_id, owner_id=owner, boot_id=boot,
                        output_dir=str(output_dir), exit_code=code)
                    active.remove((child_id, process, output_dir))

            root_code = root.poll()
            if root_code is not None:
                if active or _owned_group_writers(root):
                    quiet = _stop_processes(processes)
                    publish("failed" if quiet else "uncertain", quiescent=quiet,
                            reason="root exited while native writers remained", exit_code=root_code)
                    return 1
                checkpoints = runtime.pending_checkpoints()
                if checkpoints and root_code == 0:
                    publish("paused", quiescent=True, exit_code=root_code,
                            checkpoint_ids=[row["checkpoint"]["checkpoint_id"] for row in checkpoints])
                    return 75
                if root_code == 0:
                    children = runtime.children()
                    if children:
                        runtime.join_children([row['job_id'] for row in children])
                state = "completed" if root_code == 0 else "failed"
                publish(state, quiescent=True, exit_code=root_code,
                        reason="native workflow collectors finished" if state == "completed" else "native workflow failed or children remain queued")
                return 0 if state == "completed" else 1

            pending = runtime.pending()
            if active:
                # Only descend into the active parent's declared children;
                # siblings stay serialized without a fleet of idle JVMs.
                pending = tuple(child for child in pending
                    if runtime.request(child).parent_job_id == active[-1][0])
            if pending:
                child_id = pending[0]
                child_dir = child_id.replace(":", "-")
                output_dir = output_root / child_dir
                child_work = child_work_root / child_dir
                output_dir.mkdir(parents=True, exist_ok=True)
                child_work.mkdir(parents=True, exist_ok=True)
                child_context = dict(context, child_id=child_id,
                    child_output_dir=str(output_dir), child_working_directory=str(child_work),
                    native_runtime=context.get('native_runtime', context.get('parent', {}).get('params', {})))
                request = runtime.request(child_id)
                if request.parent_job_id != runtime.root_job_id:
                    child_context['parent'] = runtime.native_parent(request.parent_job_id)
                launched = []

                def compile_native(request):
                    invocation = compile_component_nextflow_invocation(request, child_context)
                    if not isinstance(invocation, NativeInvocation):
                        raise ValueError("native compiler must return NativeInvocation")
                    runtime.bind_native_parent(child_id,
                        component_native_parent_snapshot(invocation, request, child_context),
                        owner_id=owner, boot_id=boot)
                    return invocation

                def launch_native(invocation, request, owner_runtime):
                    invocation.materialize_inputs(output_dir)
                    launched.append(start_native(resource_bound_command(invocation.command, invocation, child_context, child_work),
                        child_work, child_work / "component.log", job_id=child_id, output_dir=output_dir))

                try:
                    claimed = runtime.dispatch(child_id, owner_id=owner, boot_id=boot,
                        compile_native=compile_native, launch_native=launch_native)
                except Exception:
                    if runtime.child_status(child_id)["status"] == "failed":
                        # A compiler rejection is definitive, not an ambiguous
                        # launch. Existing native parent failure policy decides.
                        continue
                    raise
                if claimed:
                    active.append((child_id, launched[0], output_dir))
                    publish("running", quiescent=False, owned_writers=[
                        dict(_process_identity(p.pid), boot_id=boot)
                        for p in [root, *(row[1] for row in active)] if p.poll() is None])
            time.sleep(0.1)
    except BaseException:
        quiet = _stop_processes(processes)
        if (runtime.root_state() or {}).get("state") not in {"completed", "failed", "cancelled", "paused"}:
            publish("failed" if quiet else "uncertain", quiescent=quiet,
                    reason="component owner interrupted; durable claims retained")
        raise
    finally:
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Run the shared native component workflow owner")
    parser.add_argument("--context", required=True, type=Path)
    args = parser.parse_args()
    if not args.context.is_absolute():
        parser.error("--context must be an absolute trusted launch context")
    return run_component_workflow(args.context)


if __name__ == "__main__":
    raise SystemExit(main())
