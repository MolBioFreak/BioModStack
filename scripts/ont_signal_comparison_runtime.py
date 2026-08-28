#!/usr/bin/env python3
"""Bounded network-silent Squigualiser two-track comparison renderer."""
from __future__ import annotations

import argparse
import array
from contextlib import contextmanager
import hashlib
import html
from html.parser import HTMLParser
import json
import os
import re
import selectors
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Iterator, Sequence

MAX_HTML_BYTES = 48 * 1024 * 1024
MAX_TOTAL_OUTPUT_BYTES = 64 * 1024 * 1024
MAX_BROKER_MESSAGE_BYTES = 256 * 1024
MAX_BROKER_PARENTS = 32
MAX_COMBINED_LOG_BYTES = 8 * 1024 * 1024
CSS_EXTERNAL_RESOURCE = re.compile(
    r"(?:@import\s+(?:url\()?|url\()\s*['\"]?(?:https?:)?//", re.IGNORECASE
)


class ActiveResourceAudit(HTMLParser):
    """Reject active network markup while allowing inert inline-script URL text."""

    RESOURCE_ATTRIBUTES = {
        "script": ("src",), "link": ("href",), "img": ("src", "srcset"),
        "source": ("src", "srcset"), "video": ("src", "poster"),
        "audio": ("src",), "track": ("src",), "form": ("action",),
        "base": ("href",), "image": ("href", "xlink:href"),
        "use": ("href", "xlink:href"),
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.violations: list[str] = []
        self._in_style = False

    @staticmethod
    def _local(value: str) -> bool:
        normalized = value.strip().lower()
        return not normalized or normalized.startswith(("data:", "blob:", "#"))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        values = {name.lower(): value or "" for name, value in attrs}
        if lowered == "style":
            self._in_style = True
        if lowered in {"iframe", "object", "embed"}:
            self.violations.append(lowered)
        if lowered == "meta" and values.get("http-equiv", "").lower() == "refresh":
            self.violations.append("meta-refresh")
        for attribute in self.RESOURCE_ATTRIBUTES.get(lowered, ()):
            value = values.get(attribute)
            if value is not None and not self._local(value):
                self.violations.append(f"{lowered}[{attribute}]")
        inline_style = values.get("style")
        if inline_style and CSS_EXTERNAL_RESOURCE.search(inline_style):
            self.violations.append(f"{lowered}[style]")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "style":
            self._in_style = False

    def handle_data(self, data: str) -> None:
        if self._in_style and CSS_EXTERNAL_RESOURCE.search(data):
            self.violations.append("style-resource")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_bytes(json.dumps(value, indent=2, sort_keys=True).encode() + b"\n")


def build_plot_tracks_argv(commands_file: str, output_dir: str) -> list[str]:
    for value in (commands_file, output_dir):
        if value != "/output" and not value.startswith("/output/") and not value.startswith("/tmp/"):
            raise ValueError("comparison artifact path is outside governed runtime roots")
        if ".." in Path(value).parts:
            raise ValueError("comparison artifact path contains traversal")
    return [
        "squigualiser", "plot_tracks", "--shared_x", "--auto_height",
        "--tag_name", "comparison", "-f", commands_file, "-o", output_dir,
    ]


def write_plot_tracks_commands(path: Path, commands: list[list[str]]) -> dict[str, Any]:
    if len(commands) != 2:
        raise ValueError("comparison requires exactly two plot tracks")
    lines = ["num_commands=2", "plot_heights=*,*"]
    command_hashes: list[str] = []
    for index, command in enumerate(commands, start=1):
        if command[:2] != ["squigualiser", "plot_pileup"]:
            raise ValueError("comparison track command must use plot_pileup")
        tokens = command[1:]
        if any(not token or any(character.isspace() for character in token) for token in tokens):
            raise ValueError("comparison track command contains unsupported whitespace")
        lines.append(f"{index} " + " ".join(tokens))
        command_hashes.append(hashlib.sha256("\0".join(command).encode()).hexdigest())
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"schema": "bms.ont-comparison-track-commands.v1", "argv_sha256s": command_hashes}


def selected_read_fasta(bam_path: Path, read_id: str, output_path: Path) -> None:
    try:
        import pysam  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("pysam is required to resolve governed real-read sequence") from exc
    matches = 0
    with pysam.AlignmentFile(str(bam_path), "rb", check_sq=False) as bam, output_path.open("w", encoding="ascii") as target:
        for record in bam.fetch(until_eof=True):
            if record.query_name != read_id:
                continue
            sequence = (record.query_sequence or "").upper()
            if not sequence or re.fullmatch(r"[ACGTN]+", sequence) is None:
                raise ValueError("selected real read lacks a valid governed sequence")
            target.write(f">{read_id}\n{sequence}\n")
            matches += 1
    if matches != 1:
        raise ValueError("selected real read must resolve exactly once in move/sequence authority")


def bounded_real_reference_mapping(
    parent: Path,
    read_id: str,
    work_dir: Path,
    contig: str,
    start: int,
    end: int,
) -> tuple[Path, dict[str, Any]]:
    """Materialize the one governed real-read PAF record used by this render."""
    try:
        import pysam  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("pysam is required to bound the real reference mapping") from exc
    if start < 1 or end < start or not Path(f"{parent}.tbi").is_file():
        raise ValueError("real reference mapping requires an indexed bounded region")
    plain = work_dir / "real_selected.paf"
    matches = 0
    with pysam.TabixFile(str(parent)) as source, plain.open("w", encoding="utf-8") as target:
        for line in source.fetch(contig, start - 1, end):
            fields = line.split("\t")
            if len(fields) < 12 or fields[0] != read_id:
                continue
            target.write(f"{line}\n")
            matches += 1
    if matches != 1:
        raise ValueError("real reference mapping must resolve the selected read exactly once")
    compressed = work_dir / "real_selected.paf.gz"
    pysam.tabix_compress(str(plain), str(compressed), force=True)
    pysam.tabix_index(
        str(compressed), seq_col=5, start_col=7, end_col=8,
        zerobased=True, force=True,
    )
    index = Path(f"{compressed}.tbi")
    if not compressed.is_file() or not index.is_file():
        raise RuntimeError("bounded real reference mapping lacks its exact index")
    return compressed, {
        "selected_read_id": read_id,
        "region": f"{contig}:{start}-{end}",
        "parent_sha256": sha(parent),
        "output_sha256": sha(compressed),
        "index_sha256": sha(index),
    }


def indexed_simulated_reference_mapping(
    parent: Path, work_dir: Path
) -> tuple[Path, dict[str, Any]]:
    """Create the renderer-required indexed working form of simulator truth PAF."""
    try:
        import pysam  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("pysam is required to index simulated reference truth") from exc
    lines = parent.read_text(encoding="ascii").splitlines()
    if len(lines) != 1 or len(lines[0].split("\t")) < 12:
        raise ValueError("simulated normalized PAF must contain exactly one record")
    plain = work_dir / "simulated_selected.paf"
    plain.write_text(f"{lines[0]}\n", encoding="ascii")
    compressed = work_dir / "simulated_selected.paf.gz"
    pysam.tabix_compress(str(plain), str(compressed), force=True)
    pysam.tabix_index(
        str(compressed), seq_col=5, start_col=7, end_col=8,
        zerobased=True, force=True,
    )
    index = Path(f"{compressed}.tbi")
    if not compressed.is_file() or not index.is_file():
        raise RuntimeError("indexed simulated reference mapping is incomplete")
    return compressed, {
        "parent_sha256": sha(parent),
        "output_sha256": sha(compressed),
        "index_sha256": sha(index),
    }


def _fasta_id(path: Path) -> str:
    lines = path.read_text(encoding="ascii").splitlines()
    if len(lines) < 2 or not lines[0].startswith(">") or any(line.startswith(">") for line in lines[1:]):
        raise ValueError("simulated FASTA must contain exactly one record")
    read_id = lines[0][1:].split()[0]
    if not read_id:
        raise ValueError("simulated FASTA read identity is empty")
    return read_id


def run_bounded_command(
    command: list[str], *, timeout: float, log_limit_bytes: int,
    parent_fds: tuple[int, ...] = (),
) -> subprocess.CompletedProcess[bytes]:
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        pass_fds=parent_fds,
        start_new_session=True,
    )
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    streams = {"stdout": bytearray(), "stderr": bytearray()}
    observed = 0
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            for key, _mask in selector.select(min(remaining, 0.1)):
                chunk = os.read(key.fd, 64 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    getattr(key.fileobj, "close")()
                    continue
                observed += len(chunk)
                if observed > log_limit_bytes:
                    raise RuntimeError("comparison renderer command log ceiling exceeded")
                streams[key.data].extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command, timeout)
        returncode = process.wait(timeout=remaining)
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        raise
    finally:
        selector.close()
        for pipe in (process.stdout, process.stderr):
            if not pipe.closed:
                pipe.close()
    return subprocess.CompletedProcess(
        command, returncode, bytes(streams["stdout"]), bytes(streams["stderr"])
    )


def _run(
    command: list[str], timeout: int = 600, *, parent_fds: tuple[int, ...] = ()
) -> dict[str, Any]:
    completed = run_bounded_command(
        command,
        timeout=timeout,
        log_limit_bytes=MAX_COMBINED_LOG_BYTES,
        parent_fds=parent_fds,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-4096:].decode("utf-8", "replace") or "Squigualiser comparison command failed")
    return {
        "argv_sha256": hashlib.sha256("\0".join(command).encode()).hexdigest(),
        "returncode": completed.returncode,
        "stdout_sha256": hashlib.sha256(completed.stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(completed.stderr).hexdigest(),
    }


def _one_html(directory: Path) -> Path:
    candidates = [path for path in directory.iterdir() if path.is_file() and not path.is_symlink() and path.suffix.lower() == ".html"]
    if len(candidates) != 1:
        raise ValueError("Squigualiser track stage must emit exactly one HTML file")
    if not 1 <= candidates[0].stat().st_size <= MAX_HTML_BYTES:
        raise ValueError("Squigualiser track HTML violates bounded policy")
    return candidates[0]


def _render_options(command: list[str], *, orientation: str, molecule_type: str,
                    kmer_length: int, base_shift: int, params: dict[str, Any]) -> None:
    command.extend(["--kmer_length", str(kmer_length), "--base_shift", str(base_shift),
                    "--base_limit", str(params["base_limit"]),
                    "--sig_plot_limit", str(params["signal_sample_limit"])])
    if molecule_type == "rna":
        command.append("--rna")
    elif molecule_type != "dna":
        raise ValueError("comparison molecule type is invalid")
    if orientation == "reverse":
        command.append("--plot_reverse")
    elif orientation != "forward":
        raise ValueError("comparison orientation is invalid")
    scale = params.get("scale", "none")
    if scale != "none":
        if scale not in {"medmad", "znorm"}:
            raise ValueError("comparison scale is unsupported")
        command.extend(["--sig_scale", scale])
    point_size = float(params.get("point_size", 0.5))
    if point_size != 0.5:
        if not point_size.is_integer():
            raise ValueError("Squigualiser accepts only 0.5 or integer point size")
        command.extend(["--point_size", str(int(point_size))])
    if params.get("fixed_width"):
        command.extend(["--fixed_width", "--base_width", str(params["base_width"])])
    if not params.get("show_samples", True):
        command.append("--no_samples")
    if not params.get("show_base_colours", True):
        command.append("--no_colours")
    if params.get("remove_signal_outliers"):
        command.append("--remove_signal_outliers")


def _inject_labels(path: Path, *, real_label: str, simulated_label: str) -> None:
    text = path.read_text(encoding="utf-8")
    banner = (
        '<div id="bms-comparison-authority" style="font:600 14px sans-serif;padding:10px;'
        'border:2px solid #8a5b00;background:#fff4d6;color:#2d2100">'
        f'<div>{html.escape(real_label)}</div><div>{html.escape(simulated_label)}</div>'
        '<div>Simulated signal is model-derived from the selected reference and profile. '
        'It is not instrument-acquired evidence.</div></div>'
    )
    insertion = text.lower().find("<body")
    if insertion >= 0:
        close = text.find(">", insertion)
        text = text[:close + 1] + banner + text[close + 1:]
    else:
        text = banner + text
    path.write_text(text, encoding="utf-8")


def validate_comparison_html(path: Path, *, real_read_id: str, profile_id: str) -> dict[str, int | str]:
    info = path.lstat()
    if path.is_symlink() or not path.is_file() or not 1 <= info.st_size <= MAX_HTML_BYTES:
        raise RuntimeError("comparison HTML violates bounded regular-file policy")
    raw = path.read_bytes()
    text = raw.decode("utf-8", "strict")
    audit = ActiveResourceAudit()
    try:
        audit.feed(text)
        audit.close()
    except Exception as exc:
        raise RuntimeError("comparison HTML markup is malformed") from exc
    if audit.violations or "file://" in text.lower():
        raise RuntimeError("comparison HTML contains an external active resource")
    required = (
        f"REAL · INSTRUMENT ACQUIRED · {real_read_id}",
        f"SIMULATED IDEAL · SQUIGULATOR 0.5.0 · {profile_id}",
        "Simulated signal is model-derived from the selected reference and profile. It is not instrument-acquired evidence.",
    )
    if not all(label in text for label in required) or "Bokeh" not in text:
        raise RuntimeError("comparison HTML lacks visible governed plot labels")
    return {"sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": info.st_size}


def render_comparison(*, output: Path, real_blow5: Path, real_mapping: Path, real_moves: Path,
                      reference_fasta: Path, simulated_blow5: Path, simulated_fasta: Path,
                      simulated_mapping: Path, real_read_id: str, profile_id: str, contig: str,
                      start: int, end: int, orientation: str, molecule_type: str,
                      real_kmer_length: int, simulated_kmer_length: int,
                      base_shift: int, render_params: dict[str, Any],
                      parent_fds: tuple[int, ...] = ()) -> dict[str, Any]:
    reference_sha256 = sha(reference_fasta)
    if not real_mapping.name.endswith(".gz") or not Path(f"{real_mapping}.tbi").is_file():
        raise ValueError("real mapping requires its exact adjacent tabix index")
    for blow5 in (real_blow5, simulated_blow5):
        if not Path(f"{blow5}.idx").is_file():
            raise ValueError("comparison BLOW5 requires its exact adjacent index")
    simulated_read_id = _fasta_id(simulated_fasta)
    output.mkdir(parents=True, exist_ok=True)
    receipts: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="bms-comparison-render-") as temporary:
        work = Path(temporary)
        real_sequence = work / "real_read.fasta"
        selected_read_fasta(real_moves, real_read_id, real_sequence)
        bounded_real_mapping, bounded_mapping_receipt = bounded_real_reference_mapping(
            real_mapping, real_read_id, work, contig, start, end
        )
        receipts["real_mapping_subset"] = bounded_mapping_receipt
        indexed_simulated_mapping, simulated_mapping_receipt = indexed_simulated_reference_mapping(
            simulated_mapping, work
        )
        receipts["simulated_mapping_index"] = simulated_mapping_receipt
        real_command = ["squigualiser", "plot_pileup", "--file", str(reference_fasta),
                        "--slow5", str(real_blow5), "--alignment", str(bounded_real_mapping),
                        "--region", f"{contig}:{start}-{end}", "--plot_limit", "1"]
        _render_options(real_command, orientation=orientation, molecule_type=molecule_type,
                        kmer_length=real_kmer_length, base_shift=base_shift, params=render_params)
        simulated_command = ["squigualiser", "plot_pileup", "--file", str(reference_fasta),
                             "--slow5", str(simulated_blow5), "--alignment", str(indexed_simulated_mapping),
                             "--region", f"{contig}:{start}-{end}", "--plot_limit", "1"]
        _render_options(simulated_command, orientation=orientation, molecule_type=molecule_type,
                        kmer_length=simulated_kmer_length, base_shift=0, params=render_params)
        commands_file = work / "plot_tracks.commands"
        command_receipt = write_plot_tracks_commands(commands_file, [real_command, simulated_command])
        receipts["real_track"] = {"execution": "embedded_in_plot_tracks", "argv_sha256": command_receipt["argv_sha256s"][0], "kmer_length": real_kmer_length}
        receipts["simulated_track"] = {"execution": "embedded_in_plot_tracks", "argv_sha256": command_receipt["argv_sha256s"][1], "kmer_length": simulated_kmer_length}
        track_command = build_plot_tracks_argv(str(commands_file), str(output))
        receipts["plot_tracks"] = {**_run(track_command, parent_fds=parent_fds), "track_commands": command_receipt}
    real_label = f"REAL · INSTRUMENT ACQUIRED · {real_read_id}"
    simulated_label = f"SIMULATED IDEAL · SQUIGULATOR 0.5.0 · {profile_id}"
    final = output / "comparison.html"
    _inject_labels(final, real_label=real_label, simulated_label=simulated_label)
    html_receipt = validate_comparison_html(final, real_read_id=real_read_id, profile_id=profile_id)
    total = sum(path.stat().st_size for path in output.iterdir() if path.is_file())
    if total > MAX_TOTAL_OUTPUT_BYTES:
        raise RuntimeError("comparison renderer total output exceeds bounded policy")
    return {
        "schema": "bms.ont-comparison-render-receipt.v1",
        "stage_order": ["real_track", "simulated_track", "plot_tracks"],
        "commands": receipts, "comparison_html": html_receipt,
        "selected_read_id": real_read_id, "generated_read_id": simulated_read_id,
        "region": {"contig": contig, "start": start, "end": end},
        "parent_sha256s": {
            "real_blow5_sha256": sha(real_blow5), "real_blow5_index_sha256": sha(Path(f"{real_blow5}.idx")),
            "real_mapping_sha256": sha(real_mapping), "real_mapping_index_sha256": sha(Path(f"{real_mapping}.tbi")),
            "real_moves_sha256": sha(real_moves), "simulated_blow5_sha256": sha(simulated_blow5),
            "reference_fasta_sha256": reference_sha256,
            "simulated_blow5_index_sha256": sha(Path(f"{simulated_blow5}.idx")),
            "simulated_fasta_sha256": sha(simulated_fasta), "simulated_mapping_sha256": sha(simulated_mapping),
        },
    }


class BrokerParents:
    def __init__(self, metadata: dict[str, Any], descriptors: list[int]):
        self.metadata, self.descriptors = metadata, descriptors
        self.temp = tempfile.TemporaryDirectory(prefix="bms-comparison-parents-")
        self.root = Path(self.temp.name); self.paths: dict[str, Path] = {}
        for item, descriptor in zip(metadata["parents"], descriptors, strict=True):
            alias = str(item.get("alias", ""))
            if not alias or Path(alias).name != alias:
                raise ValueError("broker parent alias is invalid")
            digest = hashlib.sha256(); offset = 0
            while chunk := os.pread(descriptor, 1024 * 1024, offset):
                digest.update(chunk); offset += len(chunk)
            if digest.hexdigest() != item.get("sha256") or offset != item.get("size_bytes"):
                raise ValueError("broker parent digest authority diverged")
            path = self.root / alias; path.symlink_to(f"/proc/self/fd/{descriptor}"); self.paths[alias] = path

    def close(self) -> None:
        for descriptor in self.descriptors:
            os.close(descriptor)
        self.temp.cleanup()


@contextmanager
def receive(socket_path: Path, timeout: float) -> Iterator[tuple[dict[str, Any], BrokerParents]]:
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(socket_path)); server.listen(1); server.settimeout(timeout)
        connection, _ = server.accept()
        with connection:
            payload, ancillary, flags, _ = connection.recvmsg(
                MAX_BROKER_MESSAGE_BYTES + 1,
                socket.CMSG_SPACE(MAX_BROKER_PARENTS * array.array("i").itemsize),
            )
        if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
            raise ValueError("broker request was truncated")
        descriptors: list[int] = []
        for level, kind, data in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                values = array.array("i"); values.frombytes(data[:len(data) - len(data) % values.itemsize]); descriptors.extend(values)
        metadata = json.loads(payload)
        if metadata.get("schema") != "bms.ont-signal-fd-broker.v1" or len(descriptors) != len(metadata.get("parents", [])):
            raise ValueError("broker request schema or descriptor count is invalid")
        parents = BrokerParents(metadata, descriptors)
        try:
            yield metadata, parents
        finally:
            parents.close()
    finally:
        server.close()


def _parse_render_params(raw: str) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("render parameters must be an object")
    allowed = {"scale", "point_size", "fixed_width", "base_width", "base_limit",
               "signal_sample_limit", "show_samples", "show_base_colours", "remove_signal_outliers"}
    if set(value) - allowed:
        raise ValueError("render parameters contain unsupported fields")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation")
    render = sub.add_parser("render")
    for name in ("real-blow5", "real-mapping", "real-moves", "reference-fasta", "simulated-blow5", "simulated-fasta", "simulated-mapping"):
        render.add_argument(f"--{name}", type=Path, required=True)
    render.add_argument("--producer-manifest", type=Path, required=True)
    render.add_argument("--real-read-id", required=True); render.add_argument("--profile-id", required=True)
    render.add_argument("--contig", required=True); render.add_argument("--start", type=int, required=True); render.add_argument("--end", type=int, required=True)
    render.add_argument("--orientation", choices=("forward", "reverse"), required=True)
    render.add_argument("--molecule-type", choices=("dna", "rna"), required=True)
    render.add_argument("--real-kmer-length", type=int, required=True)
    render.add_argument("--simulated-kmer-length", type=int, required=True)
    render.add_argument("--base-shift", type=int, required=True)
    render.add_argument("--render-params-json", required=True)
    broker = sub.add_parser("broker"); broker.add_argument("--socket", type=Path, required=True); broker.add_argument("--timeout-seconds", type=float, default=30)
    return parser


def main(argv: Sequence[str] | None = None, *, parent_fds: tuple[int, ...] = ()) -> int:
    args = _parser().parse_args(argv)
    if args.operation == "render":
        receipt = render_comparison(output=Path("/output"), real_blow5=args.real_blow5,
            real_mapping=args.real_mapping, real_moves=args.real_moves, reference_fasta=args.reference_fasta,
            simulated_blow5=args.simulated_blow5, simulated_fasta=args.simulated_fasta,
            simulated_mapping=args.simulated_mapping, real_read_id=args.real_read_id,
            profile_id=args.profile_id, contig=args.contig, start=args.start, end=args.end,
            orientation=args.orientation, molecule_type=args.molecule_type,
            real_kmer_length=args.real_kmer_length,
            simulated_kmer_length=args.simulated_kmer_length,
            base_shift=args.base_shift, render_params=_parse_render_params(args.render_params_json),
            parent_fds=parent_fds)
        producer_manifest = json.loads(args.producer_manifest.read_text(encoding="utf-8"))
        artifacts = [item for item in producer_manifest.get("artifacts", []) if item.get("kind") != "producer_manifest"]
        artifacts.append({"kind": "comparison_html", "filename": "comparison.html", "media_type": "text/html",
                          **receipt["comparison_html"], "validation_receipt": receipt})
        manifest = {"schema": "bms.ont-signal-comparison-manifest.v1", "artifacts": artifacts,
                    "parents": receipt["parent_sha256s"], "producer": producer_manifest,
                    "renderer": receipt}
        write_json(Path("/output/comparison_manifest.json"), manifest)
        return 0
    if args.operation == "broker":
        with receive(args.socket, args.timeout_seconds) as (metadata, parents):
            operation = list(metadata["operation_argv"])
            if not operation or operation[0] != "render":
                raise ValueError("renderer broker operation is invalid")
            translated = [str(parents.paths[value.removeprefix("/parents/")]) if isinstance(value, str) and value.startswith("/parents/") else value for value in operation]
            return main(translated, parent_fds=tuple(parents.descriptors))
    _parser().print_help(); return 0


if __name__ == "__main__":
    raise SystemExit(main())
