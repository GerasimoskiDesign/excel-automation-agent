"""Generate -> safety check -> sandbox run -> self-check -> repair, then package.

The loop is the core of the agent. Every attempt is recorded (code, safety
result, sandbox output, diff) so the UI can show the colleague exactly what
happened, and every failure is turned into concrete feedback for the next
attempt. It stops after `max_repairs` repairs: if four tries cannot satisfy
the checks, a human should look, not a fifth guess.
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from xlagent import prompts
from xlagent.agent.interview import Interview, spec_to_json
from xlagent.compare import DiffReport, Tolerance, compare_files
from xlagent.harness import assemble_script
from xlagent.llm.base import LLMError, LLMProvider, LLMRequest
from xlagent.llm.replay import session_key
from xlagent.packaging import build_zip
from xlagent.profiling import profile_files
from xlagent.safety import SafetyReport, check_script
from xlagent.sandbox import SandboxResult, run_script
from xlagent.spec import SpecSubmission

log = logging.getLogger(__name__)

DEFAULT_MAX_REPAIRS = 3
STDERR_TAIL = 3000

# Attempt outcomes
PASSED = "passed"
PASSED_UNVERIFIED = "passed_unverified"  # ran cleanly, no expected output to compare with
SAFETY_REJECTED = "safety_rejected"
CRASHED = "crashed"
TIMED_OUT = "timed_out"
NO_OUTPUT = "no_output"
MISMATCH = "mismatch"
NO_CODE = "no_code"


@dataclass
class Attempt:
    number: int
    stage: str
    code: str = ""
    notes: str = ""
    safety: SafetyReport | None = None
    sandbox: SandboxResult | None = None
    diff: DiffReport | None = None
    outcome: str = ""
    feedback: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "stage": self.stage,
            "outcome": self.outcome,
            "notes": self.notes,
            "safety": self.safety.summary() if self.safety else None,
            "returncode": self.sandbox.returncode if self.sandbox else None,
            "duration_s": self.sandbox.duration_s if self.sandbox else None,
            "diff": self.diff.summary() if self.diff else None,
            "cells_compared": self.diff.cells_compared if self.diff else 0,
        }


@dataclass
class BuildResult:
    status: str
    attempts: list[Attempt] = field(default_factory=list)
    script: str | None = None
    output_file: Path | None = None

    @property
    def ok(self) -> bool:
        return self.status in (PASSED, PASSED_UNVERIFIED)


def _tail(text: str, n: int = STDERR_TAIL) -> str:
    return text if len(text) <= n else "..." + text[-n:]


def _evaluate(
    attempt: Attempt,
    script: str,
    input_files: list[Path],
    output_name: str,
    expected_file: Path | None,
    timeout_s: float,
    run_dir: Path,
    tolerance: Tolerance,
) -> None:
    """Run one attempt in the sandbox and fill in outcome + feedback."""
    result = run_script(script, input_files, output_name, timeout_s=timeout_s, workdir=run_dir)
    attempt.sandbox = result
    if result.timed_out:
        attempt.outcome = TIMED_OUT
        attempt.feedback = (
            f"The test run was stopped after {timeout_s:.0f} seconds. Avoid row-by-row loops "
            "over large frames and make sure nothing waits for input."
        )
        return
    if result.returncode != 0:
        attempt.outcome = CRASHED
        attempt.feedback = "The test run crashed. stderr:\n" + _tail(result.stderr)
        return
    produced = result.output_files.get(output_name)
    if produced is None:
        attempt.outcome = NO_OUTPUT
        attempt.feedback = (
            f"The script finished but did not write {output_name} to output_path. "
            f"Files written: {sorted(result.output_files)}"
        )
        return
    if expected_file is None:
        attempt.outcome = PASSED_UNVERIFIED
        return
    diff = compare_files(expected_file, produced, tolerance)
    attempt.diff = diff
    if diff.ok:
        attempt.outcome = PASSED
    else:
        attempt.outcome = MISMATCH
        attempt.feedback = (
            "The script ran, but its output does not match the expected example:\n" + diff.summary()
        )


def build_tool(
    provider: LLMProvider,
    submission: SpecSubmission,
    input_files: list[Path],
    key: str,
    expected_file: Path | None = None,
    share_real_values: bool = False,
    max_repairs: int = DEFAULT_MAX_REPAIRS,
    timeout_s: float = 60.0,
    tolerance: Tolerance | None = None,
    run_root: Path | None = None,
    on_attempt: Callable[[Attempt], None] | None = None,
) -> BuildResult:
    """Write and test the script; up to 1 + max_repairs model attempts."""
    tolerance = tolerance or Tolerance()
    run_root = Path(run_root) if run_root else Path(tempfile.mkdtemp(prefix="xlagent_build_"))
    spec = submission.spec
    output_name = spec.output_format.file_name
    profile_text = profile_files(input_files, share_real_values).to_prompt_text()
    expected_text = (
        profile_files([expected_file], share_real_values).to_prompt_text()
        if expected_file
        else None
    )
    opening = prompts.generate_opening(
        spec_to_json(submission), profile_text, expected_text, [p.name for p in input_files]
    )
    messages: list[dict[str, Any]] = [{"role": "user", "content": opening}]
    result = BuildResult(status="failed")

    for number in range(1, max_repairs + 2):
        stage = "generate" if number == 1 else "repair"
        response = provider.complete(
            LLMRequest(
                system=prompts.GENERATE_SYSTEM,
                messages=messages,
                tools=prompts.GENERATE_TOOLS,
                max_tokens=32000,
                session_key=key,
                stage=stage,
            )
        )
        messages.append(response.as_assistant_message())
        attempt = Attempt(number=number, stage=stage)
        calls = [b for b in response.tool_uses() if b.get("name") == "write_script"]
        if not calls:
            attempt.outcome = NO_CODE
            attempt.feedback = "Please call the write_script tool with the complete code."
            messages.append({"role": "user", "content": attempt.feedback})
        else:
            call = calls[0]
            attempt.code = str(call.get("input", {}).get("code", ""))
            attempt.notes = str(call.get("input", {}).get("notes", ""))
            attempt.safety = check_script(attempt.code)
            if not attempt.safety.ok:
                attempt.outcome = SAFETY_REJECTED
                attempt.feedback = (
                    "The static safety check rejected the script, it was NOT run:\n"
                    + attempt.safety.summary()
                    + "\nRewrite it within the allowed imports and calls."
                )
            else:
                script = assemble_script(attempt.code, spec.title)
                _evaluate(
                    attempt,
                    script,
                    input_files,
                    output_name,
                    expected_file,
                    timeout_s,
                    run_root / f"attempt_{number}",
                    tolerance,
                )
                if attempt.outcome in (PASSED, PASSED_UNVERIFIED):
                    result.status = attempt.outcome
                    result.script = script
                    result.output_file = attempt.sandbox.output_files.get(output_name)
            if attempt.feedback:
                blocks = [
                    {
                        "type": "tool_result",
                        "tool_use_id": call["id"],
                        "content": attempt.feedback,
                        "is_error": True,
                    }
                ]
                blocks += [
                    {
                        "type": "tool_result",
                        "tool_use_id": b["id"],
                        "content": "Ignored: one write_script call per turn.",
                        "is_error": True,
                    }
                    for b in response.tool_uses()
                    if b["id"] != call["id"]
                ]
                messages.append({"role": "user", "content": blocks})
        log.info("build: attempt %d (%s) -> %s", number, stage, attempt.outcome)
        result.attempts.append(attempt)
        if on_attempt:
            on_attempt(attempt)
        if result.ok:
            break
    return result


# ---------------------------------------------------------------------------
# Non-interactive job (API, n8n, CLI `build`, eval)
# ---------------------------------------------------------------------------


@dataclass
class JobResult:
    status: str
    submission: SpecSubmission | None
    build: BuildResult | None
    zip_bytes: bytes | None = None
    error: str | None = None

    @property
    def assumptions(self) -> list[str]:
        return self.submission.assumptions if self.submission else []


def run_noninteractive_job(
    provider: LLMProvider,
    task: str,
    input_files: list[Path],
    expected_file: Path | None = None,
    share_real_values: bool = False,
    max_repairs: int = DEFAULT_MAX_REPAIRS,
    timeout_s: float = 60.0,
    run_root: Path | None = None,
    zip_dest: Path | None = None,
    on_attempt: Callable[[Attempt], None] | None = None,
) -> JobResult:
    """Spec (with listed assumptions) -> build loop -> zip, with no human in the loop."""
    input_files = sorted((Path(p) for p in input_files), key=lambda p: p.name)
    key = session_key("noninteractive", task, [p.name for p in input_files])
    try:
        profile_text = profile_files(input_files, share_real_values).to_prompt_text()
        expected_text = (
            profile_files([expected_file], share_real_values).to_prompt_text()
            if expected_file
            else None
        )
        interview = Interview(provider, task, profile_text, key, expected_text, interactive=False)
        turn = interview.start()
        submission = turn.submission
        if submission is None:
            raise LLMError("the model asked a question in non-interactive mode")
        build = build_tool(
            provider,
            submission,
            input_files,
            key,
            expected_file=expected_file,
            share_real_values=share_real_values,
            max_repairs=max_repairs,
            timeout_s=timeout_s,
            run_root=run_root,
            on_attempt=on_attempt,
        )
    except LLMError as exc:
        log.error("job failed: %s", exc)
        return JobResult(status="failed", submission=None, build=None, error=str(exc))
    if not build.ok:
        last = build.attempts[-1].outcome if build.attempts else "no attempts"
        return JobResult(
            status="failed",
            submission=submission,
            build=build,
            error=f"no attempt passed the checks after {len(build.attempts)} tries (last: {last})",
        )
    data = build_zip(
        build.script or "",
        submission,
        [p.name for p in input_files],
        len(build.attempts),
        dest=zip_dest,
    )
    return JobResult(status="done", submission=submission, build=build, zip_bytes=data)
