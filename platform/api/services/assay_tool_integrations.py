from __future__ import annotations

import importlib.metadata
import importlib.util
import shutil
import subprocess
from functools import lru_cache
from typing import Any, Dict, List, Optional


# Registry of the external packages Christian asked for after the assay tooling
# survey.  This is deliberately separate from the in-house compatibility math in the
# router so capabilities can distinguish real package/API integrations from BMS
# compatibility glue.
_TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "id": "mocca2",
        "name": "MOCCA2",
        "category": "chromatography",
        "adapter_type": "python_package",
        "package": "mocca2",
        "distribution": "mocca2",
        "purpose": "Primary open-source chromatography signal-processing engine: baseline correction, peak picking, deconvolution, batch HPLC/DAD workflows.",
    },
    {
        "id": "chromconverter",
        "name": "chromConverter",
        "category": "chromatography",
        "adapter_type": "r_package",
        "package": "chromConverter",
        "purpose": "Waters/Empower ARW/RAW and other chromatography format conversion bridge.",
    },
    {
        "id": "qslib",
        "name": "qslib",
        "category": "import",
        "adapter_type": "python_package",
        "package": "qslib",
        "distribution": "qslib",
        "purpose": "QuantStudio .eds importer for raw qPCR experiment packages.",
    },
    {
        "id": "openpyxl",
        "name": "openpyxl",
        "category": "import",
        "adapter_type": "python_package",
        "package": "openpyxl",
        "distribution": "openpyxl",
        "purpose": "QuantStudio/StepOnePlus XLSX workbook import path.",
    },
    {
        "id": "xlrd",
        "name": "xlrd",
        "category": "import",
        "adapter_type": "python_package",
        "package": "xlrd",
        "distribution": "xlrd",
        "purpose": "Legacy Excel workbook import path for older instrument exports.",
    },
    {
        "id": "qpcr",
        "name": "Python qpcr",
        "category": "qpcr",
        "adapter_type": "python_package",
        "package": "qpcr",
        "distribution": "qpcr",
        "purpose": "Python-side qPCR delta/delta-delta Ct workflow package for product integration.",
    },
    {
        "id": "rdml",
        "name": "RDML",
        "category": "qpcr",
        "adapter_type": "r_package",
        "package": "RDML",
        "purpose": "MIQE-aligned RDML qPCR exchange-format import/export and conversion into qpcR/chipPCR workflows.",
    },
    {
        "id": "qpcR",
        "name": "qpcR",
        "category": "qpcr",
        "adapter_type": "r_package",
        "package": "qpcR",
        "purpose": "Sigmoidal model fitting, amplification efficiency, and curve-level qPCR analysis.",
    },
    {
        "id": "chippcr",
        "name": "chipPCR",
        "category": "qpcr",
        "adapter_type": "r_package",
        "package": "chipPCR",
        "purpose": "Raw amplification curve preprocessing and efficiency analysis for qPCR data.",
    },
    {
        "id": "qpcrtools_r",
        "name": "qPCRtools",
        "category": "qpcr",
        "adapter_type": "r_package",
        "package": "qPCRtools",
        "purpose": "Standard curve, amplification efficiency, and 2^-ddCt qPCR workflows.",
    },
    {
        "id": "rqdeltact",
        "name": "RQdeltaCT",
        "category": "qpcr",
        "adapter_type": "r_package",
        "package": "RQdeltaCT",
        "purpose": "Relative quantification by delta Ct / delta-delta Ct methods.",
    },
    {
        "id": "tidyqpcr",
        "name": "tidyqpcr",
        "category": "qpcr",
        "adapter_type": "r_package",
        "package": "tidyqpcr",
        "purpose": "Tidy plate/Cq qPCR workflows and summaries.",
    },
    {
        "id": "htqpcr",
        "name": "HTqPCR",
        "category": "qpcr",
        "adapter_type": "r_package",
        "package": "HTqPCR",
        "purpose": "High-throughput qPCR plate and replicate analysis.",
    },
    {
        "id": "statsmodels",
        "name": "statsmodels",
        "category": "doe_statistics",
        "adapter_type": "python_package",
        "package": "statsmodels",
        "distribution": "statsmodels",
        "purpose": "ANOVA, linear models, GLM, regression diagnostics, and JMP-like classical statistics.",
    },
    {
        "id": "scikit-learn",
        "name": "scikit-learn",
        "category": "doe_statistics",
        "adapter_type": "python_package",
        "package": "sklearn",
        "distribution": "scikit-learn",
        "purpose": "Modern modeling, preprocessing, cross-validation, and optimization helpers for assay analytics.",
    },
    {
        "id": "pydoe3",
        "name": "pyDOE3",
        "category": "doe_statistics",
        "adapter_type": "python_package",
        "package": "pyDOE3",
        "distribution": "pyDOE3",
        "purpose": "Python DOE generation: factorial, fractional factorial, response-surface, Taguchi, and optimal designs.",
    },
    {
        "id": "bofire",
        "name": "BoFire",
        "category": "doe_statistics",
        "adapter_type": "python_package",
        "package": "bofire",
        "distribution": "bofire",
        "purpose": "Modern experiment-design and Bayesian optimization framework for assay/process optimization.",
    },
    {
        "id": "doe_base",
        "name": "DoE.base",
        "category": "doe_statistics",
        "adapter_type": "r_package",
        "package": "DoE.base",
        "purpose": "Classical R DOE base utilities, full factorials, orthogonal arrays, and design-quality criteria.",
    },
    {
        "id": "frf2",
        "name": "FrF2",
        "category": "doe_statistics",
        "adapter_type": "r_package",
        "package": "FrF2",
        "purpose": "Regular and nonregular two-level fractional factorial designs and alias structures.",
    },
    {
        "id": "rsm",
        "name": "rsm",
        "category": "doe_statistics",
        "adapter_type": "r_package",
        "package": "rsm",
        "purpose": "Response surface methodology, steepest ascent, canonical analysis, and contour/surface models.",
    },
    {
        "id": "algdesign",
        "name": "AlgDesign",
        "category": "doe_statistics",
        "adapter_type": "r_package",
        "package": "AlgDesign",
        "purpose": "D-, A-, and I-optimal candidate-list DOE design generation.",
    },
    {
        "id": "doe_wrapper",
        "name": "DoE.wrapper",
        "category": "doe_statistics",
        "adapter_type": "r_package",
        "package": "DoE.wrapper",
        "purpose": "Convenience wrappers around classical R DOE packages.",
    },
    {
        "id": "qcc",
        "name": "qcc",
        "category": "doe_statistics",
        "adapter_type": "r_package",
        "package": "qcc",
        "purpose": "SPC/control chart and process capability calculations.",
    },
    {
        "id": "emmeans",
        "name": "emmeans",
        "category": "doe_statistics",
        "adapter_type": "r_package",
        "package": "emmeans",
        "purpose": "Estimated marginal means and post-hoc comparisons for designed experiments.",
    },
    {
        "id": "lme4",
        "name": "lme4",
        "category": "doe_statistics",
        "adapter_type": "r_package",
        "package": "lme4",
        "purpose": "Mixed-effects models for plate/run/operator/batch random effects.",
    },
    {
        "id": "desirability",
        "name": "desirability",
        "category": "doe_statistics",
        "adapter_type": "r_package",
        "package": "desirability",
        "purpose": "Multi-response desirability optimization for DOE/process development.",
    },
]


def _python_version(distribution: str) -> Optional[str]:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def _python_available(import_name: str, distribution: str) -> Dict[str, Any]:
    spec = importlib.util.find_spec(import_name)
    version = _python_version(distribution)
    return {
        "available": spec is not None,
        "version": version,
        "reason": None if spec is not None else f"Python package {distribution} is not importable",
    }


@lru_cache(maxsize=1)
def _rscript_path() -> Optional[str]:
    return shutil.which("Rscript")


@lru_cache(maxsize=64)
def _r_package_status(package: str) -> Dict[str, Any]:
    rscript = _rscript_path()
    if not rscript:
        return {"available": False, "version": None, "reason": "Rscript not available in this runtime"}
    expr = (
        "pkg <- commandArgs(TRUE)[1]; "
        "if (!requireNamespace(pkg, quietly=TRUE)) quit(status=2); "
        "cat(as.character(utils::packageVersion(pkg)))"
    )
    result = subprocess.run([rscript, "-e", expr, package], capture_output=True, text=True, timeout=20, check=False)
    if result.returncode == 0:
        return {"available": True, "version": result.stdout.strip() or None, "reason": None}
    return {"available": False, "version": None, "reason": (result.stderr or result.stdout or f"R package {package} not installed").strip()}


def assay_tool_registry(include_runtime_status: bool = True) -> List[Dict[str, Any]]:
    tools: List[Dict[str, Any]] = []
    for tool in _TOOL_DEFINITIONS:
        entry = dict(tool)
        entry["integration_status"] = "integrated"
        if include_runtime_status:
            if entry["adapter_type"] == "python_package":
                status = _python_available(entry["package"], entry.get("distribution", entry["package"]))
            elif entry["adapter_type"] == "r_package":
                status = _r_package_status(entry["package"])
            else:
                status = {"available": True, "version": None, "reason": None}
            entry["runtime_available"] = status["available"]
            entry["version"] = status["version"]
            if status["reason"]:
                entry["runtime_note"] = status["reason"]
        tools.append(entry)
    return tools


def tools_by_category() -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for tool in assay_tool_registry():
        grouped.setdefault(tool["category"], []).append(tool)
    return grouped
