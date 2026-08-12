from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest


SCRIPT = Path(__file__).with_name("finalize_small_igv_report.py")


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("finalize_small_igv_report", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _governed_url(role: str, digest: str) -> str:
    return f"/api/jobs/job-123/alignment-session-artifacts/primary/{role}/{digest * 64}"


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, str]]:
    roles = [
        "reference",
        "reference_index",
        "alignment",
        "alignment_index",
        "coverage_depth",
        "position_gradient",
        "gc_content",
        "gc_zscore",
        "split_read_density",
        "soft_clip_density",
        "junction_hotspots",
    ]
    urls = {role: _governed_url(role, format(index, "x")[-1]) for index, role in enumerate(roles)}
    reference_config = tmp_path / "reference.json"
    reference_config.write_text(
        json.dumps({"fastaURL": urls["reference"], "indexURL": urls["reference_index"]}),
        encoding="utf-8",
    )
    track_roles = [
        ("alignment", "alignment_index"),
        ("coverage_depth",),
        ("position_gradient",),
        ("gc_content",),
        ("gc_zscore",),
        ("split_read_density",),
        ("soft_clip_density",),
        ("junction_hotspots",),
    ]
    tracks = []
    for index, role_pair in enumerate(track_roles):
        track = {"name": f"Track {index}", "url": urls[role_pair[0]]}
        if len(role_pair) == 2:
            track["indexURL"] = urls[role_pair[1]]
        tracks.append(track)
    track_config = tmp_path / "tracks.json"
    track_config.write_text(json.dumps(tracks), encoding="utf-8")
    report = tmp_path / "report.html"
    options = {
        "reference": {
            "fastaURL": urls["reference"],
            "indexURL": urls["reference_index"],
        },
        "tracks": json.loads(track_config.read_text(encoding="utf-8")),
    }
    report.write_text(
        "<html>\n<script>\n        var options = "
        + json.dumps(options)
        + "\n</script>\n</html>\n",
        encoding="utf-8",
    )
    return report, reference_config, track_config, urls


def _write_options(report: Path, reference_config: Path, track_config: Path) -> None:
    reference = json.loads(reference_config.read_text(encoding="utf-8"))
    tracks = json.loads(track_config.read_text(encoding="utf-8"))
    options = {"reference": {"fastaURL": reference["fastaURL"]}, "tracks": tracks}
    report.write_text(
        "<html>\n<script>\n        var options = "
        + json.dumps(options)
        + "\n</script>\n</html>\n",
        encoding="utf-8",
    )


def test_rewrites_exact_local_reference_placeholders_to_governed_urls(tmp_path: Path) -> None:
    module = _load_module()
    report, reference_config, track_config, urls = _inputs(tmp_path)
    lines = report.read_text(encoding="utf-8").splitlines(keepends=True)
    option_index = next(index for index, line in enumerate(lines) if line.lstrip().startswith("var options = "))
    options = json.loads(lines[option_index].lstrip()[len("var options = "):])
    options["reference"] = {"fastaURL": "reference_qc.fasta"}
    lines[option_index] = "        var options = " + json.dumps(options) + "\n"
    report.write_text("".join(lines), encoding="utf-8")

    module.finalize_report(
        report=report,
        reference_config=reference_config,
        track_config=track_config,
        max_bytes=1024 * 1024,
        generated_reference_fasta="reference_qc.fasta",
        generated_reference_index="reference_qc.fasta.fai",
    )

    finalized = report.read_text(encoding="utf-8")
    assert "reference_qc.fasta" not in finalized
    assert urls["reference"] in finalized
    assert urls["reference_index"] in finalized


def test_rejects_unexpected_local_reference_placeholder(tmp_path: Path) -> None:
    module = _load_module()
    report, reference_config, track_config, urls = _inputs(tmp_path)
    report.write_text(
        report.read_text(encoding="utf-8").replace(urls["reference"], "wrong-reference.fasta"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="reference does not match generated inputs"):
        module.finalize_report(
            report=report,
            reference_config=reference_config,
            track_config=track_config,
            max_bytes=1024 * 1024,
            generated_reference_fasta="reference_qc.fasta",
            generated_reference_index="reference_qc.fasta.fai",
        )


def test_injects_governed_reference_index_and_accepts_only_declared_urls(tmp_path: Path) -> None:
    module = _load_module()
    report, reference_config, track_config, urls = _inputs(tmp_path)

    module.finalize_report(
        report=report,
        reference_config=reference_config,
        track_config=track_config,
        max_bytes=1024 * 1024,
    )

    text = report.read_text(encoding="utf-8")
    assert "data:" not in text
    assert ";base64," not in text
    assert urls["reference_index"] in text
    options_line = next(line for line in text.splitlines() if "var options = " in line)
    options = json.loads(options_line.split("var options = ", 1)[1])
    assert options["reference"] == {
        "fastaURL": urls["reference"],
        "indexURL": urls["reference_index"],
    }


def test_rejects_embedded_or_undeclared_resources(tmp_path: Path) -> None:
    module = _load_module()
    report, reference_config, track_config, _urls = _inputs(tmp_path)
    original = report.read_text(encoding="utf-8")

    report.write_text(original.replace("</html>", "data:application/gzip;base64,AAAA</html>"), encoding="utf-8")
    with pytest.raises(ValueError, match="embedded data URI"):
        module.finalize_report(
            report=report,
            reference_config=reference_config,
            track_config=track_config,
            max_bytes=1024 * 1024,
        )

    report.write_text(original.replace('"tracks": [', '"tracks": [{"url": "https://example.invalid/x"}, '), encoding="utf-8")
    with pytest.raises(ValueError, match="do not match declared inputs"):
        module.finalize_report(
            report=report,
            reference_config=reference_config,
            track_config=track_config,
            max_bytes=1024 * 1024,
        )

    for injected in (
        '<script src="https://example.invalid/igv.js"></script>',
        '<link rel="stylesheet" href="/tmp/report.css">',
        '<img src="file:///tmp/plot.png">',
        '<iframe src="/unmanaged/viewer"></iframe>',
        '<style>body { background-image: url(https://example.invalid/bg.png); }</style>',
        '<object data=https://example.invalid/object></object>',
        '<form><button formaction=https://example.invalid/post></button></form>',
        '<meta http-equiv="refresh" content="0;url=https://example.invalid/refresh">',
        '<script>fetch("https://example.invalid/dynamic")</script>',
    ):
        report.write_text(original.replace("</html>", f"{injected}</html>"), encoding="utf-8")
        with pytest.raises(ValueError, match="undeclared HTML resource"):
            module.finalize_report(
                report=report,
                reference_config=reference_config,
                track_config=track_config,
                max_bytes=1024 * 1024,
            )


def test_rejects_case_variant_embedded_data_uri(tmp_path: Path) -> None:
    module = _load_module()
    report, reference_config, track_config, _urls = _inputs(tmp_path)
    report.write_text(
        report.read_text(encoding="utf-8").replace("</html>", "DATA:application/gzip;BASE64,AAAA</html>"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="embedded data URI"):
        module.finalize_report(
            report=report,
            reference_config=reference_config,
            track_config=track_config,
            max_bytes=1024 * 1024,
        )


def test_rejects_missing_or_duplicate_resource_roles(tmp_path: Path) -> None:
    module = _load_module()
    report, reference_config, track_config, urls = _inputs(tmp_path)
    tracks = json.loads(track_config.read_text(encoding="utf-8"))
    tracks.pop()
    track_config.write_text(json.dumps(tracks), encoding="utf-8")
    _write_options(report, reference_config, track_config)

    with pytest.raises(ValueError, match="exact governed resource inventory"):
        module.finalize_report(
            report=report,
            reference_config=reference_config,
            track_config=track_config,
            max_bytes=1024 * 1024,
        )

    report, reference_config, track_config, _urls = _inputs(tmp_path)
    tracks = json.loads(track_config.read_text(encoding="utf-8"))
    tracks[-1]["url"] = urls["soft_clip_density"]
    track_config.write_text(json.dumps(tracks), encoding="utf-8")
    _write_options(report, reference_config, track_config)
    with pytest.raises(ValueError, match="exact governed resource inventory"):
        module.finalize_report(
            report=report,
            reference_config=reference_config,
            track_config=track_config,
            max_bytes=1024 * 1024,
        )


def test_rejects_mixed_job_or_mode_resources(tmp_path: Path) -> None:
    module = _load_module()
    report, reference_config, track_config, urls = _inputs(tmp_path)
    tracks = json.loads(track_config.read_text(encoding="utf-8"))
    tracks[1]["url"] = urls["coverage_depth"].replace("/job-123/", "/job-other/")
    track_config.write_text(json.dumps(tracks), encoding="utf-8")
    _write_options(report, reference_config, track_config)

    with pytest.raises(ValueError, match="one job and mode"):
        module.finalize_report(
            report=report,
            reference_config=reference_config,
            track_config=track_config,
            max_bytes=1024 * 1024,
        )

    report, reference_config, track_config, urls = _inputs(tmp_path)
    tracks = json.loads(track_config.read_text(encoding="utf-8"))
    tracks[1]["url"] = urls["coverage_depth"].replace("/primary/", "/dimer_candidates/")
    track_config.write_text(json.dumps(tracks), encoding="utf-8")
    _write_options(report, reference_config, track_config)
    with pytest.raises(ValueError, match="one job and mode"):
        module.finalize_report(
            report=report,
            reference_config=reference_config,
            track_config=track_config,
            max_bytes=1024 * 1024,
        )


def test_rejects_oversized_or_ambiguous_report(tmp_path: Path) -> None:
    module = _load_module()
    report, reference_config, track_config, _urls = _inputs(tmp_path)
    original = report.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="exceeds size limit"):
        module.finalize_report(
            report=report,
            reference_config=reference_config,
            track_config=track_config,
            max_bytes=16,
        )

    options_line = next(line for line in original.splitlines() if "var options = " in line)
    report.write_text(original.replace(options_line, options_line + "\n" + options_line), encoding="utf-8")
    with pytest.raises(ValueError, match="exactly one options object"):
        module.finalize_report(
            report=report,
            reference_config=reference_config,
            track_config=track_config,
            max_bytes=1024 * 1024,
        )
