"""exec health check."""

import logging
import re
import selectors
import subprocess
import time
from dataclasses import dataclass
from typing import ClassVar

from powergslb.monitor.check.base import Check, Regex

__all__ = ['ExecCheck']


@dataclass
class ExecCheck(Check):
    """Run a command; healthy when the exit code and, optionally, the output match.

    The whole run is bounded by the check 'timeout'; on timeout the process is killed and the check fails. Only the
    first 'output_chunk' bytes of output are kept for the match; any excess is drained so the command can finish,
    but never buffered.

    :param args: Command and arguments, executed without a shell.
    :param expected_code: Exit code that counts as healthy.
    :param output_match: Regex searched in the decoded output; empty disables the match.
    :param redirect_error: Merge the command's stderr into its stdout so 'output_match' can see both.
    """
    name = 'exec'
    output_chunk: ClassVar[int] = 65536
    # Grace to reap a process that hit stdout EOF within the deadline but has not exited yet (the exit race).
    _reap_grace: ClassVar[float] = 0.1

    args: list[str]
    expected_code: int = 0
    output_match: Regex = ''
    redirect_error: bool = True

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.args or not all(isinstance(arg, str) for arg in self.args):
            raise ValueError("check parameter 'args' invalid")
        if not 0 <= self.expected_code <= 255:
            raise ValueError("check parameter 'expected_code' invalid")

    def execute(self) -> bool:
        deadline = time.monotonic() + self.timeout
        stderr = subprocess.STDOUT if self.redirect_error else subprocess.DEVNULL

        with subprocess.Popen(self.args, bufsize=0, stdout=subprocess.PIPE, stderr=stderr) as process:
            output, timed_out = self._read_output(process, deadline)
            if timed_out:
                process.kill()
                logging.error('exec command read timed out after %s seconds', self.timeout)
                return False

            try:
                code = process.wait(timeout=max(self._reap_grace, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                process.kill()
                logging.error('exec command wait timed out after %s seconds', self.timeout)
                return False

        if code != self.expected_code:
            return False

        if not self.output_match:
            return True

        return bool(re.search(self.output_match, output.decode('utf-8', errors='replace')))

    def _read_output(self, process: subprocess.Popen[bytes], deadline: float) -> tuple[bytes, bool]:
        """Drain the process stdout until EOF or the deadline, keeping only the first 'output_chunk' bytes.

        Excess output is read and discarded so a chatty command can still exit.

        :param deadline: Absolute time.monotonic() value the read must finish by.
        :returns: The kept bytes and a flag that is True when the deadline fired before EOF.
        """
        stdout = process.stdout
        assert stdout is not None

        output = b''
        selector = selectors.DefaultSelector()
        selector.register(stdout, selectors.EVENT_READ)
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    return output, True

                chunk = stdout.read(self.output_chunk)
                if not chunk:
                    return output, False

                if len(output) < self.output_chunk:
                    output += chunk[:self.output_chunk - len(output)]
        finally:
            selector.close()
