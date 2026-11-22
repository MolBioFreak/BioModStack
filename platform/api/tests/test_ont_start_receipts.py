"""Offline native API-shape and lost-reply acquisition regressions."""
from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from lib import ont_minknow_host as host
from lib.ont_start_receipts import read_start_receipt

REQUEST_ID = "ont-run-" + "a" * 32


@pytest.fixture
def fake_instrument(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    calls = []
    connection = NS(
        device=NS(get_flow_cell_info=lambda: NS(has_flow_cell=True, product_code="FLO-MIN114")),
        protocol=NS(get_current_protocol_run=lambda: None, list_protocols=lambda: NS(protocols=[])),
    )
    monkeypatch.setattr(host, "_connect_position", lambda *a, **kw: (NS(), connection))
    def start(*a, **kw):
        calls.append(kw)
        return "minknow-run-one"
    tools = NS(start_protocol=start, OutputArgs=lambda **kw: kw)
    monkeypatch.setattr(host, "_protocols_module", lambda: tools)
    payload = {"confirm_start": True, "start_request_id": REQUEST_ID,
               "protocol_id": "protocol-one", "run_hours": 2, "basecalling_enabled": False}
    return calls, tools, payload


@pytest.mark.parametrize("protocol", ["protocol-one", NS(identifier="protocol-one")])
def test_native_find_protocol_accepts_identifier_and_protocol_info(monkeypatch, fake_instrument, protocol):
    _calls, tools, _payload = fake_instrument
    tools.find_protocol = lambda *a, **kw: protocol
    status = {"live_devices": [{"position": "X1", "device_type": "mk1d", "running": False,
                               "flow_cell": {"present": True}, "output_directories": {"reads": "/data"}}]}
    result = host.protocol_options("X1", kit="SQK-LSK114", basecalling_enabled=False, status=status)
    assert result["protocol_id"] == "protocol-one"
    assert result["can_start"] is True


@pytest.mark.parametrize("product,kit,sample_rate,model_name", [
    ("FLO-MIN114", "SQK-LSK114", 5000, "dna_r10.4.1_e8.2_400bps_hac@v5.2.0"),
    ("FLO-MIN004RA", "SQK-RNA004", 4000, "rna004_130bps_hac@v5.1.0"),
])
def test_dna_and_rna_protocol_options_preserve_native_chemistry_and_model(
    monkeypatch, fake_instrument, product, kit, sample_rate, model_name,
):
    _calls, tools, _payload = fake_instrument
    connection = NS(device=NS(get_flow_cell_info=lambda: NS(product_code=product)),
                    protocol=NS(list_protocols=lambda: NS(protocols=[])))
    configs = [NS(name="configuration")]
    def find_configs(requested_product, requested_kit, requested_rate):
        assert (requested_product, requested_kit, requested_rate) == (product, kit, sample_rate)
        return configs
    def find_protocol(_connection, **kwargs):
        assert kwargs == {"product_code": product, "kit": kit, "experiment_type": "sequencing"}
        return NS(identifier="protocol-" + kit)
    def find_model(_connection, requested_kit, requested_rate, available):
        assert (requested_kit, requested_rate, available) == (kit, sample_rate, configs)
        return configs[0], NS(name=model_name, default_q_score_cutoff=9)
    monkeypatch.setattr(host, "_connect_position", lambda *a, **kw: (NS(find_basecall_configurations=find_configs), connection))
    tools.find_protocol = find_protocol
    tools.find_default_simplex_model = find_model
    status = {"live_devices": [{"position": "X1", "device_type": "mk1d", "running": False,
                               "flow_cell": {"present": True, "sample_rate": sample_rate},
                               "output_directories": {"reads": "/data"}}]}
    result = host.protocol_options("X1", kit=kit, status=status)
    assert result["can_start"] is True
    assert result["basecalling_options"] == {"simplex_models": [model_name], "min_qscore": 9}


def test_success_receipt_survives_lost_reply_and_prevents_second_physical_start(fake_instrument):
    calls, _tools, payload = fake_instrument
    code, response = host.start_protocol("X1", payload)
    assert code == 201
    assert read_start_receipt("X1", REQUEST_ID) == response
    assert host.start_protocol("X1", dict(payload)) == (code, response)
    assert len(calls) == 1
    assert host.start_protocol("X1", {**payload, "run_hours": 3})[0] == 409
    assert read_start_receipt("another-position", REQUEST_ID).get("minknow_run_id") is None


def test_minknow_transport_failure_is_unknown_and_never_retried(fake_instrument):
    calls, tools, payload = fake_instrument
    def lost_reply(*a, **kw):
        calls.append(kw)
        raise TimeoutError("RPC reply lost after sending start")
    tools.start_protocol = lost_reply
    code, response = host.start_protocol("X1", payload)
    assert code == 502 and response["start_outcome"] == "unknown"
    assert host.start_protocol("X1", payload) == (code, response)
    assert read_start_receipt("X1", REQUEST_ID)["start_outcome"] == "unknown"
    assert len(calls) == 1


def test_missing_request_identity_never_calls_minknow(fake_instrument):
    calls, _tools, payload = fake_instrument
    del payload["start_request_id"]
    assert host.start_protocol("X1", payload)[0] == 400
    assert calls == []


def test_host_receipt_route_is_read_only_and_does_not_discover_devices(monkeypatch, fake_instrument):
    import bms_host_agent
    _calls, _tools, payload = fake_instrument
    _, response = host.start_protocol("X1", payload)
    monkeypatch.setattr(bms_host_agent, "discover_ont_status", lambda: pytest.fail("receipt read must not use instrument"))
    assert bms_host_agent.ont_route_payload(f"/ont/positions/X1/start-requests/{REQUEST_ID}") == response


def test_a_barcoding_kit_is_passed_to_minknow_for_demultiplexing(fake_instrument):
    calls, tools, payload = fake_instrument
    tools.BasecallingArgs = lambda **kw: kw
    tools.BarcodingArgs = lambda **kw: kw
    request = {**payload, "basecalling_enabled": True, "simplex_model": "model"}
    code, _ = host.start_protocol("X1", {**request, "barcoding_kits": ["SQK-RBK114-24"]})
    assert code == 201
    assert calls[0]["basecalling"]["barcoding"] == {"kits": ["SQK-RBK114-24"], "trim_barcodes": False, "barcodes_both_ends": False}


def test_a_kit_without_barcodes_starts_as_before(fake_instrument):
    calls, tools, payload = fake_instrument
    tools.BasecallingArgs = lambda **kw: kw
    code, _ = host.start_protocol("X1", {**payload, "basecalling_enabled": True, "simplex_model": "model"})
    assert code == 201 and calls[0]["basecalling"]["barcoding"] is None


def test_sample_names_per_barcode_are_passed_to_minknow(fake_instrument):
    calls, tools, payload = fake_instrument
    tools.BarcodeUserData = lambda **kw: kw
    code, _ = host.start_protocol("X1", {**payload, "barcode_samples": [{"barcode": "barcode01", "alias": "plasmid_A"}]})
    assert code == 201 and calls[0]["barcode_info"] == [{"barcode_name": "barcode01", "alias": "plasmid_A"}]


def test_an_idle_device_reporting_another_rate_still_finds_the_kit_model(monkeypatch, fake_instrument):
    _calls, tools, _payload = fake_instrument
    connection = NS(device=NS(get_flow_cell_info=lambda: NS(product_code="FLO-MIN114")),
                    protocol=NS(list_protocols=lambda: NS(protocols=[])))
    configs = [NS(name="configuration", sampling_rate=5000)]
    manager = NS(find_basecall_configurations=lambda product, kit, rate=None: [] if rate == 10000 else configs)
    monkeypatch.setattr(host, "_connect_position", lambda *a, **kw: (manager, connection))
    tools.find_protocol = lambda _connection, **kwargs: NS(identifier="protocol")
    def find_model(_connection, _kit, rate, available):
        assert rate == 5000 and available == configs
        return configs[0], NS(name="hac", default_q_score_cutoff=9)
    tools.find_default_simplex_model = find_model
    status = {"live_devices": [{"position": "X1", "device_type": "mk1d", "running": False,
                               "flow_cell": {"present": True, "sample_rate": 10000},
                               "output_directories": {"reads": "/data"}}]}
    result = host.protocol_options("X1", kit="SQK-RBK114-24", status=status)
    assert result["can_start"] is True and result["basecalling_options"]["simplex_models"] == ["hac"]


def _kit_models():
    mods = lambda tier: [NS(name=f"{tier}_6mA@v1", variant="6mA", canonical_base="A", context="A", outdated=False),
                         NS(name=f"{tier}_5mCG_5hmCG@v2", variant="5mCG_5hmCG", canonical_base="C", context="CG", outdated=False),
                         NS(name=f"{tier}_old@v0", variant="4mC_5mC", canonical_base="C", context="C", outdated=True)]
    return NS(name="configuration", sampling_rate=5000, simplex_models=[
        NS(name="fast@v5", variant="fast", outdated=False, default_q_score_cutoff=8.0, modified_models=[]),
        NS(name="hac@v5", variant="hac", outdated=False, default_q_score_cutoff=9.0, modified_models=mods("hac")),
        NS(name="sup@v5", variant="sup", outdated=False, default_q_score_cutoff=10.0, modified_models=mods("sup")),
        NS(name="sup@v4", variant="sup", outdated=True, default_q_score_cutoff=10.0, modified_models=[]),
    ])


def test_protocol_options_list_the_kit_models_and_their_modification_models(monkeypatch, fake_instrument):
    _calls, tools, _payload = fake_instrument
    connection = NS(device=NS(get_flow_cell_info=lambda: NS(product_code="FLO-MIN114")),
                    protocol=NS(list_protocols=lambda: NS(protocols=[])))
    config = _kit_models()
    monkeypatch.setattr(host, "_connect_position",
                        lambda *a, **kw: (NS(find_basecall_configurations=lambda *args: [config]), connection))
    tools.find_protocol = lambda _connection, **kwargs: NS(identifier="protocol")
    tools.find_default_simplex_model = lambda _connection, _kit, _rate, _available: (config, config.simplex_models[1])
    status = {"live_devices": [{"position": "X1", "device_type": "mk1d", "running": False,
                               "flow_cell": {"present": True, "sample_rate": 5000},
                               "output_directories": {"reads": "/data"}}]}
    options = host.protocol_options("X1", kit="SQK-RBK114-24", status=status)["basecalling_options"]
    assert options["simplex_models"] == ["hac@v5"] and options["min_qscore"] == 9.0
    assert [(model["variant"], model["name"], model["min_qscore"]) for model in options["models"]] == [
        ("fast", "fast@v5", 8.0), ("hac", "hac@v5", 9.0), ("sup", "sup@v5", 10.0)]
    assert options["models"][0]["modified_models"] == []
    assert options["models"][2]["modified_models"] == [
        {"name": "sup_6mA@v1", "variant": "6mA", "canonical_base": "A", "context": "A"},
        {"name": "sup_5mCG_5hmCG@v2", "variant": "5mCG_5hmCG", "canonical_base": "C", "context": "CG"},
    ]


def test_chosen_modification_models_reach_minknow_and_the_run_writes_bam(fake_instrument):
    calls, tools, payload = fake_instrument
    tools.BasecallingArgs = lambda **kw: kw
    tools.BarcodingArgs = lambda **kw: kw
    code, _ = host.start_protocol("X1", {
        **payload, "basecalling_enabled": True, "simplex_model": "sup@v5", "min_qscore": 12,
        "modified_models": ["sup_6mA@v1", "sup_5mCG_5hmCG@v2"], "barcoding_kits": ["SQK-RBK114-24"]})
    assert code == 201
    basecalling = calls[0]["basecalling"]
    assert basecalling["simplex_model"] == "sup@v5" and basecalling["min_qscore"] == 12
    assert basecalling["modified_models"] == ["sup_6mA@v1", "sup_5mCG_5hmCG@v2"]
    assert basecalling["barcoding"]["kits"] == ["SQK-RBK114-24"]
    assert calls[0]["bam_arguments"] == {"reads_per_file": None, "batch_duration": None}


def test_a_run_without_modification_models_starts_as_before(fake_instrument):
    calls, tools, payload = fake_instrument
    tools.BasecallingArgs = lambda **kw: kw
    code, _ = host.start_protocol("X1", {**payload, "basecalling_enabled": True, "simplex_model": "hac@v5"})
    assert code == 201 and calls[0]["basecalling"]["modified_models"] is None and "bam_arguments" not in calls[0]


def _live_connection(*, barcodes=True):
    def summary(reads, passed, failed, pass_bases, fail_bases, estimated, fraction=0.0):
        return NS(read_count=reads, basecalled_pass_read_count=passed, basecalled_fail_read_count=failed,
                  basecalled_pass_bases=pass_bases, basecalled_fail_bases=fail_bases,
                  estimated_selected_bases=estimated, fraction_basecalled=fraction)
    acquisitions = {
        "acq-mux": NS(yield_summary=summary(0, 0, 0, 0, 0, 0), start_time=NS(seconds=100), end_time=NS(seconds=160),
                      config_summary=NS(basecalling_enabled=False, channel_count=512)),
        "acq-seq": NS(yield_summary=summary(1000, 700, 100, 3_500_000, 400_000, 5_000_000, 0.8),
                      start_time=NS(seconds=200), end_time=NS(seconds=0),
                      config_summary=NS(basecalling_enabled=True, channel_count=512)),
    }
    def output(acquisition_run_id, split, _timeout):
        assert split.barcode_name is True
        if acquisition_run_id != "acq-seq" or not barcodes:
            return iter([])
        group = lambda name, alias, rows: NS(filtering=[NS(barcode_name=name, barcode_alias=alias)], snapshots=[
            NS(seconds=seconds, yield_summary=summary(*values)) for seconds, values in rows])
        return iter([NS(snapshots=[
            group("barcode01", "plasmid_A", [(60, (100, 80, 5, 400_000, 20_000, 0)), (120, (600, 500, 40, 2_500_000, 150_000, 0))]),
            group("unclassified", "", [(120, (300, 200, 60, 1_000_000, 250_000, 0))]),
        ])])
    def duty(acquisition_run_id, data_selection, _timeout):
        assert acquisition_run_id == "acq-seq" and data_selection.step == 60
        return iter([NS(bucket_ranges=[NS(start=0, end=60), NS(start=60, end=120), NS(start=120, end=180)],
                        channel_states={"strand": NS(state_times=[10, 500, 1]), "adapter": NS(state_times=[0, 100, 0]),
                                        "pore": NS(state_times=[90, 200, 1]), "zero": NS(state_times=[900, 200, 8])})])
    return NS(
        protocol=NS(get_run_info=lambda run_id: NS(run_id=run_id, state="PROTOCOL_RUNNING",
                                                   acquisition_run_ids=["acq-mux", "acq-seq"])),
        acquisition=NS(get_acquisition_info=lambda run_id: acquisitions[run_id]),
        statistics=NS(stream_acquisition_output=output, stream_duty_time=duty),
    )


def _live_host(monkeypatch, connection):
    monkeypatch.setattr(host, "discover_status", lambda: {
        "implementation_status": host.MINKNOW_STATUS_CONFIGURED, "live_devices": [{"position": "X1"}]})
    monkeypatch.setattr(host, "_connect_position", lambda *a, **kw: (NS(), connection))
    monkeypatch.setattr(host, "_statistics_module", lambda: NS(
        AcquisitionOutputSplit=lambda **kw: NS(**kw), DataSelection=lambda **kw: NS(**kw)))


def test_live_stats_total_the_run_and_split_the_latest_output_by_barcode(monkeypatch):
    _live_host(monkeypatch, _live_connection())
    live = host.run_live_stats("run-one")
    assert live["available"] is True and live["position"] == "X1" and live["acquisition_count"] == 2
    assert live["totals"] == {"read_count": 1000, "basecalled_pass_read_count": 700, "basecalled_fail_read_count": 100,
                              "basecalled_pass_bases": 3_500_000, "basecalled_fail_bases": 400_000,
                              "estimated_selected_bases": 5_000_000}
    assert live["latest_acquisition"]["acquisition_id"] == "acq-seq"
    assert live["latest_acquisition"]["basecalling_enabled"] is True and live["latest_acquisition"]["fraction_basecalled"] == 0.8
    # Each barcode reports its newest snapshot, not the sum of the cumulative ones.
    assert [(item["barcode"], item["alias"], item["basecalled_pass_read_count"], item["basecalled_pass_bases"])
            for item in live["barcodes"]] == [("barcode01", "plasmid_A", 500, 2_500_000), ("unclassified", None, 200, 1_000_000)]
    # The last bucket is still filling, so the minute before it is reported.
    assert live["channels"] == {"minute_start_seconds": 60, "sequencing_fraction": 0.6, "open_pore_fraction": 0.2,
                                "other_fraction": 0.2}


def test_live_stats_keep_totals_when_minknow_has_no_barcode_or_channel_data(monkeypatch):
    connection = _live_connection(barcodes=False)
    def broken(**kwargs):
        raise RuntimeError("statistics unavailable")
    connection.statistics.stream_duty_time = broken
    _live_host(monkeypatch, connection)
    live = host.run_live_stats("run-one")
    assert live["available"] is True and live["totals"]["read_count"] == 1000
    assert live["barcodes"] == [] and live["channels"] is None


def test_live_stats_for_an_unknown_run_are_unavailable_and_start_nothing(monkeypatch, fake_instrument):
    calls, _tools, _payload = fake_instrument
    def unknown(run_id):
        raise RuntimeError("no such run")
    _live_host(monkeypatch, NS(protocol=NS(get_run_info=unknown)))
    assert host.run_live_stats("missing") == {"minknow_run_id": "missing", "available": False, "fake_or_demo_devices": False}
    assert calls == []


def test_host_agent_serves_live_stats_on_its_own_read_only_route(monkeypatch):
    import bms_host_agent
    seen = []
    monkeypatch.setattr(bms_host_agent, "ont_run_live_stats", lambda run_id: seen.append(run_id) or {"available": True})
    monkeypatch.setattr(bms_host_agent, "observe_ont_run", lambda run_id: {"status": "active"})
    assert bms_host_agent.ont_route_payload("/ont/runs/run-one/live") == {"available": True}
    assert bms_host_agent.ont_route_payload("/ont/runs/run-one") == {"status": "active"}
    assert seen == ["run-one"]


def test_adaptive_sampling_files_are_copied_where_minknow_can_read_and_passed_to_the_start(monkeypatch, fake_instrument, tmp_path):
    calls, tools, payload = fake_instrument
    tools.BasecallingArgs = lambda **kw: kw
    tools.ReadUntilArgs = lambda **kw: kw
    monkeypatch.setenv("BMS_ONT_ADAPTIVE_SAMPLING_DIR", str(tmp_path / "shared"))
    reference = tmp_path / "upload.fa"; reference.write_text(">p\nACGT\n")
    bed = tmp_path / "upload.bed"; bed.write_text("p\t0\t4\n")
    code, _ = host.start_protocol("X1", {
        **payload, "basecalling_enabled": True, "simplex_model": "hac@v5",
        "adaptive_sampling": {"mode": "enrich", "reference_path": str(reference), "bed_path": str(bed)}})
    assert code == 201
    sent = calls[0]["read_until"]
    folder = tmp_path / "shared" / REQUEST_ID
    assert sent == {"filter_type": "enrich", "reference_files": [str(folder / "reference.fa")],
                    "bed_file": str(folder / "targets.bed"), "first_channel": None, "last_channel": None}
    assert (folder / "reference.fa").read_text() == ">p\nACGT\n" and (folder / "targets.bed").read_text() == "p\t0\t4\n"
    assert (folder / "reference.fa").stat().st_mode & 0o044 == 0o044


def test_barcode_balancing_needs_no_files_and_a_plain_run_sends_no_adaptive_sampling(fake_instrument):
    calls, tools, payload = fake_instrument
    tools.BasecallingArgs = lambda **kw: kw
    tools.ReadUntilArgs = lambda **kw: kw
    base = {**payload, "basecalling_enabled": True, "simplex_model": "hac@v5"}
    assert host.start_protocol("X1", {**base, "adaptive_sampling": {"mode": "barcode_balance"}})[0] == 201
    assert calls[0]["read_until"]["filter_type"] == "barcode_balance" and calls[0]["read_until"]["reference_files"] is None
    assert host.start_protocol("X1", {**base, "start_request_id": "ont-run-" + "b" * 32})[0] == 201
    assert "read_until" not in calls[1]


@pytest.mark.parametrize("adaptive", [{"mode": "enrich"}, {"mode": "sideways"},
                                      {"mode": "deplete", "reference_path": "/nonexistent/reference.fa"}])
def test_adaptive_sampling_that_cannot_be_set_up_is_rejected_before_minknow_starts(fake_instrument, adaptive):
    calls, tools, payload = fake_instrument
    tools.BasecallingArgs = lambda **kw: kw
    tools.ReadUntilArgs = lambda **kw: kw
    code, body = host.start_protocol("X1", {**payload, "basecalling_enabled": True, "simplex_model": "hac@v5",
                                             "adaptive_sampling": adaptive})
    assert code == 400 and "Adaptive sampling could not be set up" in body["detail"] and calls == []


def _chart_connection():
    histogram = NS(bucket_ranges=[NS(start=i * 100, end=(i + 1) * 100) for i in range(4)],
                   histogram_data=[NS(n50=250.0, filtering=[], bucket_values=[10, 600, 300, 1])])
    split = NS(bucket_ranges=histogram.bucket_ranges, histogram_data=[
        NS(n50=0.0, filtering=[NS(read_end_reason=5)], bucket_values=[0, 100, 300, 1]),
        NS(n50=0.0, filtering=[NS(read_end_reason=7)], bucket_values=[10, 500, 0, 0])])
    def lengths(**kw):
        return iter([split if kw.get("split") is not None else histogram])
    def duty(acquisition_run_id, data_selection, _timeout):
        return iter([NS(bucket_ranges=[NS(start=0, end=60), NS(start=60, end=120)],
                        channel_states={"strand": NS(state_times=[50, 60]), "adapter": NS(state_times=[10, 0]),
                                        "pore": NS(state_times=[20, 20]), "unavailable": NS(state_times=[10, 0]),
                                        "zero": NS(state_times=[5, 10]), "saturated": NS(state_times=[0, 5]),
                                        "unclassified": NS(state_times=[5, 5])})])
    def output(acquisition_run_id, _timeout, **kw):
        row = lambda seconds, reads, passed: NS(seconds=seconds, yield_summary=NS(
            read_count=reads, basecalled_pass_read_count=passed, basecalled_fail_read_count=0,
            basecalled_pass_bases=passed * 1000, basecalled_fail_bases=0, estimated_selected_bases=reads * 1100))
        return iter([NS(snapshots=[NS(filtering=[], snapshots=[row(60, 100, 80), row(120, 250, 200)])])])
    return NS(statistics=NS(stream_read_length_histogram=lengths, stream_duty_time=duty, stream_acquisition_output=output))


def test_run_graph_series_follow_minknows_pore_activity_output_and_read_lengths():
    stats = NS(DataSelection=lambda **kw: NS(**kw), ReadLengthHistogramSplit=lambda **kw: NS(**kw),
               EstimatedBases=1, ReadLengths=1)
    charts = host._acquisition_charts(_chart_connection(), {"acquisition_id": "acq", "started_at": 100, "ended_at": 220}, stats)
    assert charts["pore_activity"] == [
        {"start_seconds": 0, "sequencing": 0.6, "pore": 0.2, "recovering": 0.1, "inactive": 0.05, "unclassified": 0.05},
        {"start_seconds": 60, "sequencing": 0.6, "pore": 0.2, "recovering": 0.0, "inactive": 0.15, "unclassified": 0.05}]
    assert [(p["seconds"], p["read_count"], p["basecalled_pass_bases"]) for p in charts["cumulative"]] == [
        (60, 100, 80_000), (120, 250, 200_000)]
    # The far tail (last 0.5% of bases) is trimmed; ejected strands are reported beside the totals.
    assert charts["read_lengths"] == {"n50": 250.0, "buckets": [
        {"start": 0, "end": 100, "value": 10, "rejected": 10}, {"start": 100, "end": 200, "value": 600, "rejected": 500},
        {"start": 200, "end": 300, "value": 300, "rejected": 0}]}


def test_one_unavailable_graph_series_leaves_the_others():
    connection = _chart_connection()
    def broken(**kw):
        raise RuntimeError("no histogram yet")
    connection.statistics.stream_read_length_histogram = broken
    stats = NS(DataSelection=lambda **kw: NS(**kw), ReadLengthHistogramSplit=lambda **kw: NS(**kw), EstimatedBases=1, ReadLengths=1)
    charts = host._acquisition_charts(connection, {"acquisition_id": "acq", "started_at": 100, "ended_at": 220}, stats)
    assert charts["read_lengths"] is None and len(charts["pore_activity"]) == 2 and len(charts["cumulative"]) == 2
