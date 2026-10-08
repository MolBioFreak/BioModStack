from __future__ import annotations

import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))


def _columns(table_name: str) -> set[str]:
    from services.assay_analytical_store import AnalyticalBase

    return set(AnalyticalBase.metadata.tables[table_name].columns.keys())


def _indexes(table_name: str) -> set[str]:
    from services.assay_analytical_store import AnalyticalBase

    return {index.name for index in AnalyticalBase.metadata.tables[table_name].indexes}


def test_empower_cdf_and_arw_observed_fields_are_first_class_or_raw_point_storable() -> None:
    """Coverage is based on local Empower/AIA files in Downloads/DB export testing.

    Observed CDF attrs/vars:
    sample_name, sample_type, sample_injection_volume, sample_amount,
    sample_id_comments, injection_date_time_stamp, detector_name/unit/min/max,
    retention_unit, actual_run_time_length, actual_sampling_interval,
    actual_delay_time, raw_data_retention, ordinate_values, peak_rt/area/height/
    amount/name.

    Observed ARW headers:
    Column Name, Column Serial Number, SampleName, Sample Type, Vial, System Name,
    Acquired By, Acq Method Set, Date Acquired, Injection, Injection Volume,
    Instrument Method Id, Channel, Channel Description.
    """

    from services.assay_analytical_store import AnalyticalBase

    assert "chrom_injections" in AnalyticalBase.metadata.tables
    assert "chrom_peaks" in AnalyticalBase.metadata.tables
    assert "chrom_trace_points" in AnalyticalBase.metadata.tables

    injection_columns = _columns("chrom_injections")
    assert {
        "sample_name",
        "sample_amount",
        "sample_comment",
        "column_name",
        "column_serial_number",
        "system_name",
        "acquired_by",
        "acq_method_set",
        "instrument_method_id",
        "channel_description",
        "detector_unit",
        "retention_unit",
        "detector_minimum_value",
        "detector_maximum_value",
        "actual_run_time_length",
        "actual_sampling_interval",
        "actual_delay_time",
        "raw_trace_storage_uri",
        "raw_trace_sha256",
    }.issubset(injection_columns)

    peak_columns = _columns("chrom_peaks")
    assert {"amount", "source_peak_index"}.issubset(peak_columns)

    trace_columns = _columns("chrom_trace_points")
    assert {"injection_id", "point_index", "retention_time", "signal_value", "retention_unit", "signal_unit"}.issubset(
        trace_columns
    )
    assert "ix_chrom_trace_points_injection_point" in _indexes("chrom_trace_points")
    assert "ix_chrom_trace_points_injection_rt" in _indexes("chrom_trace_points")


def test_qpcr_stepone_eds_and_xls_observed_fields_are_first_class_or_raw_point_storable() -> None:
    """Coverage is based on local 16Apr25_pRC9 StepOne/QuantStudio EDS+XLS.

    Observed XLS sheets/headers:
    Sample Setup: Well, Well Position, Sample Name, Sample Color, Target Name,
    Target Color, Task, Reporter, Quencher, Quantity, Comments.
    Results: Ct, Ct Mean, Ct CV%, Ct SD, Quantity Mean/SD/%CV, Dilution Factor,
    Dilution Adjusted, % Recovery, R2, Slope, Y-Intercept, Total DNA metrics.
    Raw Data: cycle x detector channels; Multicomponent Data: cycle x dyes;
    Amplification Data: cycle, target, Rn, delta-Rn.

    Observed EDS archive:
    840 .quant files, analysis_protocol.xml, tcprotocol.xml, filterdata.xml,
    experiment.xml, plate_setup.xml, multicomponentdata.xml.
    """

    from services.assay_analytical_store import AnalyticalBase

    assert "analytical_archive_members" in AnalyticalBase.metadata.tables
    assert "assay_run_settings" in AnalyticalBase.metadata.tables
    assert "qpcr_wells" in AnalyticalBase.metadata.tables
    assert "qpcr_amplification_points" in AnalyticalBase.metadata.tables
    assert "qpcr_multicomponent_points" in AnalyticalBase.metadata.tables
    assert "qpcr_raw_fluorescence_points" in AnalyticalBase.metadata.tables

    archive_columns = _columns("analytical_archive_members")
    assert {"source_file_id", "member_path", "member_role", "extension", "sha256", "size_bytes", "metadata_json"}.issubset(
        archive_columns
    )
    assert "ix_archive_members_source_role_ext" in _indexes("analytical_archive_members")

    setting_columns = _columns("assay_run_settings")
    assert {"run_id", "category", "setting_key", "setting_value", "numeric_value", "unit", "source_member"}.issubset(
        setting_columns
    )
    assert "ix_assay_run_settings_run_category_key" in _indexes("assay_run_settings")

    well_columns = _columns("qpcr_wells")
    assert {
        "well_position",
        "sample_name",
        "sample_color",
        "target_name",
        "target_color",
        "reporter",
        "quencher",
        "ct",
        "ct_mean",
        "ct_cv_percent",
        "ct_sd",
        "quantity_mean",
        "quantity_sd",
        "quantity_cv_percent",
        "dilution_factor",
        "dilution_adjusted",
        "percent_recovery",
        "r_squared",
        "slope",
        "y_intercept",
        "total_dna_per_ml",
        "total_dna_per_protein_concentration",
        "comments",
        "is_omitted",
        "baseline_start",
        "baseline_stop",
        "threshold",
    }.issubset(well_columns)

    amp_columns = _columns("qpcr_amplification_points")
    assert {"run_id", "well", "well_position", "cycle", "target_name", "rn", "delta_rn"}.issubset(amp_columns)
    assert "ix_qpcr_amp_points_run_well_cycle" in _indexes("qpcr_amplification_points")

    mc_columns = _columns("qpcr_multicomponent_points")
    assert {"run_id", "well", "well_position", "cycle", "dye", "signal_value"}.issubset(mc_columns)
    assert "ix_qpcr_multicomponent_run_well_cycle_dye" in _indexes("qpcr_multicomponent_points")

    raw_columns = _columns("qpcr_raw_fluorescence_points")
    assert {"run_id", "well", "well_position", "cycle", "channel", "fluorescence_value"}.issubset(raw_columns)
    assert "ix_qpcr_raw_fluorescence_run_well_cycle_channel" in _indexes("qpcr_raw_fluorescence_points")
