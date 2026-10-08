"""Prequeue reference proof for the existing BAM preparation lane.

No-M5 input is not proved by hashing two independently selected files. Ordinary
launches must use M5 or explicitly request the already supported realignment.
Internal receipt-backed signal submissions retain their separate trusted owner.
"""
import pysam
from services import verified_native_reads as native


def validate_bam_reference_admission(params):
    bam_path, reference = params.get("bam_path"), params.get("reference_fasta")
    if not bam_path or not reference:
        return
    workflow = params.get("ont_workflow_id")
    if params.get("bam_force_realign") is True:
        if workflow not in {"ont_plasmid_qc", "ont_construct_screening", "wf_clone_validation"}:
            raise ValueError("This workflow does not support BAM realignment")
        return
    # Use the same native FASTA semantics as completion; no index is created.
    from services.ngs_alignment_sessions import _fasta_contigs_from_handle
    try:
        with open(reference, "rb") as handle:
            contigs, _ = _fasta_contigs_from_handle(handle)
        with native.alignment_path(bam_path, check_sq=False) as bam:
            dictionary = {sq["SN"]: sq for sq in bam.header.to_dict().get("SQ", [])}
            mapped = set()
            minimum = params.get("bam_min_mapq", 0)
            for read in bam.fetch(until_eof=True):
                if not read.is_unmapped and read.mapping_quality >= minimum:
                    mapped.add(read.reference_name)
            if not mapped:
                raise ValueError("BAM preparation has no mapped reads after the requested MAPQ filter; select explicit realignment where supported")
            for name in mapped:
                sq = dictionary[name]
                if name not in contigs or sq["LN"] != contigs[name][0]:
                    raise ValueError("Mapped BAM dictionary does not match the selected reference; select explicit realignment where supported")
                if not sq.get("M5"):
                    alternative = (
                        "Select Force realignment to establish a new alignment against this reference."
                        if workflow in {"ont_plasmid_qc", "ont_construct_screening", "wf_clone_validation"}
                        else "Select a BAM with reference M5 authority; methylation does not implement realignment."
                    )
                    raise ValueError("Mapped BAM lacks M5 reference proof. " + alternative)
                if sq["M5"].lower() != contigs[name][1]:
                    raise ValueError("Mapped BAM M5 differs from the selected immutable reference; select explicit realignment where supported")
    except (OSError, KeyError) as exc:
        raise ValueError("BAM/reference authority could not be read before launch") from exc
