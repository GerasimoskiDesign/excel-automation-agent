"""Run a generated script in a throwaway working directory with a timeout.

What this gives us: the script only sees *copies* of the sample files, runs
with a scrubbed environment (no API keys, no proxy settings), in an isolated
interpreter (`python -I`), and is killed if it runs longer than the timeout.

What it does NOT give us: a security boundary. The child process runs as the
same OS user and could still reach the file system or the network if it
really tried. That is acceptable for trusted, internal use combined with the
static check in `safety.py`; hostile code would need a container or VM.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 60.0
MAX_CAPTURE_CHARS = 8000

# Environment variables the child keeps. Everything else (API keys, proxy
# settings, cloud credentials, PYTHONPATH tricks) is dropped.
_KEEP_ENV = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "LANG", "LC_ALL", "TZ")


@dataclass
class SandboxResult:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_s: float
    output_files: dict[str, Path] = field(default_factory=dict)
    workdir: Path | None = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


def scrubbed_env(workdir: Path) -> dict[str, str]:
    """Minimal environment for the child: no secrets, no proxies, temp inside workdir."""
    env = {k: os.environ[k] for k in _KEEP_ENV if k in os.environ}
    tmp = str(workdir / "tmp")
    env.update(
        {
            "TEMP": tmp,
            "TMP": tmp,
            "TMPDIR": tmp,
            "HOME": str(workdir),
            "USERPROFILE": str(workdir),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "MPLBACKEND": "Agg",
            # Make accidental network use fail fast instead of using a proxy.
            "NO_PROXY": "*",
        }
    )
    return env


def _truncate(text: str) -> str:
    if len(text) <= MAX_CAPTURE_CHARS:
        return text
    half = MAX_CAPTURE_CHARS // 2
    return text[:half] + "\n...[truncated]...\n" + text[-half:]


def run_script(
    script: str,
    input_files: list[Path],
    output_name: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    workdir: Path | None = None,
    python: str | None = None,
) -> SandboxResult:
    """Copy inputs into a fresh work dir, run the script there, collect outputs.

    `workdir` lets callers keep the run folder (e.g. the API job folder); when
    omitted a temporary folder is created and left for the caller to inspect
    via `result.workdir`.
    """
    workdir = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="xlagent_run_"))
    inputs_dir, output_dir = workdir / "inputs", workdir / "output"
    for d in (inputs_dir, output_dir, workdir / "tmp"):
        d.mkdir(parents=True, exist_ok=True)
    copied = []
    for src in input_files:
        dst = inputs_dir / Path(src).name
        shutil.copy2(src, dst)
        copied.append(dst.relative_to(workdir).as_posix())
    script_path = workdir / "script.py"
    script_path.write_text(script, encoding="utf-8")

    cmd = [python or sys.executable, "-I", "script.py", "--output", f"output/{output_name}"]
    cmd += copied
    log.info("sandbox: running %s in %s (timeout %.0fs)", script_path.name, workdir, timeout_s)
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            cwd=workdir,
            env=scrubbed_env(workdir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            stdin=subprocess.DEVNULL,
        )
        returncode, stdout, stderr, timed_out = proc.returncode, proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired as exc:
        # subprocess.run kills the child it started (and only that child).
        returncode, timed_out = None, True
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode()
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode()
        stderr += f"\nTimed out after {timeout_s:.0f} seconds."
    duration = time.perf_counter() - started
    outputs = {p.name: p for p in sorted(output_dir.iterdir()) if p.is_file()}
    log.info(
        "sandbox: exit=%s timed_out=%s %.2fs outputs=%s",
        returncode,
        timed_out,
        duration,
        list(outputs),
    )
    return SandboxResult(
        returncode=returncode,
        stdout=_truncate(stdout),
        stderr=_truncate(stderr),
        timed_out=timed_out,
        duration_s=round(duration, 3),
        output_files=outputs,
        workdir=workdir,
    )
