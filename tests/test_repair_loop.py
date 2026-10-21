from tests.conftest import ScriptedProvider, tool_message
from xlagent.agent.pipeline import (
    CRASHED,
    MISMATCH,
    PASSED,
    PASSED_UNVERIFIED,
    SAFETY_REJECTED,
    build_tool,
)

CRASHING = (
    "import pandas as pd\n\n\ndef run(input_paths, output_path):\n    raise KeyError('Revenue')\n"
)
UNSAFE = "import os\n\n\ndef run(input_paths, output_path):\n    os.system('dir')\n"
WRONG_OUTPUT = (
    "import pandas as pd\n\n\ndef run(input_paths, output_path):\n"
    "    with pd.ExcelWriter(output_path) as w:\n"
    "        pd.DataFrame({'Shop': ['x']}).to_excel(w, sheet_name='Summary', index=False)\n"
)


def _script(code: str):
    return lambda n: tool_message("write_script", {"code": code, "notes": "n"}, idx=n)


def test_repair_loop_stops_after_three_repairs(sales_submission, sales_inputs, tmp_path):
    provider = ScriptedProvider([_script(CRASHING)])
    result = build_tool(
        provider, sales_submission, sales_inputs, "k", timeout_s=60, run_root=tmp_path
    )
    assert result.status == "failed"
    assert len(result.attempts) == 4  # 1 generate + 3 repairs
    assert len(provider.requests) == 4
    assert [r.stage for r in provider.requests] == ["generate", "repair", "repair", "repair"]
    assert all(a.outcome == CRASHED for a in result.attempts)


def test_error_is_fed_back_as_tool_result(sales_submission, sales_inputs, tmp_path):
    provider = ScriptedProvider([_script(CRASHING)])
    build_tool(provider, sales_submission, sales_inputs, "k", max_repairs=1, run_root=tmp_path)
    second = provider.requests[1].messages
    feedback = second[-1]["content"][0]
    assert feedback["type"] == "tool_result"
    assert feedback["tool_use_id"] == "toolu_test_1"
    assert feedback["is_error"] is True
    assert "KeyError" in feedback["content"]


def test_safety_violation_is_not_run_and_regenerated(
    sales_submission, sales_inputs, sales_expected, good_sales_code, tmp_path
):
    provider = ScriptedProvider([_script(UNSAFE), _script(good_sales_code)])
    result = build_tool(
        provider,
        sales_submission,
        sales_inputs,
        "k",
        expected_file=sales_expected,
        run_root=tmp_path,
    )
    first, second = result.attempts
    assert first.outcome == SAFETY_REJECTED
    assert first.sandbox is None  # never executed
    assert not (tmp_path / "attempt_1").exists()
    assert "not on the allowlist" in provider.requests[1].messages[-1]["content"][0]["content"]
    assert second.outcome == PASSED
    assert result.status == PASSED and result.script and result.output_file.exists()


def test_mismatch_with_expected_output_triggers_repair(
    sales_submission, sales_inputs, sales_expected, good_sales_code, tmp_path
):
    provider = ScriptedProvider([_script(WRONG_OUTPUT), _script(good_sales_code)])
    result = build_tool(
        provider,
        sales_submission,
        sales_inputs,
        "k",
        expected_file=sales_expected,
        run_root=tmp_path,
    )
    assert [a.outcome for a in result.attempts] == [MISMATCH, PASSED]
    hint = provider.requests[1].messages[-1]["content"][0]["content"]
    assert "does not match the expected example" in hint
    assert "missing" in hint


def test_without_expected_output_a_clean_run_is_unverified(
    sales_submission, sales_inputs, good_sales_code, tmp_path
):
    provider = ScriptedProvider([_script(good_sales_code)])
    result = build_tool(provider, sales_submission, sales_inputs, "k", run_root=tmp_path)
    assert result.status == PASSED_UNVERIFIED
    assert len(provider.requests) == 1


def test_text_only_reply_counts_as_attempt(sales_submission, sales_inputs, tmp_path):
    text_only = {
        "id": "m",
        "type": "message",
        "role": "assistant",
        "model": "t",
        "content": [{"type": "text", "text": "Here you go: ```python ...```"}],
        "stop_reason": "end_turn",
        "usage": {},
    }
    provider = ScriptedProvider([text_only])
    result = build_tool(
        provider, sales_submission, sales_inputs, "k", max_repairs=1, run_root=tmp_path
    )
    assert [a.outcome for a in result.attempts] == ["no_code", "no_code"]
    assert "write_script" in provider.requests[1].messages[-1]["content"]
