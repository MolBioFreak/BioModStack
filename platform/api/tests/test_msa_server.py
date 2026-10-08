from pathlib import Path
import sys

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services import msa_server  # noqa: E402


def test_is_matching_gpuserver_process_rejects_empty_cmdline(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(msa_server, "_pid_is_alive", lambda pid: True)
    monkeypatch.setattr(msa_server, "_read_proc_cmdline", lambda pid: "")

    assert msa_server._is_matching_gpuserver_process(321, tmp_path / "uniref30_2302_db") is False


def test_server_status_filters_ready_servers_by_requested_contract(monkeypatch) -> None:
    monkeypatch.setattr(msa_server, "read_server_settings", lambda: {})
    monkeypatch.setattr(msa_server, "read_query_activity", lambda: None)
    monkeypatch.setattr(msa_server, "resolve_msa_gpu_id", lambda *_args, **_kwargs: 2)
    monkeypatch.setattr(
        msa_server,
        "list_servers",
        lambda *_args, **_kwargs: [
            {
                "alias": "uniref30_2302_db",
                "running": True,
                "gpu_id": 2,
                "max_seqs": 300,
                "prefilter_mode": 1,
                "db_load_mode": 0,
            },
            {
                "alias": "uniref30_2302_db",
                "running": True,
                "gpu_id": 2,
                "max_seqs": 300,
                "prefilter_mode": 1,
                "db_load_mode": 2,
            },
        ],
    )

    payload = msa_server.server_status(
        gpu_id=2,
        include_envdb=False,
        max_seqs=300,
        prefilter_mode=1,
        db_load_mode=2,
    )

    assert payload["requested_contract"] == {
        "gpu_id": 2,
        "max_seqs": 300,
        "prefilter_mode": 1,
        "db_load_mode": 2,
        "include_envdb": False,
    }
    assert payload["matching_aliases"] == ["uniref30_2302_db"]
    assert len(payload["matching_servers"]) == 1
    assert payload["matching_servers"][0]["db_load_mode"] == 2
    assert payload["all_running"] is True
