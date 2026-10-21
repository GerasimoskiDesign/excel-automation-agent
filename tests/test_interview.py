import copy

import pytest

from tests.conftest import ScriptedProvider, load_spec_dict, tool_message
from xlagent.agent.interview import Interview
from xlagent.llm.base import LLMError

QUESTION = {"question": "Which month counts?", "why": "Late entries", "options": ["a", "b"]}


def _interview(provider, interactive=True) -> Interview:
    return Interview(provider, "Summarise my sales files", "{}", "key", interactive=interactive)


def test_questions_one_at_a_time_then_spec():
    spec = load_spec_dict()
    provider = ScriptedProvider(
        [
            tool_message("ask_question", QUESTION, 1),
            tool_message("ask_question", {"question": "Refunds?", "why": "w"}, 2),
            tool_message("submit_spec", spec, 3),
        ]
    )
    interview = _interview(provider)
    turn = interview.start()
    assert turn.question.text == "Which month counts?"
    assert turn.question.options == ["a", "b"]
    turn = interview.answer("The sale date")
    assert turn.question.text == "Refunds?"
    # The answer was sent back as the tool_result for the pending question.
    last = provider.requests[1].messages[-1]["content"][0]
    assert last == {
        "type": "tool_result",
        "tool_use_id": "toolu_test_1",
        "content": "The sale date",
    }
    turn = interview.answer("Net them off")
    assert turn.is_spec
    assert turn.submission.spec.output_format.file_name == "sales_summary.xlsx"
    assert [s for s, _ in interview.transcript].count("colleague") == 2


def test_invalid_spec_goes_back_to_the_model():
    good = load_spec_dict()
    bad = copy.deepcopy(good)
    bad["spec"]["output_format"]["file_name"] = "summary.csv"
    provider = ScriptedProvider(
        [tool_message("submit_spec", bad, 1), tool_message("submit_spec", good, 2)]
    )
    turn = _interview(provider).start()
    assert turn.is_spec
    error = provider.requests[1].messages[-1]["content"][0]
    assert error["is_error"] is True
    assert "must end with .xlsx" in error["content"]


def test_gives_up_after_repeated_invalid_specs():
    bad = {"spec": {"title": "x"}}
    provider = ScriptedProvider([tool_message("submit_spec", bad, 1)])
    with pytest.raises(LLMError):
        _interview(provider).start()


def test_request_changes_reopens_the_conversation():
    spec = load_spec_dict()
    provider = ScriptedProvider(
        [tool_message("submit_spec", spec, 1), tool_message("submit_spec", spec, 2)]
    )
    interview = _interview(provider)
    interview.start()
    turn = interview.request_changes("Please sort by revenue")
    assert turn.is_spec
    assert "asked for changes: Please sort by revenue" in str(provider.requests[1].messages[-1])


def test_noninteractive_mode_refuses_questions():
    spec = load_spec_dict()
    provider = ScriptedProvider(
        [tool_message("ask_question", QUESTION, 1), tool_message("submit_spec", spec, 2)]
    )
    interview = _interview(provider, interactive=False)
    assert [t["name"] for t in interview.tools] == ["submit_spec"]
    turn = interview.start()
    assert turn.is_spec
    assert "Nobody can answer" in provider.requests[1].messages[-1]["content"][0]["content"]


def test_answer_without_pending_question_is_an_error():
    with pytest.raises(RuntimeError):
        _interview(ScriptedProvider([tool_message("ask_question", QUESTION)])).answer("x")
