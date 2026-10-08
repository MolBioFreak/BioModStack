"""Scientific parser admission shares the verified native build authority."""
import importlib.util
from pathlib import Path

import pytest
from pysam import version


def parser():
    path = Path(__file__).resolve().parents[3] / "scripts/validate_modified_base_bam.py"
    spec = importlib.util.spec_from_file_location("modified_base_parser_gate", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_actual_authenticated_native_parser_is_admitted():
    assert version.__version__ == "0.23.3+bms1"
    parser().require_parser()


@pytest.mark.parametrize("name", ["0.23.2", "0.23.3+unverified", "0.23.3+bms2"])
def test_other_bindings_are_not_admitted(monkeypatch, name):
    module = parser()
    monkeypatch.setattr(version, "__version__", name)
    with pytest.raises(module.ModifiedBaseAdmissionError, match="requires pysam"):
        module.require_parser()


def test_other_htslib_is_not_admitted(monkeypatch):
    module = parser()
    monkeypatch.setattr(version, "__htslib_version__", "1.22")
    with pytest.raises(module.ModifiedBaseAdmissionError, match="requires pysam"):
        module.require_parser()


@pytest.mark.parametrize("field", ["upstream_sha256", "patch_sha256", "backend_sha256", "build_requirements_sha256"])
def test_patched_parser_rejects_tampered_build_identity(monkeypatch, field):
    from pysam.bms_native_build import IDENTITY
    module = parser()
    monkeypatch.setitem(IDENTITY, field, "0" * 64)
    with pytest.raises(module.ModifiedBaseAdmissionError, match="native build is unverified"):
        module.require_parser()
