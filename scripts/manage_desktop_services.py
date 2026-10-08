#!/usr/bin/env python3
from __future__ import annotations

import sys

# First executable boundary, regardless of option/action ordering. Python startup
# has already happened: callers requiring no startup bytecode must use -B.
sys.dont_write_bytecode = True

import argparse
import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_lifecycle() -> None:
    # Preserve the importable legacy CLI surface, but do not resolve lifecycle
    # configuration during standalone bootstrap (malformed XDG must be JSON).
    global API_LOG, CORE_RUNTIME_LOG, FRONTEND_LOG, ServiceManagerError
    global resolve_runtime_mode, restart_all, restart_api, runtime_descriptor
    global start_api, start_all, start_runtime_target, status_lines, stop_api, stop_all
    global TailnetEnvironmentError, select_tailnet_environment
    from biomodstack_services import (  # noqa: E402
        API_LOG,
        CORE_RUNTIME_LOG,
        FRONTEND_LOG,
        ServiceManagerError,
        resolve_runtime_mode,
        restart_all,
        restart_api,
        runtime_descriptor,
        start_api,
        start_all,
        start_runtime_target,
        status_lines,
        stop_api,
        stop_all,
    )
    from biomodstack_tailnet import (  # noqa: E402
        TailnetEnvironmentError,
        select_tailnet_environment,
    )


if __name__ != "__main__":
    _load_lifecycle()


NOTIFY_ICON = "applications-science"


def notify(message: str, icon: str = NOTIFY_ICON) -> None:
    import subprocess

    try:
        subprocess.run(
            ["notify-send", "BioModStack", message, "-i", icon],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage BioModStack desktop services")
    parser.add_argument(
        "action",
        choices=["start", "start-api", "start-target", "stop", "stop-api", "restart", "restart-api", "status", "python-plan", "python-bootstrap", "python-verify", "frontend-plan", "frontend-bootstrap", "frontend-verify", "discover", "plan", "provision-plan", "provision", "verify", "configure-preview", "configure", "recover", "resume"],
    )
    parser.add_argument(
        "--runtime",
        choices=["dev", "container"],
        help="runtime mode to manage (defaults to BMS_RUNTIME_MODE or container)",
    )
    parser.add_argument("--notify", action="store_true", help="send desktop notifications")
    parser.add_argument("--json", action="store_true", dest="json_output", help="emit structured JSON for supported actions")
    parser.add_argument("--target", choices=["dev", "prod", "both"], help="runtime target for start-target")
    parser.add_argument("--model", action="append", default=[], help="reviewed model dependency selection for discover/plan; repeatable")
    parser.add_argument("--document", type=Path, help="versioned install JSON for configure-preview")
    parser.add_argument("--operation-id", help="expected durable configure operation identity")
    parser.add_argument("--expect-document-sha256", help="reject a stale input before writing")
    parser.add_argument("--expect-plan-sha256", help="expected provision-plan identity; also selects provision resume")
    parser.add_argument("--accept-license", action="append", default=[], help="explicit acceptance of a reviewed license ID; repeatable, recorded durably")
    parser.add_argument("--runtime-attestation", type=Path, help="existing Protenix observed attestation for offline verify; not approval")
    args = parser.parse_args()
    from biomodstack_python_prerequisites import (
        ACTIONS, SETUP_ACTIONS, prerequisite_report, dispatch_setup,
    )
    if args.action.startswith("frontend-"):
        from biomodstack_frontend_prerequisites import ACTIONS, prerequisite_report
    if args.action in ACTIONS:
        if any((args.runtime, args.notify, args.target, args.model, args.document,
                args.operation_id, args.expect_document_sha256, args.expect_plan_sha256,
                args.accept_license, args.runtime_attestation)):
            parser.error("Dependency prerequisites accept only --json; use BMS_PYTHON_ROOT/BMS_FRONTEND_ROOT for external state")
        report = prerequisite_report(args.action, project_root=REPO_ROOT)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["status"] in {"planned", "installed", "already-installed", "verified"} else 3
    if args.action in SETUP_ACTIONS or args.action in {"start", "start-api", "start-target", "restart", "restart-api", "status"}:
        try:
            dispatch_setup(REPO_ROOT)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            print(json.dumps({"schema_version": "bms.python-prerequisites.v1",
                              "action": args.action, "status": "blocked", "ready": False,
                              "errors": [{"code": getattr(exc, "code", "prerequisite_failed"),
                                          "message": str(exc)}]}, indent=2))
            return 3
    if args.runtime_attestation and (args.action != "verify" or args.model != ["protenix"]):
        parser.error("runtime-attestation requires verify with exactly one --model protenix")
    if args.action == "verify" and args.accept_license:
        parser.error("verify cannot accept licenses")

    if args.action in {"provision-plan", "provision", "verify"} or (args.action == "resume" and args.expect_plan_sha256):
        if args.notify or args.target or args.runtime or args.document or args.expect_document_sha256:
            parser.error("provisioning uses configured stores; runtime/target/document/notify are unsupported")
        if args.action == "provision-plan" and (args.operation_id or args.accept_license or args.expect_plan_sha256):
            parser.error("provision-plan is read-only and does not accept operation/license/expected identity")
        from biomodstack_provision import provision_report
        report = provision_report(args.action, project_root=REPO_ROOT, models=tuple(args.model),
                                  operation_id=args.operation_id, expected_plan_digest=args.expect_plan_sha256,
                                  accepted_licenses=tuple(args.accept_license),
                                  runtime_attestation=args.runtime_attestation)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["status"] in {"planned", "bytes-materialized"} else 3
    if args.expect_plan_sha256 or args.accept_license:
        parser.error("plan identity/license acceptance require provision or provision resume")

    if args.action in {"configure", "recover", "resume"}:
        if args.notify or args.target or args.runtime or args.model:
            parser.error("configuration does not accept runtime/model/target/notify")
        from biomodstack_configuration import configuration_report
        report = configuration_report(args.action, project_root=REPO_ROOT, document=args.document,
                                      operation_id=args.operation_id,
                                      expect_document_sha256=args.expect_document_sha256)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["configured"] else 3
    if args.operation_id or args.expect_document_sha256:
        parser.error("operation identity/input digest require configure/recover/resume")

    if args.action == "configure-preview":
        if not args.document or args.notify or args.target or args.runtime or args.model:
            parser.error("configure-preview requires --document; runtime/model/target/notify are unsupported")
        from biomodstack_install_document import preview_report, render_preview
        report = preview_report(args.document, project_root=REPO_ROOT)
        print(json.dumps(report, indent=2, sort_keys=True) if args.json_output else render_preview(report))
        return 0 if report["valid"] else 2
    if args.document:
        parser.error("--document is only supported with configure-preview")

    if args.action in {"discover", "plan"}:
        if args.notify or args.target:
            parser.error("bootstrap is read-only; --notify and --target are unsupported")
        from biomodstack_bootstrap import bootstrap_report, render_report, BLOCKED_EXIT
        report = bootstrap_report(args.action, project_root=REPO_ROOT,
                                  runtime=args.runtime, models=tuple(args.model))
        print(json.dumps(report, indent=2, sort_keys=True) if args.json_output else render_report(report))
        return 0 if report["ready"] else BLOCKED_EXIT
    if __name__ == "__main__":
        _load_lifecycle()

    if args.model:
        parser.error("--model is only supported with discover/plan")

    if args.json_output and args.action != "status":
        parser.error("--json is only supported with the status action")

    try:
        runtime_mode = resolve_runtime_mode(args.runtime)

        if args.action == "start-target":
            target = args.target or "prod"
            if args.notify:
                notify(f"🚀 Starting BioModStack {target} runtime target…")
            start_runtime_target(target=target)
            if target == "dev":
                select_tailnet_environment("development")
            elif target == "prod":
                select_tailnet_environment("production")
            if args.notify:
                notify("✅ BioModStack requested runtime target is running")
            print(f"Started runtime target: {target}")
            return 0

        if args.action == "start":
            if args.notify:
                notify("🚀 Starting BioModStack services…")
            start_all(runtime_mode=runtime_mode)
            if args.notify:
                notify("✅ BioModStack services are running")
            print("\n".join(status_lines(runtime_mode=runtime_mode)))
            return 0

        if args.action == "start-api":
            if args.notify:
                notify("🚀 Starting BioModStack API…")
            start_api(runtime_mode=runtime_mode)
            if args.notify:
                notify("✅ BioModStack API started")
            if runtime_mode == "container":
                print(f"Core runtime log: {CORE_RUNTIME_LOG}")
            else:
                print(f"API log: {API_LOG}")
            return 0

        if args.action == "stop":
            if args.notify:
                notify("🛑 Stopping BioModStack services…", icon="dialog-warning")
            stop_all(runtime_mode=runtime_mode)
            if args.notify:
                notify("✅ BioModStack services stopped", icon="dialog-information")
            print("Stopped BioModStack services")
            return 0

        if args.action == "stop-api":
            if args.notify:
                notify("🛑 Stopping BioModStack API…", icon="dialog-warning")
            stop_api(runtime_mode=runtime_mode)
            if args.notify:
                notify("✅ BioModStack API stopped", icon="dialog-information")
            print("Stopped BioModStack API")
            return 0

        if args.action == "restart":
            if args.notify:
                notify("♻️ Restarting BioModStack services…", icon="view-refresh")
            restart_all(runtime_mode=runtime_mode)
            if args.notify:
                notify("✅ BioModStack services restarted", icon="dialog-information")
            print("\n".join(status_lines(runtime_mode=runtime_mode)))
            return 0

        if args.action == "restart-api":
            if args.notify:
                notify("🔄 Restarting BioModStack API…", icon="view-refresh")
            restart_api(runtime_mode=runtime_mode)
            if args.notify:
                notify("✅ BioModStack API restarted", icon="dialog-information")
            if runtime_mode == "container":
                print(f"Core runtime log: {CORE_RUNTIME_LOG}")
            else:
                print(f"API log: {API_LOG}")
            return 0

        if args.action == "status":
            if args.json_output:
                print(json.dumps(runtime_descriptor(runtime_mode=runtime_mode), indent=2, sort_keys=True))
                return 0
            lines = status_lines(runtime_mode=runtime_mode)
            print("\n".join(lines))
            if runtime_mode == "container":
                fallback_log_line = f"Core runtime log: {CORE_RUNTIME_LOG}"
                fallback_log_path = str(CORE_RUNTIME_LOG)
            else:
                fallback_log_line = f"Frontend log: {FRONTEND_LOG}"
                fallback_log_path = str(FRONTEND_LOG)
            if not any(line.rstrip().endswith(fallback_log_path) for line in lines):
                print(fallback_log_line)
            return 0
    except (
        ServiceManagerError,
        TailnetEnvironmentError,
        FileNotFoundError,
        OSError,
        subprocess.CalledProcessError,
    ) as exc:
        if args.notify:
            notify(f"❌ {exc}", icon="dialog-error")
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
