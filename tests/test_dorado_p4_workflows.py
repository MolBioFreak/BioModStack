from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dorado_module_requires_preflight_and_exact_locked_paths() -> None:
    text = (ROOT / "modules/ngs/dorado_basecall.nf").read_text(encoding="utf-8")
    assert "process DoradoPreflight" in text
    assert "process DoradoBasecall" in text
    assert "path preflight_json" in text
    assert "biomodstack.dorado_preflight.v1" in text
    assert 'base_model="\\$PWD/sealed_models/\\${model_id}"' in text
    assert 'pod5_root="\\$PWD/sealed_pod5"' in text
    assert "--net --network none" in (ROOT / "nextflow.config").read_text(encoding="utf-8")
    assert '--batchsize "\\${batch_size}"' in text
    assert '--models-directory "\\$PWD/sealed_models"' in text
    assert "snapshot_model" in text
    assert "Dorado runtime changed after preflight" in text
    assert "POD5 snapshot identity mismatch" in text
    assert "dx:i:1" in text
    assert 'if [[ "\\${mode}" == duplex ]]; then command+=(duplex); else command+=(basecaller); fi' in text
    assert '"\\${command[@]}"' in text
    assert "--pairs" in text
    assert "--kit-name" in text
    assert "--modified-bases-models" in text
    assert "command+=(--no-trim)" in text
    assert "Dorado RNA always trims adapters" in text
    assert "is invalid for" in text and "visible GPU(s)" in text
    assert "params.dorado_model ?: 'sup'" not in text
    assert "def modBases" not in text


def test_barcode_is_classified_inline_then_demultiplexed_without_reclassification() -> None:
    text = (ROOT / "modules/ngs/dorado_basecall.nf").read_text(encoding="utf-8")
    assert "process DoradoDemux" in text
    assert "dorado demux" in text
    assert "--no-classify" in text
    assert "demux_manifest.json" in text
    assert "per_barcode_units.json" in text
    assert "barcode_classification_source" in text
    assert "biomodstack.dorado_barcode_unit.v1" in text
    assert "demux read-count parity failed" in text
    assert "unit_manifest_sha256" in text
    assert 'stem="\\${segment%.bam}"' in text
    assert '"\\${stem}" =~ ^(barcode[0-9]+|unclassified)' in text
    assert 'alias_to_barcode[\\${stem}]' in text


def test_all_pod5_routes_publish_canonical_basecall_provenance_and_fail_closed_reporting() -> None:
    names = [
        "ont_basecall_dna.nf",
        "ont_basecall_rna.nf",
        "ont_methylation_analysis.nf",
        "ont_plasmid_qc.nf",
        "ont_construct_screening.nf",
        "wf_clone_validation.nf",
    ]
    for name in names:
        text = (ROOT / "workflows/ngs" / name).read_text(encoding="utf-8")
        assert '"${params.out_dir}/basecall/dorado_preflight.json"' in text, name
        assert '"${params.out_dir}/basecall/dorado_runtime_provenance.json"' in text, name
        assert "Stage reporting failed" in text, name
        assert "Warning: Failed to report stage" not in text, name

    reporter = (ROOT / "scripts/stage_reporter.py").read_text(encoding="utf-8")
    assert "sys.exit(1)" in reporter
    assert "Do not fail workflow if reporting fails" not in reporter


def test_all_pod5_workflows_cross_preflight_before_gpu() -> None:
    names = [
        "ont_basecall_dna.nf",
        "ont_basecall_rna.nf",
        "ont_methylation_analysis.nf",
        "ont_plasmid_qc.nf",
        "ont_construct_screening.nf",
        "wf_clone_validation.nf",
    ]
    for name in names:
        text = (ROOT / "workflows/ngs" / name).read_text(encoding="utf-8")
        assert "DoradoPreflight" in text, name
        assert "DoradoPreflight(pod5_channel)" in text, name
        assert "DoradoBasecall(" in text and "DoradoPreflight.out.manifest" in text, name


def test_only_basecall_dna_can_emit_barcode_units() -> None:
    dna = (ROOT / "workflows/ngs/ont_basecall_dna.nf").read_text(encoding="utf-8")
    assert "DoradoDemux" in dna
    assert "barcode_kit" in dna
    assert "per_barcode_units.json" in dna
    for name in ["ont_basecall_rna.nf", "ont_methylation_analysis.nf", "ont_plasmid_qc.nf", "ont_construct_screening.nf", "wf_clone_validation.nf"]:
        text = (ROOT / "workflows/ngs" / name).read_text(encoding="utf-8")
        assert "barcoded POD5 must be demultiplexed" in text, name


def test_authorized_bam_alignment_consumes_only_a_verified_task_local_snapshot() -> None:
    text = (ROOT / "modules/ngs/dorado_align.nf").read_text(encoding="utf-8")
    assert 'cp --reflink=auto -- "${bam}" source.snapshot.bam' in text
    assert text.count("sha256sum source.snapshot.bam") == 2
    assert text.count("source.snapshot.bam") >= 7
    assert "task-local source BAM snapshot does not match authorized bam_source_sha256" in text


def test_bam_prepare_and_reference_alignment_consume_authenticated_task_local_snapshots() -> None:
    prepare = (ROOT / "modules/ngs/bam_prepare.nf").read_text(encoding="utf-8").split("process ValidateMappedBam", 1)[0]
    assert 'cp --reflink=auto -- "${bam}" source.snapshot.bam' in prepare
    assert prepare.count("sha256sum source.snapshot.bam") == 2
    assert 'samtools sort -@ ${task.cpus} -o aligned.bam "${bam}"' not in prepare
    assert 'samtools view -c "${bam}"' not in prepare
    assert "task-local source BAM snapshot does not match authorized bam_source_sha256" in prepare

    align = (ROOT / "modules/ngs/dorado_align.nf").read_text(encoding="utf-8")
    assert 'cp --reflink=auto -- "${reference}" reference.snapshot.fasta' in align
    assert align.count("sha256sum reference.snapshot.fasta") == 2
    assert align.count("reference.snapshot.fasta") >= 8
    assert "reference_sequence_sha256 must be exactly 64 hexadecimal characters" in align
    assert "task-local reference snapshot does not match authorized reference_sequence_sha256" in align
    assert 'cp "${reference}" reference.fasta' not in align
