"""Streamlit UI: upload -> profile -> interview -> confirm spec -> build -> download.

Run with `python -m xlagent app`. All agent logic lives in xlagent.agent; this
file only manages page state and rendering.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from xlagent.agent.interview import Interview, InterviewTurn
from xlagent.agent.pipeline import PASSED, PASSED_UNVERIFIED, Attempt, build_tool
from xlagent.config import Settings
from xlagent.llm.factory import make_provider
from xlagent.llm.replay import ReplayProvider, session_key
from xlagent.packaging import build_zip
from xlagent.profiling import profile_files

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")

DEMO = "sales_consolidation"
OUTCOME_LABEL = {
    "passed": "Passed - output matches your example",
    "passed_unverified": "Ran without errors (no example output to compare with)",
    "safety_rejected": "Rejected by the safety check (not run)",
    "crashed": "Crashed during the test run",
    "timed_out": "Stopped: took too long",
    "no_output": "Finished but wrote no output file",
    "mismatch": "Ran, but the output differs from your example",
    "no_code": "The model returned no code",
}


def _examples_dir() -> Path:
    for candidate in (Path.cwd() / "examples", Path(__file__).resolve().parents[2] / "examples"):
        if (candidate / DEMO).exists():
            return candidate
    return Path.cwd() / "examples"


def _state():
    ss = st.session_state
    defaults = {
        "files": [],
        "expected": None,
        "task": "",
        "share_real": False,
        "interview": None,
        "turn": None,
        "confirmed": False,
        "build": None,
        "zip": None,
        "provider": None,
        "workdir": tempfile.mkdtemp(prefix="xlagent_ui_"),
        "suggested": [],
    }
    for key, value in defaults.items():
        ss.setdefault(key, value)
    return ss


def _reset_agent(ss) -> None:
    for key in ("interview", "turn", "build", "zip", "provider"):
        ss[key] = None
    ss["confirmed"] = False


def _save_uploads(ss, uploads, expected_upload) -> None:
    folder = Path(ss["workdir"])
    paths = []
    for up in uploads or []:
        path = folder / "inputs" / Path(up.name).name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(up.getvalue())
        paths.append(path)
    ss["files"] = sorted(paths, key=lambda p: p.name)
    if expected_upload is not None:
        path = folder / "expected" / Path(expected_upload.name).name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(expected_upload.getvalue())
        ss["expected"] = path


def _load_demo(ss) -> None:
    folder = _examples_dir() / DEMO
    ss["files"] = sorted((folder / "inputs").iterdir(), key=lambda p: p.name)
    ss["expected"] = sorted((folder / "expected").iterdir())[0]
    ss["task"] = (folder / "task.md").read_text(encoding="utf-8")
    _reset_agent(ss)


def sidebar(ss, settings: Settings) -> None:
    st.sidebar.header("xlagent")
    if settings.replay:
        st.sidebar.info(
            "Replay mode: the model's answers come from recorded fixtures, so only the demo "
            "example works. Set XLAGENT_REPLAY=0 and ANTHROPIC_API_KEY for live use."
        )
    else:
        st.sidebar.success(f"Live mode: {settings.model}")
    if st.sidebar.button("Load demo example (sales consolidation)", use_container_width=True):
        _load_demo(ss)
        st.rerun()
    if st.sidebar.button("Start over", use_container_width=True):
        _reset_agent(ss)
        ss["files"], ss["expected"], ss["task"] = [], None, ""
        st.rerun()


def step_upload(ss) -> None:
    st.subheader("1. Your files and your task")
    if not ss["files"]:
        uploads = st.file_uploader(
            "Sample input files (.xlsx / .csv)", type=["xlsx", "csv"], accept_multiple_files=True
        )
        expected = st.file_uploader("Optional: an example of the expected output", type=["xlsx"])
        if uploads:
            _save_uploads(ss, uploads, expected)
    if ss["files"]:
        names = ", ".join(p.name for p in ss["files"])
        st.caption(f"Input files: {names}")
        if ss["expected"]:
            st.caption(f"Expected output example: {Path(ss['expected']).name}")
    st.text_area("Describe the task in your own words", height=220, key="task")


def step_profile(ss) -> str:
    st.subheader("2. What the agent will see")
    st.toggle("Share real sample values with the model", key="share_real")
    if ss["share_real"]:
        st.warning("Up to 5 real rows per sheet will be sent to the model.")
    else:
        st.info(
            "Privacy: only sheet names, column names, data types, row counts and up to 5 "
            "masked rows per sheet are sent (letters become x, digits become 9, dates a fixed "
            "date). Your full files never leave this machine; scripts run locally."
        )
    profile = profile_files(ss["files"], ss["share_real"])
    with st.expander(f"Profile of {len(profile.sheets)} sheet(s) sent to the model"):
        for sheet in profile.sheets[:6]:
            st.markdown(f"**{sheet.file}** / {sheet.sheet} - {sheet.rows} rows")
            st.dataframe(pd.DataFrame(sheet.sample_rows), hide_index=True)
        if len(profile.sheets) > 6:
            st.caption(f"... and {len(profile.sheets) - 6} more sheets")
    return profile.to_prompt_text()


def _render_turn(ss, turn: InterviewTurn) -> None:
    interview: Interview = ss["interview"]
    for speaker, text in interview.transcript:
        if speaker == "agent" and text.startswith("Proposed spec"):
            continue
        with st.chat_message("assistant" if speaker == "agent" else "user"):
            st.write(text)
    if turn.question is not None:
        if turn.question.why:
            st.caption(f"Why this matters: {turn.question.why}")
        suggestion = ss["suggested"][0] if ss["suggested"] else ""
        with st.form(f"answer_{len(interview.transcript)}"):
            if turn.question.options:
                st.caption("Suggested answers: " + " | ".join(turn.question.options))
            answer = st.text_input("Your answer", value=suggestion)
            if st.form_submit_button("Send answer") and answer.strip():
                if ss["suggested"]:
                    ss["suggested"].pop(0)
                with st.spinner("Thinking..."):
                    ss["turn"] = interview.answer(answer.strip())
                st.rerun()


def step_interview(ss, settings: Settings, profile_text: str) -> None:
    st.subheader("3. Interview")
    key = session_key("interactive", ss["task"], [p.name for p in ss["files"]])
    if ss["interview"] is None:
        if st.button("Start the interview", type="primary", disabled=len(ss["task"]) < 10):
            provider = make_provider(settings)
            if isinstance(provider, ReplayProvider):
                if not provider.has_session(key):
                    st.error(
                        "Replay mode has no recording for this task and these files. Use "
                        "'Load demo example' in the sidebar, or switch to live mode."
                    )
                    return
                ss["suggested"] = provider.recorded_user_turns(key)
            ss["provider"] = provider
            expected_text = (
                profile_files([ss["expected"]], ss["share_real"]).to_prompt_text()
                if ss["expected"]
                else None
            )
            ss["interview"] = Interview(provider, ss["task"], profile_text, key, expected_text)
            with st.spinner("Reading your files and task..."):
                ss["turn"] = ss["interview"].start()
            st.rerun()
        return
    _render_turn(ss, ss["turn"])


def step_spec(ss) -> None:
    turn: InterviewTurn = ss["turn"]
    st.subheader("4. Confirm the requirements")
    st.write("This is what the script will do. Please check it carefully.")
    st.json(turn.submission.spec.model_dump(mode="json"), expanded=True)
    if turn.submission.assumptions:
        st.markdown("**Assumptions:**\n" + "\n".join(f"- {a}" for a in turn.submission.assumptions))
    if ss["confirmed"]:
        st.success("Spec confirmed.")
        return
    col1, col2 = st.columns(2)
    if col1.button("Confirm spec", type="primary"):
        ss["confirmed"] = True
        st.rerun()
    with col2.form("changes"):
        feedback = st.text_input("Or describe what should change")
        if st.form_submit_button("Request changes") and feedback.strip():
            with st.spinner("Revising..."):
                ss["turn"] = ss["interview"].request_changes(feedback.strip())
            st.rerun()


def _render_attempt(attempt: Attempt) -> None:
    ok = attempt.outcome in (PASSED, PASSED_UNVERIFIED)
    label = f"Attempt {attempt.number}: {OUTCOME_LABEL.get(attempt.outcome, attempt.outcome)}"
    with st.expander(label, expanded=not ok):
        if attempt.notes:
            st.caption(attempt.notes)
        st.code(attempt.code, language="python")
        if attempt.safety is not None:
            st.markdown(f"**Safety check:** {attempt.safety.summary()}")
        if attempt.sandbox is not None:
            st.markdown(
                f"**Test run:** exit code {attempt.sandbox.returncode}, "
                f"{attempt.sandbox.duration_s:.1f}s"
            )
            if attempt.sandbox.stderr.strip() and not ok:
                st.code(attempt.sandbox.stderr[-2500:], language="text")
        if attempt.diff is not None:
            st.markdown(f"**Comparison with your example:**\n\n{attempt.diff.summary()}")


def step_build(ss, settings: Settings) -> None:
    st.subheader("5. Write, check and test the script")
    submission = ss["turn"].submission
    if ss["build"] is None:
        if st.button("Build my tool", type="primary"):
            key = ss["interview"].session_key
            with st.status("Writing and testing the script...", expanded=True) as status:
                result = build_tool(
                    ss["provider"],
                    submission,
                    ss["files"],
                    key,
                    expected_file=ss["expected"],
                    share_real_values=ss["share_real"],
                    max_repairs=settings.max_repairs,
                    timeout_s=settings.sandbox_timeout_s,
                    run_root=Path(ss["workdir"]) / "runs",
                    on_attempt=lambda a: status.write(
                        f"Attempt {a.number}: {OUTCOME_LABEL.get(a.outcome, a.outcome)}"
                    ),
                )
                status.update(label="Done", state="complete" if result.ok else "error")
            ss["build"] = result
            if result.ok:
                ss["zip"] = build_zip(
                    result.script or "",
                    submission,
                    [p.name for p in ss["files"]],
                    len(result.attempts),
                )
            st.rerun()
        return
    result = ss["build"]
    for attempt in result.attempts:
        _render_attempt(attempt)
    if result.ok:
        st.subheader("6. Your tool")
        st.success(
            f"Passed after {len(result.attempts)} attempt(s). The zip contains script.py, "
            "run.bat (double-click), README_for_colleague.md and requirements.txt."
        )
        st.download_button(
            "Download the tool (.zip)",
            data=ss["zip"],
            file_name="excel_tool.zip",
            mime="application/zip",
            type="primary",
        )
        with st.expander("Preview: README_for_colleague.md"):
            import io
            import zipfile

            with zipfile.ZipFile(io.BytesIO(ss["zip"])) as zf:
                st.markdown(zf.read("README_for_colleague.md").decode("utf-8"))
    else:
        st.error(
            f"No attempt passed after {len(result.attempts)} tries. A human should take a look - "
            "the attempts above show what went wrong."
        )


def main() -> None:
    st.set_page_config(page_title="xlagent - Excel automation agent", layout="wide")
    st.title("Excel automation agent")
    st.caption(
        "Describe a repetitive Excel task, answer a few questions, and get a tested, "
        "double-click tool back."
    )
    settings = Settings.from_env()
    ss = _state()
    sidebar(ss, settings)
    step_upload(ss)
    if not ss["files"]:
        return
    profile_text = step_profile(ss)
    step_interview(ss, settings, profile_text)
    if ss["turn"] is not None and ss["turn"].is_spec:
        step_spec(ss)
        if ss["confirmed"]:
            step_build(ss, settings)


main()
