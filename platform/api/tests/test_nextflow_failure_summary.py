from services.nextflow import _BoundedLogTail, _nextflow_failure_summary

REAL = """executor >  local (1)
ERROR ~ Error executing process > 'ONT_BASECALL_DNA:DNA_READ_ANALYSIS:DoradoPreflight (dorado-preflight)'

Caused by:
  Process `ONT_BASECALL_DNA:DNA_READ_ANALYSIS:DoradoPreflight (dorado-preflight)` terminated with an error exit status (1)

Command executed:

  set -euo pipefail

Command exit status:
  1

Command output:
  (empty)

Command error:
  Traceback (most recent call last):
    File "/code/scripts/dorado_p4_preflight.py", line 702, in <module>
      raise SystemExit(main())
                       ^^^^^^
  ValueError: Dorado runtime canonical selector identity mismatch

Work dir:
  /work/d6/87e195c10f8ea1a90eebd242bff783

Tip: view the complete command output by changing to the process work dir
"""


def test_summary_names_the_failing_process_and_its_last_error_line():
    summary = _nextflow_failure_summary(REAL.splitlines(keepends=True))
    assert summary == ("process ONT_BASECALL_DNA:DNA_READ_ANALYSIS:DoradoPreflight (dorado-preflight) failed: "
                       "ValueError: Dorado runtime canonical selector identity mismatch")


def test_summary_reads_the_retained_tail_and_stays_bounded():
    tail = _BoundedLogTail(max_lines=200, max_line_chars=4000, max_bytes=1_000_000)
    for line in REAL.replace("identity mismatch", "identity mismatch " + "x" * 2000).splitlines(keepends=True):
        tail.append(line)
    summary = _nextflow_failure_summary(tail)
    assert summary.startswith("process ONT_BASECALL_DNA:") and len(summary) == 400


def test_no_process_error_keeps_the_generic_message():
    assert _nextflow_failure_summary(["N E X T F L O W\n", "Launching main.nf\n"]) is None


def test_empty_command_error_still_names_the_process():
    text = "ERROR ~ Error executing process > 'A:B (1)'\nCommand error:\n  (empty)\nWork dir:\n  /w\n"
    assert _nextflow_failure_summary(text.splitlines()) == "process A:B (1) failed"
