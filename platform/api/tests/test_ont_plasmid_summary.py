"""The plasmid result is assembled from the job's published files only."""
import zipfile

from services import ont_plasmid_summary as summary


def artifact(path, size=10):
    return {"state": "present", "relative_path": path, "artifact_id": "a" * 64, "url": f"/api/jobs/j/ngs-artifacts/{path}",
            "size_bytes": size}


CLONE = [artifact(path) for path in (
    "assembly/wf_clone_out/s.final.fasta", "assembly/wf_clone_out/s.annotations.gbk",
    "assembly/wf_clone_out/wf-clone-validation-report.html", "assembly/adapter/consensus_pileup.ab1",
    "assembly/adapter/per_base_support.tsv", "assembly/adapter/plasmid_summary/summary.json",
    "assembly/adapter/plasmid_summary/coverage.svg", "assembly/adapter/plasmid_summary/read_length_histogram.svg",
    "assembly/adapter/plasmid_summary/virtual_gel.svg", "assembly/adapter/plasmid_summary/sample_summary.txt",
    "assembly/adapter/plasmid_summary/run_summary.csv", "align/aligned.bam", "align/aligned.bam.bai",
    "fastq_qc/fastq_consensus.fasta", "fastq_qc/plasmid_summary/summary.json", "fastq_qc/per_base_support.tsv",
)]


def test_assembly_files_are_chosen_once_each_and_win_over_read_qc_files():
    files = summary.select_summary_files(CLONE)
    assert [item["key"] for item in files] == [
        "consensus", "annotated_sequence", "chromatogram", "per_base_table", "assembly_report", "coverage_plot",
        "read_length_histogram", "virtual_gel", "sample_summary", "run_summary", "alignments", "alignments_index"]
    by_key = {item["key"]: item for item in files}
    assert by_key["consensus"]["relative_path"] == "assembly/wf_clone_out/s.final.fasta"
    assert by_key["per_base_table"]["relative_path"] == "assembly/adapter/per_base_support.tsv"
    assert summary.select_summary_artifact(CLONE)["relative_path"] == "assembly/adapter/plasmid_summary/summary.json"


def test_read_qc_result_uses_its_own_files_and_absent_files_are_not_listed():
    qc = [artifact(path) for path in ("fastq_qc/fastq_consensus.fasta", "fastq_qc/per_base_support.tsv",
                                      "fastq_qc/plasmid_summary/summary.json", "fastq_qc/aligned.bam")]
    qc.append({"state": "missing_optional", "relative_path": None, "artifact_id": None})
    files = summary.select_summary_files(qc)
    assert [item["key"] for item in files] == ["consensus", "per_base_table", "alignments"]
    assert summary.select_summary_artifact(qc)["relative_path"] == "fastq_qc/plasmid_summary/summary.json"
    assert summary.select_summary_artifact([artifact("align/aligned.bam")]) is None


def test_two_assemblies_in_one_result_are_not_guessed_between():
    files = summary.select_summary_files([artifact("assembly/wf_clone_out/a.final.fasta"),
                                          artifact("assembly/wf_clone_out/b.final.fasta")])
    assert files == []


def test_zip_holds_every_file_and_names_what_is_left_out(tmp_path, monkeypatch):
    files = summary.select_summary_files(CLONE)
    inside, left_out = summary.plan_zip(files)
    assert left_out == [] and len(inside) == len(files)
    monkeypatch.setattr(summary, "ZIP_LARGE_FILE_LIMIT_BYTES", 100)
    inside, left_out = summary.plan_zip(files)
    assert [item["key"] for item in left_out] == ["alignments", "alignments_index"]
    members = []
    for item in inside:
        path = tmp_path / item["filename"]
        path.write_text(item["key"])
        members.append((item["filename"], path))
    summary.write_zip(tmp_path / "out.zip", members, left_out, "s")
    with zipfile.ZipFile(tmp_path / "out.zip") as archive:
        names = set(archive.namelist())
        assert names == {f"s/{item['filename']}" for item in inside} | {"s/NOT_IN_THIS_ZIP.txt"}
        note = archive.read("s/NOT_IN_THIS_ZIP.txt").decode()
        assert "aligned.bam" in note and "over 2 GB" in note
        assert archive.read("s/s.final.fasta") == b"consensus"
