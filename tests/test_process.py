"""Subprocess helpers, driven with the running interpreter as the child."""

from __future__ import annotations

import sys

import pytest

from chaptercut.pipeline.process import (
    ProcessError,
    ProcessTimeout,
    run,
    run_checked,
    stream_lines,
)


def python(*code: str) -> list[str]:
    return [sys.executable, "-c", "\n".join(code)]


async def test_stdout_is_captured() -> None:
    result = await run(python("print('hello')"), timeout=30)
    assert result.returncode == 0
    assert result.stdout.strip() == "hello"


async def test_stderr_is_captured() -> None:
    result = await run(python("import sys", "sys.stderr.write('warned')"), timeout=30)
    assert result.stderr == "warned"


async def test_a_non_zero_exit_raises_with_the_stderr_attached() -> None:
    with pytest.raises(ProcessError) as caught:
        await run(
            python("import sys", "sys.stderr.write('it broke')", "sys.exit(3)"),
            timeout=30,
        )
    assert caught.value.returncode == 3
    assert "it broke" in caught.value.stderr


async def test_run_checked_turns_a_timeout_into_a_process_timeout() -> None:
    with pytest.raises(ProcessTimeout) as caught:
        await run_checked(python("import time", "time.sleep(30)"), timeout=0.5)
    assert caught.value.seconds == 0.5


async def test_a_timed_out_process_is_killed() -> None:
    # Nothing to assert beyond returning promptly: a surviving child would
    # hold the pipes open and hang this test.
    with pytest.raises(ProcessTimeout):
        await run_checked(python("import time", "time.sleep(60)"), timeout=0.5)


async def test_stream_lines_delivers_each_line() -> None:
    seen: list[str] = []
    await stream_lines(
        python("for i in range(5):", "    print(f'line {i}', flush=True)"),
        timeout=30,
        on_line=seen.append,
    )
    assert seen == [f"line {i}" for i in range(5)]


async def test_stream_lines_without_a_callback_is_fine() -> None:
    result = await stream_lines(python("print('quiet')"), timeout=30)
    assert result.returncode == 0


async def test_stream_lines_reports_a_failure_with_stderr() -> None:
    with pytest.raises(ProcessError) as caught:
        await stream_lines(
            python("import sys", "sys.stderr.write('bad args')", "sys.exit(2)"),
            timeout=30,
        )
    assert "bad args" in caught.value.stderr


async def test_stream_lines_times_out() -> None:
    with pytest.raises(ProcessTimeout):
        await stream_lines(python("import time", "time.sleep(60)"), timeout=0.5)


async def test_the_error_message_names_the_program_and_truncates_stderr() -> None:
    with pytest.raises(ProcessError) as caught:
        await run(
            python("import sys", "sys.stderr.write('x' * 5000)", "sys.exit(1)"),
            timeout=30,
        )
    message = str(caught.value)
    assert "exited 1" in message
    assert len(message) < 3000
