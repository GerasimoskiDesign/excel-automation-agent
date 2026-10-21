from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from xlagent.llm.base import LLMRequest, LLMResponse

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
SOURCES = ROOT / "scripts" / "fixture_sources"


def tool_message(name: str, payload: dict[str, Any], idx: int = 1, text: str = "") -> dict:
    """An Anthropic-shaped assistant message with one tool_use block."""
    content: list[dict[str, Any]] = []
    if text:
        content.append({"type": "text", "text": text})
    content.append({"type": "tool_use", "id": f"toolu_test_{idx}", "name": name, "input": payload})
    return {
        "id": f"msg_test_{idx}",
        "type": "message",
        "role": "assistant",
        "model": "test-model",
        "content": content,
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


class ScriptedProvider:
    """Test double: returns queued responses (or a callable's result) and logs requests."""

    name = "scripted"

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        # Deep-copy the history so later appends don't change what we recorded.
        self.requests.append(
            LLMRequest(
                system=request.system,
                messages=json.loads(json.dumps(request.messages)),
                tools=request.tools,
                session_key=request.session_key,
                stage=request.stage,
            )
        )
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if callable(item):
            item = item(len(self.requests))
        return LLMResponse.from_api_dict(item)


def load_spec_dict(example: str = "sales_consolidation") -> dict:
    from xlagent.llm.replay import default_cassette_dir

    data = json.loads((default_cassette_dir() / f"{example}.json").read_text(encoding="utf-8"))
    first = data["interactions"][0]["response"]["content"]
    return next(b for b in first if b["type"] == "tool_use")["input"]


@pytest.fixture
def examples_dir() -> Path:
    return EXAMPLES


@pytest.fixture
def sales_inputs() -> list[Path]:
    return sorted((EXAMPLES / "sales_consolidation" / "inputs").iterdir())


@pytest.fixture
def sales_expected() -> Path:
    return EXAMPLES / "sales_consolidation" / "expected" / "sales_summary.xlsx"


@pytest.fixture
def sales_submission():
    from xlagent.spec import SpecSubmission

    return SpecSubmission.model_validate(load_spec_dict("sales_consolidation"))


@pytest.fixture
def good_sales_code() -> str:
    return (SOURCES / "sales_consolidation" / "attempt_2.py").read_text(encoding="utf-8")
