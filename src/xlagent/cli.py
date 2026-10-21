"""Command line: python -m xlagent <command>."""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from xlagent.config import Settings

log = logging.getLogger("xlagent")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("httpx", "httpx2", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def cmd_examples(args: argparse.Namespace) -> int:
    from xlagent.examples_gen import generate_examples

    generate_examples(Path(args.out), seed=args.seed)
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from xlagent.evaluation import results_markdown, run_eval, write_report
    from xlagent.llm.factory import make_provider

    settings = Settings.from_env()
    # The eval runs in replay mode unless --live is given explicitly.
    settings = replace(settings, replay=not args.live)
    rows = run_eval(
        make_provider(settings), Path(args.examples_dir), timeout_s=settings.sandbox_timeout_s
    )
    log.info("results:\n%s", results_markdown(rows))
    if args.report_dir:
        write_report(rows, Path(args.report_dir), Path(args.examples_dir))
    passed = sum(r.passed for r in rows)
    log.info("eval: %d/%d examples passed", passed, len(rows))
    return 0 if passed == len(rows) else 1


def cmd_build(args: argparse.Namespace) -> int:
    from xlagent.agent.pipeline import run_noninteractive_job
    from xlagent.llm.factory import make_provider

    settings = Settings.from_env()
    task = Path(args.task_file).read_text(encoding="utf-8")
    inputs: list[Path] = []
    for raw in args.inputs:
        p = Path(raw)
        inputs.extend(sorted(x for x in p.iterdir() if x.is_file()) if p.is_dir() else [p])
    job = run_noninteractive_job(
        make_provider(settings),
        task,
        inputs,
        expected_file=Path(args.expected) if args.expected else None,
        share_real_values=args.share_real_values,
        timeout_s=settings.sandbox_timeout_s,
        zip_dest=Path(args.out),
    )
    for a in job.assumptions:
        log.info("assumption: %s", a)
    if job.status != "done":
        log.error("build failed: %s", job.error)
        return 1
    log.info("deliverable written to %s", args.out)
    return 0


def cmd_app(args: argparse.Namespace) -> int:
    app_path = Path(__file__).with_name("app.py")
    cmd = [sys.executable, "-m", "streamlit", "run", str(app_path), "--server.port", str(args.port)]
    if args.headless:
        cmd += ["--server.headless", "true"]
    return subprocess.call(cmd)


def cmd_api(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("xlagent.api:app", host=args.host, port=args.port, log_level="info")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m xlagent", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("examples", help="generate the synthetic example tasks")
    p.add_argument("--out", default="examples")
    p.add_argument("--seed", type=int, default=42)
    p.set_defaults(func=cmd_examples)

    p = sub.add_parser("eval", help="run all examples end to end and report pass/fail")
    p.add_argument("--examples-dir", default="examples")
    p.add_argument("--report-dir", default=None, help="write results + charts here (e.g. docs)")
    p.add_argument("--live", action="store_true", help="call the real API instead of replay")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("build", help="non-interactive build: task + files -> deliverable zip")
    p.add_argument("--task-file", required=True)
    p.add_argument("--inputs", nargs="+", required=True, help="files or folders")
    p.add_argument("--expected", default=None, help="optional expected output for self-check")
    p.add_argument("--out", default="out/deliverable.zip")
    p.add_argument("--share-real-values", action="store_true")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("app", help="start the Streamlit app")
    p.add_argument("--port", type=int, default=8501)
    p.add_argument("--headless", action="store_true")
    p.set_defaults(func=cmd_app)

    p = sub.add_parser("api", help="start the HTTP API (used by n8n)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_api)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    return int(args.func(args))
