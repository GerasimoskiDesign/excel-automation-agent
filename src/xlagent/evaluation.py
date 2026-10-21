"""Run the three example tasks end to end and report pass/fail.

Why an eval and not only unit tests: unit tests prove each part works; the
eval proves the parts work *together* on realistic tasks - profile, spec,
generate, safety check, sandbox, compare, repair, package - and it is the
number a reviewer can check by running one command.
"""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from xlagent.agent.pipeline import run_noninteractive_job
from xlagent.examples_gen import EXAMPLE_NAMES
from xlagent.llm.base import LLMProvider

log = logging.getLogger(__name__)


@dataclass
class EvalRow:
    example: str
    passed: bool
    status: str
    attempts: int
    outcomes: list[str] = field(default_factory=list)
    cells_compared: int = 0
    assumptions: int = 0
    error: str | None = None


def load_example(folder: Path) -> tuple[str, list[Path], Path | None]:
    task = (folder / "task.md").read_text(encoding="utf-8")
    inputs = sorted(p for p in (folder / "inputs").iterdir() if p.is_file())
    expected = sorted((folder / "expected").glob("*"))
    return task, inputs, expected[0] if expected else None


def run_eval(
    provider: LLMProvider,
    examples_dir: Path,
    names: tuple[str, ...] = EXAMPLE_NAMES,
    timeout_s: float = 60.0,
    work_dir: Path | None = None,
) -> list[EvalRow]:
    rows = []
    work_dir = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="xlagent_eval_"))
    for name in names:
        task, inputs, expected = load_example(Path(examples_dir) / name)
        job = run_noninteractive_job(
            provider,
            task,
            inputs,
            expected_file=expected,
            timeout_s=timeout_s,
            run_root=work_dir / name,
            zip_dest=work_dir / f"{name}.zip",
        )
        attempts = job.build.attempts if job.build else []
        last_diff = attempts[-1].diff if attempts else None
        row = EvalRow(
            example=name,
            passed=job.status == "done" and job.build is not None and job.build.status == "passed",
            status=job.status,
            attempts=len(attempts),
            outcomes=[a.outcome for a in attempts],
            cells_compared=last_diff.cells_compared if last_diff else 0,
            assumptions=len(job.assumptions),
            error=job.error,
        )
        log.info(
            "eval: %-22s %s  attempts=%d  outcomes=%s",
            name,
            "PASS" if row.passed else "FAIL",
            row.attempts,
            " -> ".join(row.outcomes),
        )
        rows.append(row)
    return rows


def results_markdown(rows: list[EvalRow]) -> str:
    lines = [
        "| Example | Result | Attempts | Attempt outcomes | Cells compared | Assumptions listed |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r.example} | {'pass' if r.passed else 'FAIL'} | {r.attempts} | "
            f"{' -> '.join(r.outcomes)} | {r.cells_compared} | {r.assumptions} |"
        )
    return "\n".join(lines) + "\n"


def write_report(rows: list[EvalRow], report_dir: Path, examples_dir: Path | None = None) -> None:
    """Write eval_results.json/.md and the README charts (charts need matplotlib)."""
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "eval_results.json").write_text(
        json.dumps([asdict(r) for r in rows], indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    (report_dir / "eval_results.md").write_text(
        results_markdown(rows), encoding="utf-8", newline="\n"
    )
    try:
        from xlagent import charts
    except ImportError:  # matplotlib is a dev dependency
        log.warning("matplotlib not installed; skipping charts")
        return
    charts.attempts_chart(rows, report_dir / "img" / "eval_attempts.png")
    if examples_dir is not None:
        from xlagent.profiling import mask_frame
        from xlagent.xlsx_io import read_tables

        sample_file = Path(examples_dir) / "sales_consolidation" / "inputs"
        first = sorted(sample_file.glob("sales_station_*.xlsx"))[0]
        real = next(iter(read_tables(first).values())).head(5)
        charts.masking_chart(real, mask_frame(real), report_dir / "img" / "masking.png")
    log.info("report written to %s", report_dir)
