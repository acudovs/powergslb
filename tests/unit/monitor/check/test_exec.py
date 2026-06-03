# pylint: disable=missing-function-docstring

"""Tests for ExecCheck.execute() against real short-lived commands."""

import os
import resource
import time
from types import SimpleNamespace
from typing import Any

import pytest

from powergslb.monitor.check import exec as exec_module
from powergslb.monitor.check.exec import ExecCheck


def _check(**overrides: Any) -> ExecCheck:
    params: dict[str, Any] = {'interval': 10, 'timeout': 5, 'fall': 2, 'rise': 2, 'args': ['sh', '-c', 'exit 0']}
    params.update(overrides)
    return ExecCheck(**params)


# expected_code: exit status must match (default 0)

def test_zero_exit_is_healthy() -> None:
    assert _check(args=['sh', '-c', 'exit 0']).execute() is True


def test_nonzero_exit_is_unhealthy() -> None:
    assert _check(args=['sh', '-c', 'exit 1']).execute() is False


def test_expected_code_exact_match_healthy() -> None:
    assert _check(args=['sh', '-c', 'exit 3'], expected_code=3).execute() is True


def test_expected_code_mismatch_unhealthy() -> None:
    assert _check(args=['sh', '-c', 'exit 0'], expected_code=1).execute() is False


# __post_init__ validation

@pytest.mark.parametrize('args', [[], [1, 2], ['ok', 3]])
def test_invalid_args_rejected(args: Any) -> None:
    with pytest.raises(ValueError, match="check parameter 'args' invalid"):
        _check(args=args)


def test_invalid_output_match_regex_rejected() -> None:
    with pytest.raises(ValueError, match="check parameter 'output_match' invalid"):
        _check(output_match='[')


@pytest.mark.parametrize('expected_code', [-1, 256, 2555])
def test_out_of_range_expected_code_rejected(expected_code: int) -> None:
    with pytest.raises(ValueError, match="check parameter 'expected_code' invalid"):
        _check(expected_code=expected_code)


# output_match: regex via re.search

def test_output_match_found_healthy() -> None:
    assert _check(args=['sh', '-c', 'echo service OK'], output_match=r'OK').execute() is True


def test_output_match_not_found_unhealthy() -> None:
    assert _check(args=['sh', '-c', 'echo service down'], output_match=r'OK').execute() is False


def test_output_match_regex() -> None:
    assert _check(args=['sh', '-c', 'printf %s \'{"status":"ready"}\''],
                  output_match=r'"status"\s*:\s*"ready"').execute() is True


# redirect_error: merge stderr into stdout for the match

def test_redirect_error_true_sees_stderr() -> None:
    assert _check(args=['sh', '-c', 'echo boom >&2'], output_match=r'boom', redirect_error=True).execute() is True


def test_redirect_error_false_hides_stderr() -> None:
    assert _check(args=['sh', '-c', 'echo boom >&2'], output_match=r'boom', redirect_error=False).execute() is False


# output_chunk: only the first bytes are kept for the match; the rest is drained

def test_output_truncated_to_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ExecCheck, 'output_chunk', 2)
    # 'OK' is within the first 2 bytes; 'xx' is past the kept window and must not match.
    assert _check(args=['sh', '-c', 'printf OKxx'], output_match=r'OK').execute() is True
    assert _check(args=['sh', '-c', 'printf OKxx'], output_match=r'xx').execute() is False


# timeout: a command that outlives the deadline is killed and fails

def test_timeout_kills_and_unhealthy() -> None:
    assert _check(args=['sleep', '5'], timeout=1).execute() is False


def test_closed_stdout_still_bounded_by_timeout() -> None:
    # stdout reaches EOF immediately, but the process keeps running past the deadline: process.wait() must time out.
    assert _check(args=['sh', '-c', 'exec 1>&-; sleep 5'], timeout=1, redirect_error=False).execute() is False


def test_read_output_handles_high_fd() -> None:
    # select.select() raises ValueError for a descriptor >= FD_SETSIZE (1024); selectors (epoll/poll) handles it.
    # Force stdout onto a high-numbered fd and confirm _read_output drains it instead of raising.
    high_fd = 1100
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft <= high_fd:
        if hard != resource.RLIM_INFINITY and hard <= high_fd:
            pytest.skip('cannot raise RLIMIT_NOFILE above the high fd')
        resource.setrlimit(resource.RLIMIT_NOFILE, (high_fd + 1, hard))

    read_fd, write_fd = os.pipe()
    try:
        os.dup2(read_fd, high_fd)
    finally:
        os.close(read_fd)
        if soft <= high_fd:
            resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))

    os.write(write_fd, b'OK')
    os.close(write_fd)  # EOF after 'OK'

    stdout = os.fdopen(high_fd, 'rb', buffering=0)
    try:
        process = SimpleNamespace(stdout=stdout)
        # pylint: disable-next=protected-access
        output, timed_out = _check()._read_output(process, time.monotonic() + 5)  # type: ignore[arg-type]
        assert output == b'OK'
        assert timed_out is False
    finally:
        stdout.close()


def test_clean_eof_reaps_within_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    # A command closes stdout (clean EOF) within the deadline, then exits a moment later, but the deadline is already
    # exhausted by the time we reap. Replace only the exec module's monotonic so _read_output sees time left while the
    # reap sees the deadline passed (subprocess.wait keeps real timing). With max(0.0, ...) the reap gets 0 and kills a
    # healthy process; the _reap_grace floor lets it finish.
    deadline_base = 1000.0
    times = iter([deadline_base, deadline_base])  # 1: deadline calc, 2: _read_output remaining (still positive)
    monkeypatch.setattr(exec_module, 'time',
                        SimpleNamespace(monotonic=lambda: next(times, deadline_base + 10.0)))
    check = _check(args=['sh', '-c', 'exec 1>&-; sleep 0.02'], timeout=1, redirect_error=False)
    assert check.execute() is True
