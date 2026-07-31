"""Detach source launches from the terminal while preserving clean shutdown."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


_FOREGROUND_FLAG = "--foreground"
_NO_DETACH_FLAGS = {_FOREGROUND_FLAG, "--self-test"}


def _run_foreground() -> None:
    """Run the Tk application in the current process."""
    if _FOREGROUND_FLAG in sys.argv:
        sys.argv.remove(_FOREGROUND_FLAG)

    from .bootstrap import main as bootstrap_main

    bootstrap_main()


def _should_run_foreground() -> bool:
    """Avoid detaching packaged builds, tests, and explicitly foreground runs."""
    if getattr(sys, "frozen", False):
        return True
    return any(flag in sys.argv[1:] for flag in _NO_DETACH_FLAGS)


def _spawn_detached() -> None:
    """Start a detached GUI child and return control to the shell."""
    repository_root = Path(__file__).resolve().parent.parent
    script = repository_root / "run_app.py"
    command = [
        sys.executable,
        str(script),
        _FOREGROUND_FLAG,
        *sys.argv[1:],
    ]

    kwargs: dict[str, object] = {
        "cwd": str(repository_root),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }

    if os.name == "nt":
        detached_process = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        new_process_group = getattr(
            subprocess,
            "CREATE_NEW_PROCESS_GROUP",
            0x00000200,
        )
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        kwargs["creationflags"] = (
            detached_process | new_process_group | no_window
        )
    else:
        kwargs["start_new_session"] = True

    subprocess.Popen(command, **kwargs)


def main() -> None:
    """Launch the application, detaching ordinary source-code launches."""
    if _should_run_foreground():
        _run_foreground()
        return

    _spawn_detached()


if __name__ == "__main__":
    main()
