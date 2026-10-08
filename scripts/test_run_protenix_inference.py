import importlib.util
import sys
import types
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("run_protenix_inference.py")
SPEC = importlib.util.spec_from_file_location("run_protenix_inference_module", MODULE_PATH)
run_protenix_inference = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(run_protenix_inference)


def _install_fake_protenix_template_utils():
    class FakeTemplateHitFilter:
        def _assess_hit(
            self,
            hit,
            pdb_code,
            query_seq,
            cutoff,
            max_subseq_ratio: float = 0.95,
            min_align_ratio: float = 0.1,
        ):
            return {
                "pdb_code": pdb_code,
                "max_subseq_ratio": max_subseq_ratio,
                "min_align_ratio": min_align_ratio,
            }

    template_utils = types.ModuleType("protenix.data.template.template_utils")
    template_utils.TemplateHitFilter = FakeTemplateHitFilter

    protenix = types.ModuleType("protenix")
    data = types.ModuleType("protenix.data")
    template = types.ModuleType("protenix.data.template")
    template.template_utils = template_utils

    sys.modules["protenix"] = protenix
    sys.modules["protenix.data"] = data
    sys.modules["protenix.data.template"] = template
    sys.modules["protenix.data.template.template_utils"] = template_utils

    return template_utils.TemplateHitFilter


def test_allow_exact_duplicate_template_pdb_ids_relaxes_duplicate_filter():
    template_filter_cls = _install_fake_protenix_template_utils()

    run_protenix_inference._install_exact_template_duplicate_allowlist(["2LGV"])

    template_filter = template_filter_cls()
    allowed = template_filter._assess_hit(hit=None, pdb_code="2lgv", query_seq="", cutoff=None)
    blocked = template_filter._assess_hit(hit=None, pdb_code="1abc", query_seq="", cutoff=None)

    assert allowed["max_subseq_ratio"] == 1.01
    assert blocked["max_subseq_ratio"] == 0.95
