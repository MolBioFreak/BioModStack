from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "build_fastq_igv_tracks.py"


def test_build_fastq_igv_tracks_smoke_generates_report_artifacts(tmp_path: Path) -> None:
    reference = tmp_path / "ref.fa"
    reference.write_text(">plasmid\nACGTACGTAC\n", encoding="utf-8")
    coverage = tmp_path / "coverage.tsv"
    coverage.write_text(
        "reference\tposition\tdepth\n"
        "plasmid\t1\t1\n"
        "plasmid\t2\t2\n"
        "plasmid\t3\t3\n"
        "plasmid\t4\t4\n"
        "plasmid\t5\t5\n"
        "plasmid\t6\t5\n"
        "plasmid\t7\t4\n"
        "plasmid\t8\t3\n"
        "plasmid\t9\t2\n"
        "plasmid\t10\t1\n",
        encoding="utf-8",
    )
    fake_samtools = tmp_path / "fake_samtools.py"
    fake_samtools.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if len(sys.argv) >= 2 and sys.argv[1] == 'view':\n"
        "    print('read_split\\t0\\tplasmid\\t1\\t60\\t4M2N4M\\t*\\t0\\t0\\tACGTACGT\\tIIIIIIII')\n"
        "    print('read_soft\\t0\\tplasmid\\t3\\t60\\t2S5M1S\\t*\\t0\\t0\\tTTGTACG\\tIIIIIII')\n"
        "    raise SystemExit(0)\n"
        "print('unexpected fake_samtools args: ' + ' '.join(sys.argv[1:]), file=sys.stderr)\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    fake_samtools.chmod(0o755)

    outputs = {
        "coverage_depth": tmp_path / "igv_coverage_depth.bedgraph",
        "position_gradient": tmp_path / "igv_position_gradient.bedgraph",
        "gc_content": tmp_path / "igv_gc_content.bedgraph",
        "gc_zscore": tmp_path / "igv_gc_zscore.bedgraph",
        "split_density": tmp_path / "igv_split_read_density.bedgraph",
        "softclip_density": tmp_path / "igv_softclip_density.bedgraph",
        "junction_hotspots": tmp_path / "igv_junction_hotspots.bed",
        "report_sites_bed": tmp_path / "igv_report_sites.bed",
        "report_sites_tsv": tmp_path / "igv_report_sites.tsv",
    }

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--bam",
            str(tmp_path / "aligned.bam"),
            "--reference-fasta",
            str(reference),
            "--coverage-tsv",
            str(coverage),
            "--samtools-cmd",
            str(fake_samtools),
            "--window-bp",
            "5",
            "--hotspot-max",
            "3",
            "--out-coverage-depth",
            str(outputs["coverage_depth"]),
            "--out-position-gradient",
            str(outputs["position_gradient"]),
            "--out-gc-content",
            str(outputs["gc_content"]),
            "--out-gc-zscore",
            str(outputs["gc_zscore"]),
            "--out-split-read-density",
            str(outputs["split_density"]),
            "--out-softclip-density",
            str(outputs["softclip_density"]),
            "--out-junction-hotspots-bed",
            str(outputs["junction_hotspots"]),
            "--out-report-sites-bed",
            str(outputs["report_sites_bed"]),
            "--out-report-sites-tsv",
            str(outputs["report_sites_tsv"]),
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    assert result.stderr == ""
    for output in outputs.values():
        assert output.exists(), f"missing {output.name}"
        assert output.stat().st_size > 0, f"empty {output.name}"
    report_tsv = outputs["report_sites_tsv"].read_text(encoding="utf-8")
    assert "site_id" in report_tsv
    assert "hotspot_" in report_tsv
