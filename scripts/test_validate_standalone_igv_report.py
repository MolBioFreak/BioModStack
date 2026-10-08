from __future__ import annotations

import base64
import gzip
import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).with_name("validate_standalone_igv_report.py")
CSP = (
    "default-src 'none'; base-uri 'none'; form-action 'none'; object-src 'none'; "
    "frame-src 'none'; child-src 'none'; connect-src data: blob:; img-src data: blob:; "
    "media-src data: blob:; font-src data:; script-src 'unsafe-inline' blob:; "
    "style-src 'unsafe-inline'; worker-src blob:"
)
IGV_JS = "window.igv={createBrowser:async()=>({loadSession:async()=>{}})};//opaque/apI/payload"
CONTROLLER_TEMPLATE = """
const tableJson = "@TABLE_JSON@"
const sessionDictionary = "@SESSION_DICTIONARY@"
let igvBrowser
igv.createBrowser(document.body,{loadDefaultGenomes:false,search:false});
"""


def _gzip_uri(payload: bytes) -> str:
    encoded = base64.b64encode(gzip.compress(payload, mtime=0)).decode()
    return "data:application/gzip;base64," + encoded


FASTA_GZIP = _gzip_uri(b">chr1\nAC\n")
BAM_GZIP = _gzip_uri(b"BAM\x01")


def _session(locus: str = "chr1:1-2", *, document: object | None = None) -> str:
    if document is None:
        document = {
            "locus": locus,
            "reference": {"fastaURL": FASTA_GZIP},
            "tracks": [
                {
                    "name": "reads",
                    "type": "alignment",
                    "format": "bam",
                    "url": BAM_GZIP,
                    "showCoverage": True,
                    "showSoftClips": True,
                    "showMismatches": True,
                    "showAllBases": True,
                    "showInsertionText": True,
                    "displayMode": "EXPANDED",
                    "visibilityWindow": -1,
                    "height": 500,
                    "order": 1,
                }
            ],
        }
    payload = json.dumps(document, separators=(",", ":")).encode()
    encoded = base64.b64encode(gzip.compress(payload, mtime=0)).decode()
    return "data:application/gzip;base64," + encoded


def _table(*, row_id: object = 0, chrom: object = "chr1", start: object = 1, end: object = 2) -> dict[str, object]:
    return {
        "headers": ["unique_id", "Chrom", "Start", "End", "Name"],
        "rows": [[row_id, chrom, start, end, "site"]],
    }


def _controller(
    *,
    table: object | None = None,
    sessions: object | None = None,
    suffix: str = "",
) -> str:
    if table is None:
        table = _table()
    if sessions is None:
        sessions = {"0": _session()}
    return (
        "\nconst tableJson = "
        + json.dumps(table, separators=(",", ":"))
        + "\nconst sessionDictionary = "
        + json.dumps(sessions, separators=(",", ":"))
        + "\nlet igvBrowser\n"
        + "igv.createBrowser(document.body,{loadDefaultGenomes:false,search:false});\n"
        + suffix
    )


def _template() -> str:
    return (
        "<!doctype html><html><head>"
        f"<meta http-equiv='Content-Security-Policy' content=\"{CSP}\">"
        "<script src='file:///opt/bms/igv-reports/igv.min.js'></script>"
        f"</head><body><script>{CONTROLLER_TEMPLATE}</script></body></html>"
    )


def _report(
    *,
    head_extra: str = "",
    body_extra: str = "",
    igv_js: str = IGV_JS,
    controller: str | None = None,
) -> str:
    if controller is None:
        controller = _controller()
    return (
        "<!doctype html><html><head>"
        f"<meta http-equiv='Content-Security-Policy' content=\"{CSP}\">"
        f"{head_extra}<script>\n{igv_js}</script>"
        f"</head><body>{body_extra}<script>{controller}</script></body></html>"
    )


def _run(tmp_path: Path, payload: str, max_bytes: int = 8192) -> subprocess.CompletedProcess[str]:
    report = tmp_path / "report.html"
    template = tmp_path / "template.html"
    igv_js = tmp_path / "igv.min.js"
    report.write_text(payload, encoding="utf-8")
    template.write_text(_template(), encoding="utf-8")
    igv_js.write_text(IGV_JS, encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--report",
            str(report),
            "--max-bytes",
            str(max_bytes),
            "--template",
            str(template),
            "--igv-js",
            str(igv_js),
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def test_accepts_exact_network_silent_standalone_igv_report(tmp_path: Path) -> None:
    result = _run(tmp_path, _report())
    assert result.returncode == 0, result.stderr
    assert "standalone IGV report valid" in result.stdout


def test_rejects_active_or_nested_resource_bypasses(tmp_path: Path) -> None:
    payloads = (
        _report(body_extra="<img srcset='https://example.invalid/x.png 1x'>"),
        _report(body_extra="<div style=\"background:url('https://example.invalid/x.png')\"></div>"),
        _report(head_extra="<style>@import url('https://example.invalid/x.css');</style>"),
        _report(head_extra="<meta http-equiv='refresh' content='0; url=/api/jobs/job-1'>"),
        _report(body_extra="<iframe srcdoc=\"<img src='https://example.invalid/x.png'>\"></iframe>"),
        _report(body_extra="<iframe src='data:text/html,&lt;script src=https://example.invalid/x.js&gt;&lt;/script&gt;'></iframe>"),
        _report(body_extra="<img src='data:image/png;base64,AA==' onerror=\"fetch('https://example.invalid/x')\">"),
        _report(body_extra="<svg><image xlink:href='https://example.invalid/x.png'></image></svg>"),
    )
    for payload in payloads:
        result = _run(tmp_path, payload)
        assert result.returncode != 0, payload
        assert "external, active, or host-bound resource" in result.stderr


def test_rejects_missing_csp_and_executable_script_drift(tmp_path: Path) -> None:
    without_csp = _report().replace(f"<meta http-equiv='Content-Security-Policy' content=\"{CSP}\">", "")
    payloads = (
        without_csp,
        _report(body_extra="<script>fetch('https://example.invalid/x')</script>"),
        _report(igv_js=IGV_JS + ";fetch('https://example.invalid/x')"),
        _report(controller=_controller(suffix="fetch('https://example.invalid/x')")),
    )
    for payload in payloads:
        assert _run(tmp_path, payload).returncode != 0


def test_rejects_active_markup_in_dynamic_table_values(tmp_path: Path) -> None:
    tables = (
        {"headers": ["unique_id", "<img src=x onerror=fetch(1)>", "Start", "End", "Name"], "rows": [[0, "chr1", 1, 2, "site"]]},
        _table(chrom="<iframe srcdoc=bad>"),
    )
    for table in tables:
        assert _run(tmp_path, _report(controller=_controller(table=table))).returncode != 0


def test_rejects_malformed_or_unclosed_session_dictionary(tmp_path: Path) -> None:
    malformed = (
        {"0": "data:application/gzip;base64,%%%"},
        {"0": "data:application/gzip;base64,QUJD"},
        {"0": _gzip_uri(b"")},
        {},
        {"0": _session(), "1": _session()},
    )
    for sessions in malformed:
        assert _run(tmp_path, _report(controller=_controller(sessions=sessions))).returncode != 0


def test_rejects_duplicate_or_invalid_table_row_ids(tmp_path: Path) -> None:
    tables = (
        {"headers": ["unique_id", "Chrom", "Start", "End", "Name"], "rows": [[0, "chr1", 1, 2, "a"], [0, "chr1", 1, 2, "b"]]},
        _table(row_id="0"),
    )
    for table in tables:
        assert _run(tmp_path, _report(controller=_controller(table=table))).returncode != 0


def test_rejects_corrupt_or_open_ended_nested_resources(tmp_path: Path) -> None:
    corrupt = "data:application/gzip;base64,H4sIAAAAAAAAAAAAAAAAAAAA"
    base_track = _session_document()["tracks"][0]
    documents = (
        {**_session_document(), "reference": {"fastaURL": corrupt}},
        {**_session_document(), "tracks": [{**base_track, "url": corrupt}]},
        {**_session_document(), "reference": {"fastaURL": FASTA_GZIP, "indexURL": "https://example.invalid/ref.fai"}},
    )
    for document in documents:
        payload = _report(controller=_controller(sessions={"0": _session(document=document)}))
        assert _run(tmp_path, payload).returncode != 0


def _session_document() -> dict[str, object]:
    return json.loads(gzip.decompress(base64.b64decode(_session().split(",", 1)[1], validate=True)))


def test_rejects_script_attribute_drift(tmp_path: Path) -> None:
    payloads = (
        _report().replace("<script>\n" + IGV_JS, "<script type='text/plain'>\n" + IGV_JS),
        _report().replace("<script>\nconst tableJson", "<script type='text/plain'>\nconst tableJson"),
    )
    for payload in payloads:
        assert _run(tmp_path, payload).returncode != 0


def test_rejects_c1_controls_and_noninitial_or_mismatched_rows(tmp_path: Path) -> None:
    cases = (
        (_table(chrom="chr1\u007f"), {"0": _session()}),
        (_table(chrom="chr1\u0085"), {"0": _session()}),
        (_table(row_id=1), {"1": _session()}),
        (_table(chrom="chr2", start=10, end=20), {"0": _session()}),
    )
    for table, sessions in cases:
        payload = _report(controller=_controller(table=table, sessions=sessions))
        assert _run(tmp_path, payload).returncode != 0


def test_template_renders_dynamic_table_values_as_text() -> None:
    template = Path(__file__).parents[1] / "templates/ngs/igv_variant_standalone.html"
    text = template.read_text(encoding="utf-8")
    assert "cell.textContent = headers[j]" in text
    assert "cell.textContent = rowData[j]" in text
    assert "cell.innerHTML" not in text


def test_rejects_oversized_report(tmp_path: Path) -> None:
    result = _run(tmp_path, _report(), max_bytes=8)
    assert result.returncode != 0
    assert "exceeds size limit" in result.stderr
