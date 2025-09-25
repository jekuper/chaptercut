"""Async subprocess helpers.

Everything external runs out of process with a timeout, so a yt-dlp hang or an
ffmpeg crash can never take the bot down with it.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from chaptercut.logging import get_logger

log = get_logger(__name__)

STDERR_TAIL_CHARS = 2000


class ProcessError(RuntimeError):
    def __init__(self, argv: Sequence[str], returncode: int, stderr: str) -> None:
        self.program = argv[0] if argv else "?"
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"{self.program} exited {returncode}: {stderr[-STDERR_TAIL_CHARS:]}")


class ProcessTimeout(RuntimeError):
    def __init__(self, argv: Sequence[str], seconds: float) -> None:
        self.program = argv[0] if argv else "?"
        self.seconds = seconds
        super().__init__(f"{self.program} timed out after {seconds:.0f}s")


@dataclass(slots=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


def _spawn_kwargs() -> dict[str, object]:
    """Put the child in its own group so a timeout kills its children too."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _kill_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        if sys.platform == "win32":
            process.kill()
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):  # pragma: no cover - racy teardown
        pass


async def run(
    argv: Sequence[str],
    timeout: float,
    cwd: str | None = None,
) -> ProcessResult:
    """Run to completion, capturing output. Raises on non-zero exit or timeout."""
    log.debug("process.run", program=argv[0], argc=len(argv))
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
        cwd=cwd,
        **_spawn_kwargs(),  # pyright: ignore[reportArgumentType]
    )
    try:
        raw_out, raw_err = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except (TimeoutError, asyncio.CancelledError):
        _kill_tree(process)
        with contextlib.suppress(Exception):
            await process.wait()
        raise
    stdout = raw_out.decode("utf-8", "replace")
    stderr = raw_err.decode("utf-8", "replace")
    if process.returncode != 0:
        raise ProcessError(argv, process.returncode or -1, stderr)
    return ProcessResult(returncode=0, stdout=stdout, stderr=stderr)


async def run_checked(argv: Sequence[str], timeout: float, cwd: str | None = None) -> ProcessResult:
    """`run`, but a timeout becomes a ProcessTimeout instead of a bare TimeoutError."""
    try:
        return await run(argv, timeout=timeout, cwd=cwd)
    except TimeoutError as exc:
        raise ProcessTimeout(argv, timeout) from exc


async def stream_lines(
    argv: Sequence[str],
    timeout: float,
    on_line: Callable[[str], None] | None = None,
    cwd: str | None = None,
) -> ProcessResult:
    """Run while feeding stdout lines to `on_line`. stderr is buffered for errors."""
    log.debug("process.stream", program=argv[0], argc=len(argv))
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
        cwd=cwd,
        **_spawn_kwargs(),  # pyright: ignore[reportArgumentType]
    )

    stderr_chunks: list[str] = []

    async def drain_stderr() -> None:
        assert process.stderr is not None
        async for raw in process.stderr:
            stderr_chunks.append(raw.decode("utf-8", "replace"))

    async def pump_stdout() -> None:
        assert process.stdout is not None
        async for raw in process.stdout:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line and on_line is not None:
                on_line(line)

    stderr_task = asyncio.create_task(drain_stderr())
    stdout_task = asyncio.create_task(pump_stdout())
    try:
        await asyncio.wait_for(
            asyncio.gather(stdout_task, stderr_task, process.wait()), timeout=timeout
        )
    except (TimeoutError, asyncio.CancelledError) as exc:
        stdout_task.cancel()
        stderr_task.cancel()
        _kill_tree(process)
        with contextlib.suppress(Exception):
            await process.wait()
        # A cancellation is a shutdown, not a stalled download: let it through.
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise ProcessTimeout(argv, timeout) from exc

    stderr = "".join(stderr_chunks)
    if process.returncode != 0:
        raise ProcessError(argv, process.returncode or -1, stderr)
    return ProcessResult(returncode=0, stdout="", stderr=stderr)
