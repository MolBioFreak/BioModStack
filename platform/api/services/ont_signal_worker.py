from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select, update

from database import (
    InputFile,
    Job,
    OntMoveTableSource,
    OntRawSignalRepresentation,
    OntSignalCalibrationArtifact,
    OntSignalCalibrationJob,
    OntSignalMappingArtifact,
    OntSignalMappingEvent,
    OntSignalMappingJob,
    OntSignalMappingProfile,
    OntSquigualiserViewJob,
)
from molbio_ngs_models import MolBioNGSReferenceArtifact, MolBioNGSReferenceRevision
from paths import get_molbio_ngs_reference_root, get_results_dir
from services import ngs_alignment_sessions, ont_raw_signal

logger = logging.getLogger(__name__)
LEASE_SECONDS = 300
MAX_FAILURE = 4000
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class OntSignalWorker:
    """Single-owner leased worker for move validation, reusable mapping, and bounded renders."""

    def __init__(self, session_factory: Any, domain_session_factory: Any, *, poll_interval: float = 5.0):
        self._session_factory = session_factory
        self._domain_session_factory = domain_session_factory
        self._poll_interval = max(1.0, poll_interval)
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._child: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        await self._recover_expired()
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="ont-signal-workbench-worker")

    async def stop(self) -> None:
        self._stop.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self._child is not None and self._child.returncode is None:
            self._child.terminate()
            await self._child.wait()
        self._child = None

    @staticmethod
    def _now() -> datetime:
        return datetime.utcnow()

    @staticmethod
    def _output_root() -> Path:
        return get_results_dir() / "ont_signal_workbench"

    @staticmethod
    def _runtime_identity() -> dict[str, str]:
        image = os.environ.get("BMS_ONT_SQUIGUALISER_IMAGE", "").strip()
        digest = os.environ.get("BMS_ONT_SQUIGUALISER_IMAGE_DIGEST", "").strip().lower()
        if image != f"sha256:{digest}" or not HEX64.fullmatch(digest):
            raise RuntimeError("pinned Squigualiser runtime identity is unavailable")
        return {
            "image": image,
            "image_digest": digest,
            "upstream_version": "0.7.0",
            "upstream_commit": "5a2404f1f43bc3227a85475c59b2b77970078b2e",
            "network": "none",
        }

    def _container_command(self, mounts: list[tuple[Path, str, bool]], arguments: list[str]) -> list[str]:
        identity = self._runtime_identity()
        runtime = os.environ.get("BMS_CONTAINER_RUNTIME", "podman").strip()
        if runtime not in {"podman", "docker"}:
            raise RuntimeError("unsupported container runtime")
        uid, gid = os.getuid(), os.getgid()
        if uid == 0 or gid == 0:
            uid = gid = 65534
        command = [
            runtime, "run", "--rm", "--network", "none", "--read-only", "--user", f"{uid}:{gid}",
            "--pids-limit", "128", "--memory", "4g", "--cpus", "4", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=512m",
        ]
        for source, target, writable in mounts:
            resolved = source.resolve(strict=True)
            command.extend(["--mount", f"type=bind,src={resolved},dst={target},{'rw' if writable else 'ro'}"])
        command.extend([
            identity["image"],
            "python3", "/opt/bms/ont_signal_runtime.py", *arguments,
        ])
        return command

    async def _execute(self, command: list[str], kind: str, item_id: str, claim_token: str) -> dict[str, Any]:
        self._child = await asyncio.create_subprocess_exec(
            *command, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/nonexistent"},
            start_new_session=True,
        )
        communication = asyncio.create_task(self._child.communicate())
        while not communication.done():
            try:
                await asyncio.wait_for(asyncio.shield(communication), timeout=60)
            except asyncio.TimeoutError:
                async with self._session_factory() as session:
                    table = {"move": OntMoveTableSource, "calibration": OntSignalCalibrationJob, "mapping": OntSignalMappingJob, "view": OntSquigualiserViewJob}[kind]
                    row = await session.get(table, item_id)
                    if row is None or row.claim_token != claim_token:
                        await session.rollback()
                        self._child.terminate()
                        await self._child.wait()
                        communication.cancel()
                        raise RuntimeError("signal-workbench lease was lost")
                    if getattr(row, "cancel_requested_at", None) is not None:
                        await session.rollback()
                        self._child.terminate()
                        await self._child.wait()
                        communication.cancel()
                        raise asyncio.CancelledError()
                    result = await session.execute(
                        update(table).where(table.id == item_id, table.claim_token == claim_token).values(lease_expires_at=self._now() + timedelta(seconds=LEASE_SECONDS))
                    )
                    if result.rowcount != 1:
                        await session.rollback()
                        self._child.terminate()
                        await self._child.wait()
                        communication.cancel()
                        raise RuntimeError("signal-workbench lease was lost")
                    await session.commit()
        stdout, stderr = await communication
        returncode = self._child.returncode
        self._child = None
        receipt = {
            "argv_sha256": hashlib.sha256("\0".join(command).encode()).hexdigest(),
            "returncode": returncode,
            "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
            "stderr_tail": stderr[-MAX_FAILURE:].decode("utf-8", "replace"),
        }
        if returncode != 0:
            raise RuntimeError(receipt["stderr_tail"] or "Squigualiser runtime failed")
        return receipt

    async def _recover_expired(self) -> None:
        now = self._now()
        async with self._session_factory() as session:
            for table, state_field in ((OntMoveTableSource, "validation_state"), (OntSignalCalibrationJob, "state"), (OntSignalMappingJob, "state"), (OntSquigualiserViewJob, "state")):
                rows = (await session.execute(select(table).where(
                    getattr(table, state_field) == "running", table.lease_expires_at.is_not(None), table.lease_expires_at < now,
                ))).scalars()
                for row in rows:
                    setattr(row, state_field, "requested")
                    row.reason_code = "expired_lease_recovered"
                    row.claim_token = None
                    row.lease_expires_at = None
                    if isinstance(row, OntSignalCalibrationJob):
                        row.stage_receipts = {**(row.stage_receipts or {}), "lease_recovery": {"recovered_at": now.isoformat(), "expired_attempt": row.attempt}}
                    if hasattr(row, "updated_at"): row.updated_at = now
            await session.commit()

    async def _claim(self, table: Any, state_field: str) -> tuple[str, str] | None:
        token = uuid.uuid4().hex
        async with self._session_factory() as session:
            row = (await session.execute(select(table).where(
                getattr(table, state_field) == "requested",
                table.claim_token.is_(None),
            ).order_by(table.created_at, table.id).limit(1))).scalar_one_or_none()
            if row is None:
                return None
            result = await session.execute(update(table).where(
                table.id == row.id, getattr(table, state_field) == "requested", table.claim_token.is_(None),
            ).values(**{
                state_field: "running", "reason_code": "worker_claimed", "claim_token": token,
                "lease_expires_at": self._now() + timedelta(seconds=LEASE_SECONDS),
                **({"attempt": row.attempt + 1, "updated_at": self._now()} if hasattr(row, "attempt") else {}),
            }))
            if result.rowcount != 1:
                await session.rollback()
                return None
            await session.commit()
            return str(row.id), token

    async def _fail(self, table: Any, state_field: str, item_id: str, token: str, exc: Exception) -> None:
        async with self._session_factory() as session:
            row = await session.get(table, item_id)
            if row is None or row.claim_token != token:
                return
            setattr(row, state_field, "failed")
            row.reason_code = "runtime_validation_failed"
            row.claim_token = None
            row.lease_expires_at = None
            if hasattr(row, "failure_code"): row.failure_code = exc.__class__.__name__
            if hasattr(row, "failure_message"): row.failure_message = str(exc)[:MAX_FAILURE]
            if hasattr(row, "updated_at"): row.updated_at = self._now()
            if hasattr(row, "completed_at"): row.completed_at = self._now()
            if isinstance(row, OntSignalCalibrationJob):
                row.stage_receipts = {**(row.stage_receipts or {}), "failure": {"failed_at": self._now().isoformat(), "failure_code": exc.__class__.__name__, "message_sha256": hashlib.sha256(str(exc).encode()).hexdigest()}}
            if isinstance(row, OntSignalMappingJob):
                session.add(OntSignalMappingEvent(id=f"ont-signal-event-{uuid.uuid4().hex}", job_id=row.id, state="failed", reason_code=row.reason_code, receipt={"error_class": exc.__class__.__name__}, created_at=self._now()))
            await session.commit()

    @staticmethod
    def _raw_paths(representation: OntRawSignalRepresentation) -> list[Path]:
        manifest = representation.artifact_manifest if isinstance(representation.artifact_manifest, dict) else {}
        paths = [Path(str(item["path"])) for item in manifest.get("artifacts", []) if isinstance(item, dict) and item.get("kind") == "blow5" and item.get("path")]
        if not paths:
            raise RuntimeError("ready BLOW5 representation has no governed artifacts")
        return paths

    async def _process_move(self, item_id: str, token: str) -> None:
        async with self._session_factory() as session:
            source = await session.get(OntMoveTableSource, item_id)
            if source is None or source.claim_token != token: return
            representation = await session.get(OntRawSignalRepresentation, source.raw_representation_id)
            tracked = await session.get(InputFile, source.input_file_id)
            if representation is None or tracked is None: raise RuntimeError("move-source parents disappeared")
            bam = Path(tracked.directory) / tracked.filename
            blow5 = self._raw_paths(representation)
            output = self._output_root() / "move-sources" / source.id
            output.mkdir(parents=True, exist_ok=False)
            (output / ".owner").write_text(source.id, encoding="utf-8")
            mounts = [(bam, "/parents/moves.bam", False), (output, "/output", True)]
            arguments = ["validate-moves", "--bam", "/parents/moves.bam", "--molecule-type", source.molecule_type, "--filtered-bam", "/output/filtered_moves.bam", "--inventory", "/output/read_inventory.txt", "--report", "/output/validation.json"]
            for index, path in enumerate(blow5):
                mounts.append((path, f"/parents/raw-{index}.blow5", False)); arguments.extend(["--blow5", f"/parents/raw-{index}.blow5"])
        command_receipt = await self._execute(self._container_command(mounts, arguments), "move", item_id, token)
        report = json.loads((output / "validation.json").read_text())
        async with self._session_factory() as session:
            source = await session.get(OntMoveTableSource, item_id)
            if source is None or source.claim_token != token: raise RuntimeError("move-source lease lost before publication")
            source.bam_header_sha256 = report["move_bam_header_sha256"]
            source.record_count = report["record_count"]; source.unique_read_count = report["unique_read_count"]
            source.mv_tag_count = report["tag_counts"]["mv"]; source.ts_tag_count = report["tag_counts"]["ts"]; source.ns_tag_count = report["tag_counts"]["ns"]
            source.basecall_model_id = report["basecall_model_id"]; source.read_inventory_sha256 = report["read_inventory_sha256"]
            source.validation_receipt = {**report, "command": command_receipt, "managed_outputs": {"filtered_move_bam": str(output / "filtered_moves.bam"), "read_inventory": str(output / "read_inventory.txt")}}
            source.validation_state = "ready"; source.reason_code = "move_source_exact_read_set_ready"
            source.claim_token = None; source.lease_expires_at = None; source.validated_at = self._now()
            await session.commit()

    async def _process_calibration(self, item_id: str, token: str) -> None:
        async with self._session_factory() as session:
            job = await session.get(OntSignalCalibrationJob, item_id)
            if job is None or job.claim_token != token: return
            if job.cancel_requested_at is not None: raise asyncio.CancelledError()
            source = await session.get(OntMoveTableSource, job.move_source_id)
            representation = await session.get(OntRawSignalRepresentation, job.raw_representation_id)
            if source is None or representation is None or source.validation_state != "ready" or representation.state != "ready":
                raise RuntimeError("calibration parents are not ready")
            outputs = source.validation_receipt.get("managed_outputs", {}) if isinstance(source.validation_receipt, dict) else {}
            filtered_bam = Path(str(outputs.get("filtered_move_bam", "")))
            blow5_paths = self._raw_paths(representation)
            output = self._output_root() / "calibrations" / job.id
            if output.exists():
                owner = output / ".owner"
                if not owner.is_file() or owner.read_text(encoding="utf-8") != job.id:
                    raise RuntimeError("calibration recovery output ownership is invalid")
                shutil.rmtree(output)
            output.mkdir(parents=True, exist_ok=False); (output / ".owner").write_text(job.id, encoding="utf-8")
            mounts = [(filtered_bam, "/parents/filtered_moves.bam", False), (output, "/output", True)]
            args = [
                "calibrate", "--filtered-bam", "/parents/filtered_moves.bam", "--sample-count", str(job.sample_count),
                "--raw-manifest-sha256", representation.manifest_sha256, "--move-artifact-sha256", source.artifact_sha256,
                "--move-inventory-sha256", str(source.read_inventory_sha256), "--basecall-model-id", str(source.basecall_model_id),
                "--output-dir", "/output", "--report", "/output/calibration.json",
            ]
            for index, path in enumerate(blow5_paths):
                adjacent = Path(f"{path}.idx")
                mounts.extend([(path, f"/parents/raw-{index}.blow5", False), (adjacent, f"/parents/raw-{index}.blow5.idx", False)])
                args.extend(["--blow5", f"/parents/raw-{index}.blow5"])
            parents = job.resource_snapshot.get("parents", {})
        command_receipt = await self._execute(self._container_command(mounts, args), "calibration", item_id, token)
        report_path = output / "calibration.json"
        if report_path.stat().st_size > 1024 * 1024:
            raise RuntimeError("calibration report exceeds bounded policy")
        report = json.loads(report_path.read_text())
        recommendation = report.get("recommendation", {})
        selected = report.get("sample_selection", {})
        tool = report.get("tool_identity", {})
        score_evidence = report.get("score_evidence")
        selected_ids = selected.get("read_ids")
        selection_sha = hashlib.sha256(json.dumps(selected_ids, sort_keys=True, separators=(",", ":")).encode()).hexdigest() if isinstance(selected_ids, list) else None
        if (
            report.get("schema") != "bms.ont-signal-calibration.v1"
            or report.get("parent_sha256s", {}).get("raw_manifest_sha256") != parents.get("raw_manifest_sha256")
            or report.get("parent_sha256s", {}).get("move_bam_sha256") != parents.get("move_bam_sha256")
            or report.get("basecall_model_id") != parents.get("basecall_model_id")
            or selected.get("requested_count") != job.sample_count or selected.get("selected_count") != job.sample_count
            or not isinstance(selected_ids, list) or len(selected_ids) != job.sample_count
            or len(set(selected_ids)) != job.sample_count or any(not isinstance(read_id, str) or not read_id for read_id in selected_ids)
            or selected.get("selection_sha256") != selection_sha
            or tool.get("version") != "0.7.0" or tool.get("commit") != "5a2404f1f43bc3227a85475c59b2b77970078b2e"
            or recommendation.get("kmer_length") not in range(1, 10)
            or recommendation.get("signal_move_offset") not in range(0, 9)
            or recommendation.get("kmer_length") != recommendation.get("signal_move_offset") + 1
            or not isinstance(score_evidence, list) or len(score_evidence) != 9
            or [item.get("candidate_signal_move_offset") for item in score_evidence if isinstance(item, dict)] != list(range(9))
            or any(not isinstance(item, dict) or item.get("read_count") != job.sample_count or not isinstance(item.get("score"), (int, float)) for item in score_evidence)
        ):
            raise RuntimeError("calibration report failed governed validation")
        artifact_sha = self._sha_file(report_path)
        async with self._session_factory() as session:
            job = await session.get(OntSignalCalibrationJob, item_id)
            if job is not None and job.claim_token == token and job.cancel_requested_at is not None:
                raise asyncio.CancelledError()
            if job is None or job.claim_token != token or job.calibration_artifact_id is not None:
                raise RuntimeError("calibration lease or single-publication invariant was lost")
            artifact = OntSignalCalibrationArtifact(
                id=f"ont-signal-calibration-artifact-{uuid.uuid4().hex}", raw_representation_id=job.raw_representation_id,
                move_source_id=job.move_source_id, basecall_model_id=report["basecall_model_id"],
                sample_selection=report["sample_selection"], recommended_kmer_length=recommendation["kmer_length"],
                recommended_signal_move_offset=recommendation["signal_move_offset"], score_evidence=report["score_evidence"],
                runtime_identity={**self._runtime_identity(), **tool}, parent_sha256s=report["parent_sha256s"],
                artifact_sha256=artifact_sha, created_at=self._now(),
            )
            session.add(artifact); await session.flush()
            job.calibration_artifact_id = artifact.id; job.state = "ready"; job.reason_code = "validated_calibration_ready"
            job.claim_token = None; job.lease_expires_at = None; job.updated_at = self._now(); job.completed_at = self._now()
            job.stage_receipts = {**(job.stage_receipts or {}), "runtime": command_receipt, "report_sha256": artifact_sha, "validation": report.get("validation", {})}
            await session.commit()

    @staticmethod
    def _alignment_authority(job: Job) -> dict[str, str]:
        params = job.params if isinstance(job.params, dict) else {}
        values = {"source_reference_sha256": params.get("reference_sequence_sha256"), "workflow_id": params.get("ont_workflow_id") or params.get("workflow_id"), "input_mode": params.get("ont_input_mode") or params.get("input_mode")}
        if not all(isinstance(value, str) and value for value in values.values()): raise RuntimeError("alignment job authority is incomplete")
        return {key: str(value) for key, value in values.items()}

    async def _process_mapping(self, item_id: str, token: str) -> None:
        async with self._session_factory() as session:
            job = await session.get(OntSignalMappingJob, item_id)
            if job is None or job.claim_token != token: return
            if job.cancel_requested_at is not None: raise asyncio.CancelledError()
            source = await session.get(OntMoveTableSource, job.move_source_id)
            profile = await session.get(OntSignalMappingProfile, job.mapping_profile_id)
            representation = await session.get(OntRawSignalRepresentation, job.raw_representation_id)
            if source is None or profile is None or representation is None or source.validation_state != "ready": raise RuntimeError("mapping parents are not ready")
            outputs = source.validation_receipt.get("managed_outputs", {})
            filtered_bam = Path(str(outputs.get("filtered_move_bam", ""))); inventory = Path(str(outputs.get("read_inventory", "")))
            output = self._output_root() / "mappings" / job.id
            output.mkdir(parents=True, exist_ok=False); (output / ".owner").write_text(job.id, encoding="utf-8")
            mounts = [(filtered_bam, "/parents/filtered_moves.bam", False), (inventory, "/parents/read_inventory.txt", False), (output, "/output", True)]
            if job.mode == "signal_to_read":
                args = ["reform", "--filtered-bam", "/parents/filtered_moves.bam", "--inventory", "/parents/read_inventory.txt", "--kmer-length", str(profile.kmer_length), "--signal-move-offset", str(profile.signal_move_offset), "--output", "/output/reform.paf", "--report", "/output/validation.json"]
                artifact_kind, artifact_path, media = "reform_paf", output / "reform.paf", "text/plain"
            else:
                parent_artifact = (await session.execute(select(OntSignalMappingArtifact).where(OntSignalMappingArtifact.mapping_job_id == job.parent_mapping_job_id, OntSignalMappingArtifact.kind == "reform_paf"))).scalar_one()
                alignment_job = await session.get(Job, job.alignment_job_id)
                if alignment_job is None: raise RuntimeError("alignment job disappeared")
                alignment_bam, alignment_meta, _, _ = ngs_alignment_sessions.resolve_session_alignment_bundle(alignment_job.id, str(job.alignment_session_id), **self._alignment_authority(alignment_job), job_output_dir=getattr(alignment_job, "child_output_dir", None) or alignment_job.output_dir)
                mounts.extend([(Path(parent_artifact.managed_relative_path), "/parents/reform.paf", False), (alignment_bam, "/parents/alignment.bam", False)])
                async with self._domain_session_factory() as domain_session:
                    revision = await domain_session.get(MolBioNGSReferenceRevision, job.reference_revision_id)
                    artifact = None if revision is None else await domain_session.get(MolBioNGSReferenceArtifact, revision.artifact_id)
                    if revision is None or artifact is None: raise RuntimeError("managed reference authority disappeared")
                    reference = get_molbio_ngs_reference_root() / artifact.managed_relative_path
                mounts.append((reference, "/parents/reference.fasta", False))
                args = ["realign", "--reform-paf", "/parents/reform.paf", "--alignment-bam", "/parents/alignment.bam", "--reference-fasta", "/parents/reference.fasta", "--output", "/output/realign.paf", "--report", "/output/validation.json"]
                artifact_kind, artifact_path, media = "realign_paf", output / "realign.paf", "text/plain"
            parent_identities = job.resource_snapshot.get("parents", {})
        command_receipt = await self._execute(self._container_command(mounts, args), "mapping", item_id, token)
        validation = json.loads((output / "validation.json").read_text())
        digest = self._sha_file(artifact_path)
        async with self._session_factory() as session:
            job = await session.get(OntSignalMappingJob, item_id)
            if job is None or job.claim_token != token: raise RuntimeError("mapping lease lost before publication")
            artifact = OntSignalMappingArtifact(
                id=f"ont-signal-artifact-{uuid.uuid4().hex}", mapping_job_id=job.id, kind=artifact_kind,
                managed_relative_path=str(artifact_path), media_type=media, sha256=digest, size_bytes=artifact_path.stat().st_size,
                parent_identities=parent_identities, runtime_identity=self._runtime_identity(), validation_receipt=validation,
                created_at=self._now(),
            )
            session.add(artifact)
            job.state = "ready"; job.reason_code = f"validated_{job.mode}_mapping_ready"; job.claim_token = None; job.lease_expires_at = None
            job.stage_receipts = {**(job.stage_receipts or {}), "runtime": command_receipt, "validation": validation}
            job.updated_at = self._now(); job.completed_at = self._now()
            session.add(OntSignalMappingEvent(id=f"ont-signal-event-{uuid.uuid4().hex}", job_id=job.id, state="ready", reason_code=job.reason_code, receipt={"artifact_sha256": digest, "runtime": self._runtime_identity()}, created_at=self._now()))
            await session.commit()

    @staticmethod
    def _sha_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024): digest.update(chunk)
        return digest.hexdigest()

    async def _process_view(self, item_id: str, token: str) -> None:
        async with self._session_factory() as session:
            view = await session.get(OntSquigualiserViewJob, item_id)
            if view is None or view.claim_token != token: return
            if view.cancel_requested_at is not None: raise asyncio.CancelledError()
            artifact = await session.get(OntSignalMappingArtifact, view.mapping_artifact_id)
            mapping = None if artifact is None else await session.get(OntSignalMappingJob, artifact.mapping_job_id)
            representation = None if mapping is None else await session.get(OntRawSignalRepresentation, mapping.raw_representation_id)
            if artifact is None or mapping is None or representation is None or mapping.state != "ready": raise RuntimeError("render parents are not ready")
            source = await session.get(OntMoveTableSource, mapping.move_source_id)
            if source is None or source.validation_state != "ready": raise RuntimeError("render move-source authority is not ready")
            source_outputs = source.validation_receipt.get("managed_outputs", {}) if isinstance(source.validation_receipt, dict) else {}
            filtered_moves = Path(str(source_outputs.get("filtered_move_bam", "")))
            if view.mode == "read":
                blow5, blow5_index = ont_raw_signal._validated_blow5_paths(representation, str(view.read_id))
                blow5_paths = [(blow5, blow5_index)]
            else:
                blow5_paths = [(path, Path(f"{path}.idx")) for path in self._raw_paths(representation)]
            output = self._output_root() / "views" / view.id
            output.mkdir(parents=True, exist_ok=False); (output / ".owner").write_text(view.id, encoding="utf-8")
            mounts = [(Path(artifact.managed_relative_path), "/parents/mapping.paf", False), (output, "/output", True)]
            args = ["render", "--mode", view.mode, "--mapping", "/parents/mapping.paf", "--output-dir", "/output", "--report", "/output/render_manifest.json"]
            for index, (path, adjacent_index) in enumerate(blow5_paths):
                mounts.extend([(path, f"/parents/raw-{index}.blow5", False), (adjacent_index, f"/parents/raw-{index}.blow5.idx", False)])
                args.extend(["--blow5", f"/parents/raw-{index}.blow5"])
            if view.mode == "read":
                mounts.append((filtered_moves, "/parents/filtered_moves.bam", False))
                args.extend(["--read-id", str(view.read_id), "--sequence-bam", "/parents/filtered_moves.bam"])
            else:
                async with self._domain_session_factory() as domain_session:
                    revision = await domain_session.get(MolBioNGSReferenceRevision, mapping.reference_revision_id)
                    reference_artifact = None if revision is None else await domain_session.get(MolBioNGSReferenceArtifact, revision.artifact_id)
                    if revision is None or reference_artifact is None: raise RuntimeError("render reference authority disappeared")
                    reference = get_molbio_ngs_reference_root() / reference_artifact.managed_relative_path
                mounts.append((reference, "/parents/reference.fasta", False))
                args.extend(["--reference-fasta", "/parents/reference.fasta", "--region", f"{view.reference_contig}:{view.reference_start}-{view.reference_end}"])
            params = view.render_params
            args.extend(["--strand", params["strand"], "--signal-units", params["signal_units"], "--scale", params["scale"], "--base-shift", str(params["base_shift_value"]), "--point-size", str(params["point_size"]), "--base-width", str(params["base_width"]), "--base-limit", str(params["base_limit"]), "--signal-sample-limit", str(params["signal_sample_limit"]), "--pileup-read-limit", str(params["pileup_read_limit"])])
            for key, flag in (("fixed_width", "--fixed-width"), ("loose_bound", "--loose-bound"), ("show_samples", "--show-samples"), ("show_base_colours", "--show-base-colours"), ("remove_signal_outliers", "--remove-signal-outliers")):
                if params.get(key): args.append(flag)
            managed_bed_id = params.get("managed_bed_artifact_id")
            if managed_bed_id:
                bed = await session.get(InputFile, str(managed_bed_id))
                if bed is None or not bed.filename.lower().endswith(".bed"): raise RuntimeError("managed BED authority is unavailable or has the wrong media type")
                bed_path = Path(bed.directory) / bed.filename
                mounts.append((bed_path, "/parents/annotation.bed", False)); args.extend(["--bed", "/parents/annotation.bed"])
        command_receipt = await self._execute(self._container_command(mounts, args), "view", item_id, token)
        manifest = json.loads((output / "render_manifest.json").read_text())
        for item in manifest["artifacts"]: item["managed_relative_path"] = str(output / item.pop("filename"))
        async with self._session_factory() as session:
            view = await session.get(OntSquigualiserViewJob, item_id)
            if view is None or view.claim_token != token: raise RuntimeError("view lease lost before publication")
            view.output_manifest = manifest; view.render_receipt = {**(view.render_receipt or {}), "runtime": self._runtime_identity(), "command": command_receipt}
            view.state = "ready"; view.reason_code = "bounded_squigualiser_view_ready"; view.claim_token = None; view.lease_expires_at = None; view.updated_at = self._now(); view.completed_at = self._now()
            await session.commit()

    async def _cancel_claim(self, table: Any, state_field: str, item_id: str, token: str) -> None:
        async with self._session_factory() as session:
            row = await session.get(table, item_id)
            if row is not None and row.claim_token == token:
                setattr(row, state_field, "cancelled"); row.reason_code = "cancelled"; row.claim_token = None; row.lease_expires_at = None
                if isinstance(row, OntSignalCalibrationJob):
                    row.stage_receipts = {**(row.stage_receipts or {}), "cancellation": {**((row.stage_receipts or {}).get("cancellation", {})), "completed_at": self._now().isoformat(), "disposition": "cancelled"}}
                if hasattr(row, "updated_at"): row.updated_at = self._now()
                if hasattr(row, "completed_at"): row.completed_at = self._now()
                await session.commit()

    async def _run(self) -> None:
        while not self._stop.is_set():
            work = None
            for table, field, kind, handler in (
                (OntMoveTableSource, "validation_state", "move", self._process_move),
                (OntSignalCalibrationJob, "state", "calibration", self._process_calibration),
                (OntSignalMappingJob, "state", "mapping", self._process_mapping),
                (OntSquigualiserViewJob, "state", "view", self._process_view),
            ):
                claimed = await self._claim(table, field)
                if claimed is None: continue
                work = True; item_id, token = claimed
                try:
                    await handler(item_id, token)
                except asyncio.CancelledError:
                    if self._stop.is_set(): raise
                    await self._cancel_claim(table, field, item_id, token)
                except Exception as exc:
                    logger.exception("ONT signal workbench %s failed: %s", kind, item_id)
                    await self._fail(table, field, item_id, token, exc)
                break
            if work is None:
                try: await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval)
                except asyncio.TimeoutError: pass
