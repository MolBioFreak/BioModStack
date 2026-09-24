from pathlib import Path
import sys

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.nextflow import parse_stage_progress


def test_parse_ppiflow_tqdm_progress_from_runpartialflow_stdout(tmp_path: Path) -> None:
    (tmp_path / ".command.out").write_text(
        "Running inference...\n"
        "\rTesting DataLoader 0:  12%|█▎        | 1/8 [02:42<18:54,  0.01it/s]"
        "\rTesting DataLoader 0:  25%|██▌       | 2/8 [05:20<16:00,  0.01it/s]",
        encoding="utf-8",
    )

    assert parse_stage_progress(str(tmp_path), "runpartialflow") == "PPIFlow sample 2/8 (25%)"


def test_parse_ppiflow_progress_falls_back_to_sample_files(tmp_path: Path) -> None:
    out_dir = tmp_path / "ppiflow_out"
    out_dir.mkdir()
    (out_dir / "sample0.pdb").write_text("MODEL\nEND\n", encoding="utf-8")
    (out_dir / "sample1.pdb").write_text("MODEL\nEND\n", encoding="utf-8")
    (tmp_path / ".command.out").write_text("samples_per_target=8\n", encoding="utf-8")

    assert parse_stage_progress(str(tmp_path), "runpartialflow") == "PPIFlow sample 2/8"


@pytest.mark.parametrize("separator", [b"\n", b"\r", b"\r\n"])
@pytest.mark.parametrize("terminated", [True, False])
@pytest.mark.parametrize("limit", [1, 2, 200, 2048])
def test_progress_window_matches_universal_newline_tail(tmp_path, separator, terminated, limit):
    from services.nextflow import _read_recent_progress_lines

    path = tmp_path / ".command.out"
    lines = [f"sample {i} \u2588\u258e ".encode() + b"x" * (i % 97) for i in range(2301)]
    # Invalid native stderr bytes keep the established replacement semantics.
    lines[-2] += b"\xff"
    path.write_bytes(separator.join(lines) + (separator if terminated else b""))
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        expected = "".join(stream.readlines()[-limit:])
    assert _read_recent_progress_lines(path, limit) == expected


def test_progress_window_preserves_empty_short_and_long_lines(tmp_path):
    from services.nextflow import _read_recent_progress_lines

    path = tmp_path / ".command.out"
    for payload in (b"", b"\n", b"single", b"\r\n", b"x" * 150000, b"a\vnot-a-newline\nf\flast"):
        path.write_bytes(payload)
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            expected = "".join(stream.readlines()[-200:])
        assert _read_recent_progress_lines(path) == expected


def test_progress_window_handles_crlf_at_chunk_boundary(tmp_path):
    from services.nextflow import _read_recent_progress_lines

    path = tmp_path / ".command.out"
    # The last block starts at the LF of CRLF. Reading another block must not
    # count that one line boundary twice or truncate the requested window.
    ending = b"\n" + b"x" * 65535
    path.write_bytes(b"prefix\r\n" * 220 + b"edge\r" + ending)
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        expected = "".join(stream.readlines()[-200:])
    assert _read_recent_progress_lines(path) == expected


def test_progress_window_reads_only_recent_bytes_of_large_log(tmp_path, monkeypatch):
    from services.nextflow import _read_recent_progress_lines

    path = tmp_path / ".command.out"
    path.write_bytes((b"old completed sample " + b"x" * 100 + b"\n") * 20000
                     + b"Testing DataLoader 0:  25%|xx| 2/8 [elapsed]\n")
    original_open = Path.open
    reads = []

    class TrackedReader:
        def __init__(self, reader):
            self.reader = reader

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.reader.__exit__(*args)

        def seek(self, *args):
            return self.reader.seek(*args)

        def read(self, count):
            data = self.reader.read(count)
            reads.append(len(data))
            return data

    def track_open(file_path, *args, **kwargs):
        reader = original_open(file_path, *args, **kwargs)
        return TrackedReader(reader) if file_path == path and args == ("rb",) else reader

    monkeypatch.setattr(Path, "open", track_open)
    assert parse_stage_progress(str(tmp_path), "runpartialflow") == "PPIFlow sample 2/8 (25%)"
    assert 0 < sum(reads) < path.stat().st_size // 4
