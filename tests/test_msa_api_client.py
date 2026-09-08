"""OFFLINE, fixture-labelled provider HTTP contract tests; NOT live acceptance.

Native shapes follow ColabFold run_mmseqs2 and the Neurosnap API tutorial/service
metadata linked in biomodstack_msa_api. A3M bytes, IDs and filenames are synthetic
fixtures; no paid job, sequence submission, account key or provider sample is used.
Run from platform/api with the locked dev environment and socket guard enabled.
"""
import io
import json
import os
from pathlib import Path
import sys
import tarfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import biomodstack_msa_api as api

SEQ = "ACDEFGHIKLMNPQRSTVWYA"
SEQ2 = "CDEFGHIKLMNPQRSTVWYAC"
FIXTURE_KEY = "offline-fixture-not-a-real-key"


def a3m(seq=SEQ, identifier="101", insertion=True):
    row = seq[:3] + ("gg" if insertion else "") + seq[3:]
    return f">{identifier}\n{seq}\n>fixture-hit\n{row}\n".encode()


def archive(files=None, sequences=None, role="unpaired", use_env=True):
    sequences = sequences or [SEQ]
    native = b"\x00".join(a3m(seq, str(101 + i)) for i, seq in enumerate(sequences)) + b"\x00"
    if files is None:
        files = {"pair.a3m": native} if role == "paired" else {"uniref.a3m": native}
        if role == "unpaired" and use_env:
            files["bfd.mgnify30.metaeuk30.smag30.a3m"] = native
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w:gz") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return result.getvalue()


def response(value, status=200, retry_after=None):
    return api.HTTPResponse(status, value if isinstance(value, bytes) else json.dumps(value).encode(), retry_after)


class FixtureHTTP:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.observer = None

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.observer:
            self.observer(method, url)
        assert self.responses, "unexpected request in offline fixture transport"
        result = self.responses.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


@pytest.fixture
def setup(tmp_path):
    credential = tmp_path / "fixture-key"
    credential.write_text(FIXTURE_KEY)
    credential.chmod(0o600)
    return dict(cache_root=tmp_path / "cache", credential_file=credential,
                sequences=[SEQ], provider="neurosnap_api", settings={})


def client(tmp_path, transport, **kwargs):
    return api.MSAClient(config=api.ClientConfig(controller_root=tmp_path / "authority",
                                                poll_seconds=0, **kwargs),
                         transport=transport, sleep=lambda _: None)


def ns_success(name="provider chosen MSA.a3m"):
    return [response("fixture-job"), response("completed"),
            response({"config": {}, "in": [], "out": [["summary.txt", "5 B"], [name, "1 KB"]]}),
            response(a3m(identifier="query"))]


def cf_args(setup, **settings):
    return {**setup, "provider": "colabfold_api", "credential_file": None, "settings": settings}


def cf_success(sequences=None, role="unpaired", use_env=True, remote_id="fixture-ticket"):
    return [response({"id": remote_id, "status": "PENDING"}), response({"status": "COMPLETE"}),
            response(archive(sequences=sequences, role=role, use_env=use_env))]


def test_neurosnap_multipart_native_artifact_and_durable_id(tmp_path, setup):
    transport = FixtureHTTP(*ns_success())
    c = client(tmp_path, transport)

    def durable(method, url):
        if "/status/" in url:
            state = json.loads(next(setup["cache_root"].rglob("state.json")).read_text())
            assert state["tickets"]["unpaired"]["remote_id"] == "fixture-job"
    transport.observer = durable
    result = c.prepare_msa(**setup)
    assert set(result) == {"provider", "request_digest", "artifacts", "provenance", "cache_hit"}
    assert not result["cache_hit"]
    method, url, kwargs = transport.calls[0]
    assert method == "POST" and url == "https://neurosnap.ai/api/job/submit/mmseqs2%20MSA%20Generation"
    assert kwargs["headers"]["X-API-KEY"] == FIXTURE_KEY
    assert kwargs["data"] is None
    fields = {key: value for key, (filename, value) in kwargs["files"].items() if filename is None}
    assert fields == {"Query Sequence": json.dumps({"aa": {"query": SEQ}, "dna": {}, "rna": {}}),
                      "Coverage": "35", "Identity threshold": "50", "Max Sequences": "1000000",
                      "Force Uppercase": "false", "Pad Sequences": "false"}
    assert transport.calls[-1][1].endswith("/out/provider%20chosen%20MSA.a3m")
    assert Path(result["artifacts"][0]["path"]).read_bytes() == a3m(identifier="query")
    assert result["provenance"]["database_version"] is None
    for path in setup["cache_root"].rglob("*"):
        if path.is_file():
            assert FIXTURE_KEY.encode() not in path.read_bytes()
    # A verified cache hit needs neither a key read nor HTTP, including cache-only.
    replay = c.prepare_msa(**{**setup, "credential_file": tmp_path / "missing"}, cache_only=True)
    assert replay["cache_hit"] and replay["artifacts"] == result["artifacts"]
    assert len(transport.calls) == 4


@pytest.mark.parametrize("env,filter_,mode", [(True, True, "env"), (False, True, "all"),
                                               (True, False, "env-nofilter"), (False, False, "nofilter")])
def test_colabfold_native_modes_and_cache(tmp_path, setup, env, filter_, mode):
    transport = FixtureHTTP(*cf_success(use_env=env))
    args = cf_args(setup, use_env=env, use_filter=filter_)
    c = client(tmp_path, transport)
    result = c.prepare_msa(**args)
    assert transport.calls[0][1] == "https://api.colabfold.com/ticket/msa"
    assert transport.calls[0][2]["data"] == {"q": f">101\n{SEQ}\n", "mode": mode}
    assert "X-API-KEY" not in transport.calls[0][2]["headers"]
    expected = a3m() * (2 if env else 1)
    assert Path(result["artifacts"][0]["path"]).read_bytes() == expected
    assert c.prepare_msa(**args)["cache_hit"]
    assert len(transport.calls) == 3


def test_colabfold_complex_native_pairing_no_synthetic_rows(tmp_path, setup):
    sequences = [SEQ, SEQ2]
    transport = FixtureHTTP(*cf_success(sequences), *cf_success(sequences, role="paired", remote_id="pair-ticket"))
    result = client(tmp_path, transport).prepare_msa(**{**cf_args(setup, pairing_mode="unpaired_paired",
                                                                              pairing_strategy="complete"),
                                                      "sequences": sequences})
    assert len(result["artifacts"]) == 4
    assert transport.calls[3][1].endswith("/ticket/pair")
    assert transport.calls[3][2]["data"]["mode"] == "paircomplete-env"
    for item in result["artifacts"]:
        expected = a3m(sequences[item["chain_index"]], str(101 + item["chain_index"]))
        assert Path(item["path"]).read_bytes() == expected * (2 if item["role"] == "unpaired" else 1)


def test_unpaired_duplicate_chains_preserve_index(tmp_path, setup):
    transport = FixtureHTTP(*cf_success())
    result = client(tmp_path, transport).prepare_msa(**{**cf_args(setup), "sequences": [SEQ, SEQ]})
    assert [x["chain_index"] for x in result["artifacts"]] == [0, 1]
    assert transport.calls[0][2]["data"]["q"] == f">101\n{SEQ}\n"


@pytest.mark.parametrize("settings", [{"coverage": 9}, {"coverage": float("nan")},
    {"identity_threshold": 101}, {"max_sequences": 10.5}, {"max_sequences": True},
    {"force_uppercase": True}, {"pad_sequences": True}, {"force_uppercase": "false"},
    {"pairing_mode": "paired"}, {"use_env": True}, {"coverage": 35, "msa_neurosnap_coverage_percent": 35}])
def test_neurosnap_unsupported_settings_before_io(tmp_path, setup, settings):
    transport = FixtureHTTP()
    with pytest.raises(api.MSAAPIError):
        client(tmp_path, transport).prepare_msa(**{**setup, "settings": settings,
                                                   "credential_file": tmp_path / "not-read"})
    assert not transport.calls and not setup["cache_root"].exists()


@pytest.mark.parametrize("settings", [{"use_templates": True}, {"pairing_mode": "invented"},
    {"pairing_strategy": "invented"}, {"use_filter": "false"}, {"sensitivity": 7},
    {"pairing_mode": "paired", "use_filter": False}])
def test_colabfold_unsupported_before_submit(tmp_path, setup, settings):
    transport = FixtureHTTP()
    with pytest.raises(api.MSAAPIError):
        client(tmp_path, transport).prepare_msa(**cf_args(setup, **settings))
    assert not transport.calls


def test_neurosnap_alias_defaults_and_bounds():
    assert api.validate_settings("neurosnap_api", {"msa_neurosnap_coverage_percent": 90,
        "msa_neurosnap_identity_percent": 25, "msa_neurosnap_max_sequences": 10,
        "msa_neurosnap_force_uppercase": False, "msa_neurosnap_pad_sequences": False}) == {
            "coverage": 90, "identity_threshold": 25, "max_sequences": 10,
            "force_uppercase": False, "pad_sequences": False, "pairing_mode": "unpaired"}
    assert api.validate_settings("colabfold_api", {})["use_env"] is True


@pytest.mark.parametrize("sequences", [[], ["short"], [SEQ.lower()], [SEQ + "\n"], [SEQ] * 11, ["A" * 25001]])
def test_neurosnap_sequence_bounds_before_submit(tmp_path, setup, sequences):
    transport = FixtureHTTP()
    with pytest.raises(api.MSAAPIError):
        client(tmp_path, transport).prepare_msa(**{**setup, "sequences": sequences})
    assert not transport.calls


def test_request_identity_includes_provider_chain_order_and_settings(tmp_path):
    c = client(tmp_path, FixtureHTTP())
    digests = [c._identity(sequences, provider, settings)[1] for sequences, provider, settings in [
        ([SEQ], "colabfold_api", {}), ([SEQ], "colabfold_api", {"use_env": False}),
        ([SEQ2], "colabfold_api", {}), ([SEQ], "neurosnap_api", {}),
        ([SEQ], "neurosnap_api", {"coverage": 40}),
        ([SEQ, SEQ2], "colabfold_api", {}), ([SEQ2, SEQ], "colabfold_api", {}),
        ([SEQ, SEQ2], "colabfold_api", {"pairing_mode": "paired"})]]
    assert len(set(digests)) == len(digests)
    assert c._identity([SEQ], "neurosnap_api", {"coverage": 35})[1] == c._identity(
        [SEQ], "neurosnap_api", {"msa_neurosnap_coverage_percent": 35.0})[1]


@pytest.mark.parametrize("target", ["chain", "native", "identity", "query", "role", "missing"])
def test_cache_corruption_never_resubmits(tmp_path, setup, target):
    transport = FixtureHTTP(*ns_success())
    c = client(tmp_path, transport)
    result = c.prepare_msa(**setup)
    entry = Path(result["artifacts"][0]["path"]).parent
    manifest_path = entry / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if target == "chain":
        (entry / "chain-0-unpaired.a3m").write_bytes(b"corrupt")
    elif target == "native":
        (entry / "native-unpaired.a3m").write_bytes(b"corrupt")
    elif target == "identity":
        manifest["identity"]["settings"]["coverage"] = 40
    elif target == "query":
        data = a3m(SEQ2)
        (entry / "chain-0-unpaired.a3m").write_bytes(data)
        manifest["artifacts"][0]["sha256"] = api._hash(data)
    elif target == "role":
        manifest["artifacts"][0]["role"] = "paired"
    elif target == "missing":
        manifest["artifacts"] = []
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(api.MSAAPIError):
        c.prepare_msa(**setup)
    assert len(transport.calls) == 4


def test_cache_only_miss_reads_no_credential_submits_nothing(tmp_path, setup):
    transport = FixtureHTTP()
    with pytest.raises(api.MSAAPIError, match="no verified"):
        client(tmp_path, transport).prepare_msa(**{**setup, "credential_file": tmp_path / "absent"}, cache_only=True)
    assert not transport.calls
    assert not list(setup["cache_root"].rglob("state.json"))


def test_restart_same_id_and_other_cache_root_blocked(tmp_path, setup):
    first = FixtureHTTP(response("fixture-job"), response("running"))
    with pytest.raises(api.PendingMSA):
        client(tmp_path, first, max_polls=1).prepare_msa(**setup)
    blocked = FixtureHTTP()
    with pytest.raises(api.ReconciliationRequired):
        client(tmp_path, blocked).prepare_msa(**{**setup, "cache_root": tmp_path / "other", "settings": {"coverage": 40}})
    assert not blocked.calls
    second = FixtureHTTP(*ns_success()[1:])
    result = client(tmp_path, second).prepare_msa(**setup)
    assert not result["cache_hit"]
    assert all(method == "GET" for method, _, _ in second.calls)
    assert all("fixture-job" in url for _, url, _ in second.calls)


@pytest.mark.parametrize("failure", [TimeoutError("secret body"), response(b"not json"),
    response({"status": "RATELIMIT"}), response("", 429), response("", 401), response("", 500),
    response("", 302)])
def test_ambiguous_submit_is_never_retried(tmp_path, setup, failure):
    first = FixtureHTTP(failure)
    with pytest.raises(api.ReconciliationRequired, match="never automatically resubmit") as error:
        client(tmp_path, first).prepare_msa(**setup)
    assert "secret body" not in str(error.value)
    assert len(first.calls) == 1
    second = FixtureHTTP()
    with pytest.raises(api.ReconciliationRequired, match="no durable remote ID"):
        client(tmp_path, second).prepare_msa(**setup)
    assert not second.calls


@pytest.mark.parametrize("status", ["failed", "deleted", "cancelled"])
def test_terminal_state_is_durable_no_resubmit(tmp_path, setup, status):
    first = FixtureHTTP(response("fixture-job"), response(status))
    with pytest.raises(api.MSAAPIError, match="terminated"):
        client(tmp_path, first).prepare_msa(**setup)
    with pytest.raises(api.MSAAPIError, match="terminated"):
        client(tmp_path, FixtureHTTP()).prepare_msa(**setup)


def test_unknown_status_is_pending_not_success(tmp_path, setup):
    first = FixtureHTTP(response("fixture-job"), response("future-status"))
    with pytest.raises(api.PendingMSA, match="unknown"):
        client(tmp_path, first).prepare_msa(**setup)
    assert not list(setup["cache_root"].rglob("manifest.json"))


def test_safe_retry_and_download_restart_reuse_id(tmp_path, setup):
    first = FixtureHTTP(response("fixture-job"), response(b"", 503), response("completed"),
                        response({"out": [["actual.a3m", "1 KB"]]}), TimeoutError(), TimeoutError(), TimeoutError())
    with pytest.raises(api.MSAAPIError):
        client(tmp_path, first).prepare_msa(**setup)
    second = FixtureHTTP(response({"out": [["actual.a3m", "1 KB"]]}), response(a3m()))
    result = client(tmp_path, second).prepare_msa(**setup)
    assert result["artifacts"]
    assert all(method == "GET" for method, _, _ in second.calls)
    assert sum(method == "POST" for method, _, _ in first.calls) == 1


def test_retry_after_not_truncated_into_early_retry(tmp_path, setup):
    first = FixtureHTTP(response("fixture-job"), response(b"", 429, "600"))
    with pytest.raises(api.PendingMSA, match="Retry-After"):
        client(tmp_path, first).prepare_msa(**setup)
    assert len(first.calls) == 2


@pytest.mark.parametrize("listing", [[], [["x.a3m", "1"], ["y.a3m", "2"]],
    [["../x.a3m", "1"]], [["/x.a3m", "1"]], [["x\\y.a3m", "1"]],
    [["archive.tar.gz", "1"]], [{"filename": "x.a3m"}], [["x.a3m"]]])
def test_neurosnap_output_discovery_rejects_unknown_layout(tmp_path, setup, listing):
    transport = FixtureHTTP(response("fixture-job"), response("completed"), response({"out": listing}))
    with pytest.raises(api.MSAAPIError):
        client(tmp_path, transport).prepare_msa(**setup)
    assert len(transport.calls) == 3


@pytest.mark.parametrize("data", [a3m(SEQ2), b">q\n" + SEQ.encode() + b"\n>bad\nAAA\n", b"not a3m",
    b">q\n" + SEQ.encode() + b"\n>empty\n", b">q\n" + SEQ.encode() + b"\n>bad\n" + b"!" * 20])
def test_malformed_or_query_mismatched_a3m(tmp_path, setup, data):
    responses = ns_success()
    responses[-1] = response(data)
    with pytest.raises(api.MSAAPIError):
        client(tmp_path, FixtureHTTP(*responses)).prepare_msa(**setup)


@pytest.mark.parametrize("files", [{"../evil": b"x"}, {"uniref.a3m": a3m(identifier="102")},
    {"uniref.a3m": a3m(SEQ2)}, {"uniref.a3m": a3m() + b"\x00" + a3m()}, {"other": b"x"}])
def test_colabfold_archive_safety_and_query_binding(tmp_path, setup, files):
    transport = FixtureHTTP(response({"id": "ticket"}), response({"status": "COMPLETE"}), response(archive(files)))
    with pytest.raises(api.MSAAPIError):
        client(tmp_path, transport).prepare_msa(**cf_args(setup, use_env=False))
    assert not (tmp_path / "evil").exists()


def test_symlink_archive_member_rejected(tmp_path, setup):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as tar:
        info = tarfile.TarInfo("uniref.a3m")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    transport = FixtureHTTP(response({"id": "ticket"}), response({"status": "COMPLETE"}), response(data.getvalue()))
    with pytest.raises(api.MSAAPIError, match="unsafe"):
        client(tmp_path, transport).prepare_msa(**cf_args(setup, use_env=False))


def test_bounded_response(tmp_path, setup):
    transport = FixtureHTTP(response("fixture-job"), response(b"x" * 500))
    with pytest.raises(api.MSAAPIError, match="size limit"):
        client(tmp_path, transport, max_bytes=100).prepare_msa(**setup)


@pytest.mark.parametrize("mode", ["public", "symlink", "missing"])
def test_protected_credential_preflight(tmp_path, setup, mode):
    path = setup["credential_file"]
    if mode == "public":
        path.chmod(0o644)
    elif mode == "symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(path)
        setup["credential_file"] = alias
    else:
        path.unlink()
    transport = FixtureHTTP()
    with pytest.raises(api.MSAAPIError):
        client(tmp_path, transport).prepare_msa(**setup)
    assert not transport.calls
    assert not list(setup["cache_root"].rglob("state.json"))


def test_symlink_cached_artifact_not_followed(tmp_path, setup):
    c = client(tmp_path, FixtureHTTP(*ns_success()))
    result = c.prepare_msa(**setup)
    path = Path(result["artifacts"][0]["path"])
    path.unlink()
    path.symlink_to(setup["credential_file"])
    with pytest.raises(api.MSAAPIError, match="unsafe"):
        c.prepare_msa(**setup)


def test_process_lock_serializes_all_cache_roots(tmp_path):
    c = client(tmp_path, FixtureHTTP())
    with c._authority("colabfold_api"):
        pid = os.fork()
        if pid == 0:
            try:
                with c._authority("colabfold_api"):
                    os._exit(3)
            except api.PendingMSA:
                os._exit(0)
            except BaseException:
                os._exit(4)
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0


def test_paired_poll_restart_does_not_resubmit_unpaired(tmp_path, setup):
    args = {**cf_args(setup, pairing_mode="unpaired_paired"), "sequences": [SEQ, SEQ2]}
    first = FixtureHTTP(*cf_success([SEQ, SEQ2]), response({"id": "pair-ticket"}), response({"status": "RUNNING"}))
    with pytest.raises(api.PendingMSA):
        client(tmp_path, first, max_polls=1).prepare_msa(**args)
    second = FixtureHTTP(response(archive(sequences=[SEQ, SEQ2])), response({"status": "COMPLETE"}),
                         response(archive(sequences=[SEQ, SEQ2], role="paired")))
    assert len(client(tmp_path, second).prepare_msa(**args)["artifacts"]) == 4
    assert all(method == "GET" for method, _, _ in second.calls)


def test_cancel_explicit_once_then_terminal_reconciliation(tmp_path, setup):
    first = FixtureHTTP(response("fixture-job"), response("running"))
    with pytest.raises(api.PendingMSA):
        client(tmp_path, first, max_polls=1).prepare_msa(**setup)
    cancel = FixtureHTTP(response(b""))
    c = client(tmp_path, cancel)
    assert c.cancel(**setup)["status"] == "cancel_requested"
    assert c.cancel(**setup)["status"] == "cancel_requested"
    assert len(cancel.calls) == 1 and cancel.calls[0][1].endswith("/job/cancel/fixture-job")
    with pytest.raises(api.MSAAPIError, match="terminated"):
        client(tmp_path, FixtureHTTP(response("cancelled"))).prepare_msa(**setup)


def test_cancel_ambiguity_not_retried_and_colabfold_no_endpoint(tmp_path, setup):
    with pytest.raises(api.PendingMSA):
        client(tmp_path, FixtureHTTP(response("fixture-job"), response("running")), max_polls=1).prepare_msa(**setup)
    c = client(tmp_path, FixtureHTTP(TimeoutError()))
    with pytest.raises(api.MSAAPIError):
        c.cancel(**setup)
    assert c.cancel(**setup)["status"] == "cancel_ambiguous"
    with pytest.raises(api.MSAAPIError, match="not documented"):
        c.cancel(**cf_args(setup))


def test_actual_requests_transport_disables_redirects_netrc_and_proxy(monkeypatch):
    import requests
    calls = []

    class Response:
        status_code = 302
        headers = {"Location": "https://attacker.invalid/key-sink"}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_content(self, size): yield b"do not forward"

    class Session:
        trust_env = True
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def request(self, method, url, **kwargs):
            assert self.trust_env is False
            calls.append((method, url, kwargs))
            return Response()

    monkeypatch.setattr(requests, "Session", Session)
    result = api.RequestsTransport().request("GET", api.SERVICES["neurosnap_api"] + "/job/status/fixture",
                                           headers={"X-API-KEY": FIXTURE_KEY})
    assert result.status == 302
    assert len(calls) == 1
    assert calls[0][2]["allow_redirects"] is False
    assert calls[0][2]["stream"] is True
    assert calls[0][2]["timeout"] == (6.1, 30)


@pytest.mark.parametrize("field", ["provider", "effective_settings", "tickets"])
def test_provenance_corruption_is_blocked(tmp_path, setup, field):
    c = client(tmp_path, FixtureHTTP(*ns_success()))
    result = c.prepare_msa(**setup)
    path = Path(result["artifacts"][0]["path"]).parent / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["provenance"][field] = {}
    path.write_text(json.dumps(manifest))
    with pytest.raises(api.MSAAPIError, match="provenance"):
        c.prepare_msa(**setup)


def test_interrupted_after_id_durable_resumes_polling(tmp_path, setup):
    first = FixtureHTTP(response("fixture-job"), KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        client(tmp_path, first).prepare_msa(**setup)
    second = FixtureHTTP(*ns_success()[1:])
    assert client(tmp_path, second).prepare_msa(**setup)["artifacts"]
    assert all(method == "GET" for method, _, _ in second.calls)


def test_id_persistence_failure_cannot_cause_duplicate_post(tmp_path, setup, monkeypatch):
    original = api._atomic

    def fail_id_write(path, data):
        if path.name == "state.json" and b'"remote_id"' in data:
            raise OSError("fixture disk failure")
        return original(path, data)

    monkeypatch.setattr(api, "_atomic", fail_id_write)
    transport = FixtureHTTP(response("fixture-job"))
    with pytest.raises(OSError):
        client(tmp_path, transport).prepare_msa(**setup)
    monkeypatch.setattr(api, "_atomic", original)
    with pytest.raises(api.ReconciliationRequired, match="no durable remote ID"):
        client(tmp_path, FixtureHTTP()).prepare_msa(**setup)
    assert len(transport.calls) == 1


def test_credential_echo_is_rejected_not_persisted(tmp_path, setup):
    responses = ns_success()
    responses[-1] = response(a3m(identifier=FIXTURE_KEY))
    with pytest.raises(api.MSAAPIError, match="credential material"):
        client(tmp_path, FixtureHTTP(*responses)).prepare_msa(**setup)
    for path in setup["cache_root"].rglob("*"):
        if path.is_file():
            assert FIXTURE_KEY.encode() not in path.read_bytes()


def test_fifo_credential_fails_without_blocking(tmp_path, setup):
    setup["credential_file"].unlink()
    os.mkfifo(setup["credential_file"], 0o600)
    with pytest.raises(api.MSAAPIError, match="private"):
        client(tmp_path, FixtureHTTP()).prepare_msa(**setup)


def test_symlink_cache_ancestor_creates_nothing_outside_root(tmp_path, setup):
    target = tmp_path / "target"
    target.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(target)
    with pytest.raises(api.MSAAPIError, match="symlink"):
        client(tmp_path, FixtureHTTP()).prepare_msa(**{**setup, "cache_root": alias / "must-not-create"})
    assert not (target / "must-not-create").exists()


def test_paired_rows_must_correspond(tmp_path, setup):
    unequal = a3m() + b"\x00" + a3m(SEQ2, "102") + f">extra\n{SEQ2}\n".encode() + b"\x00"
    transport = FixtureHTTP(response({"id": "pair-ticket"}), response({"status": "COMPLETE"}),
                            response(archive({"pair.a3m": unequal})))
    with pytest.raises(api.MSAAPIError, match="row counts"):
        client(tmp_path, transport).prepare_msa(**{**cf_args(setup, pairing_mode="paired"),
                                                   "sequences": [SEQ, SEQ2]})


def test_public_cache_only_contract(tmp_path, setup, monkeypatch):
    monkeypatch.setenv("BMS_MSA_API_STATE_ROOT", str(tmp_path / "public-authority"))
    with pytest.raises(api.MSAAPIError, match="no verified"):
        api.prepare_msa(**setup, cache_only=True)


def test_neurosnap_independent_chains_share_cache_and_preserve_identity(tmp_path, setup):
    second = [response("fixture-second"), response("completed"),
              response({"out": [["native.a3m", "1 KB"]]}), response(a3m(SEQ2, "query"))]
    transport = FixtureHTTP(*ns_success(), *second)
    c = client(tmp_path, transport)
    args = {**setup, "sequences": [SEQ, SEQ2, SEQ]}
    result = c.prepare_msa(**args)
    assert [a['chain_index'] for a in result['artifacts']] == [0, 1, 2]
    assert result['artifacts'][0]['sha256'] == result['artifacts'][2]['sha256']
    assert len([call for call in transport.calls if call[0] == 'POST']) == 2
    replay = c.prepare_msa(**{**args, 'cache_only': True, 'credential_file': None})
    assert replay['cache_hit'] is True
    assert replay['artifacts'] == result['artifacts']
    assert len([call for call in transport.calls if call[0] == 'POST']) == 2


def test_colabfold_short_sequence_does_not_inherit_neurosnap_minimum(tmp_path, setup):
    seq = 'ACDEFGHIK'
    transport = FixtureHTTP(*cf_success([seq]))
    result = client(tmp_path, transport).prepare_msa(**{**cf_args(setup), 'sequences': [seq]})
    assert result['artifacts']
    with pytest.raises(api.MSAAPIError, match='20-25000'):
        api.effective_settings('neurosnap_api', {}, [seq])


def test_native_mmseqs_tab_separated_hit_headers_are_preserved():
    # Synthetic fixture shaped after the actual live MMseqs hit metadata header.
    data = f'>101\n{SEQ}\n>fixture_hit\t115\t0.894\t3.629E-27\t0\t19\n{SEQ}\n'.encode()
    assert api.validate_a3m(data, SEQ) == 2
    with pytest.raises(api.MSAAPIError, match='header'):
        api.validate_a3m(data.replace(b'fixture_hit', b'bad\x01header'), SEQ)


def test_cached_input_is_available_while_another_submission_owns_lock(tmp_path, setup):
    c = client(tmp_path, FixtureHTTP(*ns_success()))
    first = c.prepare_msa(**setup)
    with c._authority('neurosnap_api'):
        cached = c.prepare_msa(**{**setup, 'cache_only': True, 'credential_file': None})
    assert cached['cache_hit'] is True
    assert cached['artifacts'] == first['artifacts']
