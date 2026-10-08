from __future__ import annotations

import subprocess
import sys

def run_mmseqs(mmseqs_bin: str, params: list, env: dict, capture_output: bool = True):
    """
    Run MMseqs2 command with robust output handling.

    Uses Popen with threaded output streaming to prevent pipe buffer deadlock
    while preserving full logging capability. Allows unlimited runtime for
    long MSA searches.

    Args:
        mmseqs_bin: Path to mmseqs binary
        params: Command parameters
        env: Environment dict
        capture_output: If True, capture and return stdout/stderr

    Returns:
        subprocess.CompletedProcess with returncode and captured output

    Raises:
        RuntimeError: If mmseqs command fails (non-zero exit)

    Note:
        This implementation uses threading to read stdout/stderr concurrently,
        preventing the deadlock that can occur with subprocess.run(capture_output=True)
        when a child process produces more output than the OS pipe buffer can hold.
        See: https://docs.python.org/3/library/subprocess.html#subprocess.Popen.communicate
    """
    import threading
    from io import StringIO

    cmd = [str(mmseqs_bin)] + [str(p) for p in params]
    module = params[0] if params else "unknown"

    # Suppress verbose parameter list in logs
    env_copy = env.copy()
    env_copy["MMSEQS_CALL_DEPTH"] = "1"

    if not capture_output:
        # Simple case - no capture needed
        result = subprocess.run(cmd, env=env_copy)
        if result.returncode != 0:
            raise RuntimeError(f"MMseqs2 {module} failed with exit code {result.returncode}")
        return result

    # Use Popen with threaded output reading to prevent pipe buffer deadlock
    stdout_buffer = StringIO()
    stderr_buffer = StringIO()

    def stream_output(pipe, buffer, echo_to=None):
        """Read from pipe and optionally echo to another stream."""
        try:
            for line in iter(pipe.readline, ''):
                buffer.write(line)
                if echo_to:
                    echo_to.write(line)
                    echo_to.flush()
            pipe.close()
        except Exception:
            pass

    proc = subprocess.Popen(
        cmd,
        env=env_copy,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1  # Line buffered
    )

    # Start threads to read output concurrently (prevents pipe buffer deadlock)
    stdout_thread = threading.Thread(
        target=stream_output,
        args=(proc.stdout, stdout_buffer, None)  # Don't echo stdout (too verbose)
    )
    stderr_thread = threading.Thread(
        target=stream_output,
        args=(proc.stderr, stderr_buffer, sys.stderr)  # Echo errors
    )

    stdout_thread.daemon = True
    stderr_thread.daemon = True
    stdout_thread.start()
    stderr_thread.start()

    # Wait for process to complete (no timeout - allow long searches)
    proc.wait()

    # Wait for output threads to finish reading
    stdout_thread.join(timeout=5.0)
    stderr_thread.join(timeout=5.0)

    stdout_content = stdout_buffer.getvalue()
    stderr_content = stderr_buffer.getvalue()

    if proc.returncode != 0:
        error_msg = stderr_content.strip() if stderr_content else f"Exit code {proc.returncode}"
        raise RuntimeError(f"MMseqs2 {module} failed: {error_msg}")

    return subprocess.CompletedProcess(cmd, proc.returncode, stdout_content, stderr_content)
