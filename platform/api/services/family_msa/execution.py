"""Native MAFFT command projection for the canonical workflow compiler.

The lifecycle owner is responsible for input materialization, process groups,
terminal status, and artifact publication; this module starts no processes.
"""
from bms_alignment import AlignmentRequest
from bms_alignment.native import compile_mafft


def compile_command(payload: dict, executable: str, input_path: str) -> list[str]:
    return compile_mafft(AlignmentRequest.model_validate(payload), executable, input_path)
