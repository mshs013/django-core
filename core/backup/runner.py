"""Run native tools safely. Pure Python (stdlib only).

* argument LISTS only, never a shell;
* a minimal environment (PATH, HOME, locale) plus the explicitly passed extras (PGPASSWORD...), so the web process's
  other secrets do not leak into child processes;
* credentials in option files created with mode 0600 and removed afterwards (:class:`SecretFile`);
* a hard timeout that kills the child; stderr is captured (bounded), redacted and never contains the password;
* the command line is never logged or put in errors beyond the tool's base name.
"""
from __future__ import annotations

import gzip
import os
import subprocess
import tempfile
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

from core.backup.adapters.redact import redact

_PASS_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR",
             "SYSTEMROOT", "WINDIR", "TEMP", "TMP")     # Windows: without SYSTEMROOT a child cannot open sockets (WSAEPROVIDERFAILINIT)
_CHUNK = 1 << 20
_STDERR_LIMIT = 8000


class ToolFailed(RuntimeError):
    """A native tool failed. The message is already redacted."""


class SecretFile:
    """A 0600 temp file for tool credentials (``--defaults-extra-file``); always removed on exit."""

    def __init__(self, content: str, suffix: str = ".cnf"):
        self.content, self.suffix, self.path = content, suffix, None

    def __enter__(self) -> str:
        fd, self.path = tempfile.mkstemp(suffix=self.suffix, prefix="core-backup-")
        if hasattr(os, "fchmod"):             # POSIX only (mkstemp already creates 0600 there); Windows uses the folder ACL
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(self.content)
        return self.path

    def __exit__(self, *exc) -> None:
        if self.path and os.path.exists(self.path):
            os.unlink(self.path)


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in _PASS_ENV}
    env.update(extra or {})
    return env


def run_tool(argv: Sequence[str], *, env: dict[str, str] | None = None, secrets: Sequence[str] = (), timeout: int = 3600,
             stdout_to: Path | None = None, gzip_level: int | None = None, stdin_from: Path | None = None,
             gunzip_stdin: bool = False, should_cancel: Callable[[], bool] | None = None) -> str:
    """Run ``argv``; return stdout text unless it is streamed to ``stdout_to`` (optionally gzip-compressed).

    ``stdin_from`` feeds a file (optionally gunzipped) to the tool. Raises ToolFailed with a redacted message on a non-zero exit,
    a timeout or a cancel request.
    """
    name = Path(argv[0]).name
    stderr_file = tempfile.TemporaryFile()
    try:
        proc = subprocess.Popen(list(argv), env=child_env(env), shell=False, stdin=subprocess.PIPE if stdin_from else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=stderr_file)
    except OSError as exc:
        stderr_file.close()
        raise ToolFailed(redact(f"{name} could not be started: {exc.strerror or exc}", secrets)) from None

    state = {"timeout": False, "cancel": False}
    stop = threading.Event()

    def watchdog() -> None:
        waited = 0.0
        while not stop.wait(1.0):
            waited += 1.0
            if should_cancel is not None and should_cancel():
                state["cancel"] = True
                proc.kill()
                return
            if waited >= timeout:
                state["timeout"] = True
                proc.kill()
                return

    watcher = threading.Thread(target=watchdog, daemon=True)
    watcher.start()
    feeder = None
    out_chunks: list[bytes] = []
    try:
        if stdin_from is not None:
            feeder = threading.Thread(target=_feed, args=(proc, stdin_from, gunzip_stdin), daemon=True)
            feeder.start()
        if stdout_to is not None:
            opener = (lambda: gzip.open(stdout_to, "wb", compresslevel=gzip_level)) if gzip_level is not None else (lambda: open(stdout_to, "wb"))
            with opener() as out:
                while chunk := proc.stdout.read(_CHUNK):
                    out.write(chunk)
        else:
            while chunk := proc.stdout.read(_CHUNK):
                out_chunks.append(chunk)
        proc.wait()
    finally:
        stop.set()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        if feeder is not None:
            feeder.join(timeout=5)
        proc.stdout.close()
        stderr_file.seek(0)
        err = stderr_file.read(_STDERR_LIMIT).decode("utf-8", "replace").strip()
        stderr_file.close()
    if state["cancel"]:
        raise ToolFailed(f"{name} was cancelled")
    if state["timeout"]:
        raise ToolFailed(f"{name} timed out after {timeout}s")
    if proc.returncode != 0:
        raise ToolFailed(redact(f"{name} failed (exit {proc.returncode}): {err}", secrets))
    return b"".join(out_chunks).decode("utf-8", "replace")


def _feed(proc: subprocess.Popen, path: Path, gunzip: bool) -> None:
    try:
        with (gzip.open(path, "rb") if gunzip else open(path, "rb")) as src:
            while block := src.read(_CHUNK):
                proc.stdin.write(block)
    except (BrokenPipeError, OSError, EOFError):
        pass                       # the tool exited early; its exit status and stderr explain why
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
