"""The scoping interview: questions one at a time until a valid spec exists.

The interview is a small state machine around one model conversation. Each
model turn ends in a tool call: `ask_question` (we wait for the colleague's
answer) or `submit_spec` (validated with Pydantic; errors go back to the model
so it can correct itself). Nothing here knows about Streamlit or HTTP, so the
same class drives the UI, the API's non-interactive mode and the tests.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from xlagent import prompts
from xlagent.llm.base import LLMError, LLMProvider, LLMRequest, LLMResponse
from xlagent.spec import SpecSubmission

log = logging.getLogger(__name__)

MAX_MODEL_TURNS = 12
MAX_INVALID_SPECS = 3


@dataclass
class Question:
    text: str
    why: str = ""
    options: list[str] = field(default_factory=list)


@dataclass
class InterviewTurn:
    """What the UI should show next: a question, or a spec to confirm."""

    question: Question | None = None
    submission: SpecSubmission | None = None
    preface: str = ""  # any text the model wrote before its tool call

    @property
    def is_spec(self) -> bool:
        return self.submission is not None


def _format_validation_error(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors()[:10]:
        loc = ".".join(str(p) for p in err["loc"])
        lines.append(f"- {loc}: {err['msg']}")
    return "The spec did not validate. Fix these problems and call submit_spec again:\n" + (
        "\n".join(lines)
    )


class Interview:
    def __init__(
        self,
        provider: LLMProvider,
        task: str,
        profile_text: str,
        session_key: str,
        expected_profile_text: str | None = None,
        interactive: bool = True,
    ) -> None:
        self.provider = provider
        self.session_key = session_key
        self.interactive = interactive
        self.system = (
            prompts.INTERVIEW_SYSTEM if interactive else prompts.INTERVIEW_SYSTEM_NONINTERACTIVE
        )
        self.tools = prompts.INTERVIEW_TOOLS if interactive else prompts.NONINTERACTIVE_TOOLS
        opening = prompts.interview_opening(task, profile_text, expected_profile_text)
        self.messages: list[dict[str, Any]] = [{"role": "user", "content": opening}]
        self.transcript: list[tuple[str, str]] = []  # (speaker, text) for display
        self._pending_tool_id: str | None = None
        self._extra_pending: list[dict[str, Any]] = []
        self._model_turns = 0
        self.submission: SpecSubmission | None = None

    # -- public API ---------------------------------------------------------
    def start(self) -> InterviewTurn:
        return self._advance()

    def answer(self, text: str) -> InterviewTurn:
        """Send the colleague's answer to the pending question."""
        if self._pending_tool_id is None:
            raise RuntimeError("no question is waiting for an answer")
        self.transcript.append(("colleague", text))
        self._reply_tool(text)
        return self._advance()

    def request_changes(self, feedback: str) -> InterviewTurn:
        """The colleague rejected the proposed spec; let the model revise it."""
        if self._pending_tool_id is None or self.submission is None:
            raise RuntimeError("there is no spec waiting for confirmation")
        self.transcript.append(("colleague", feedback))
        self.submission = None
        self._reply_tool(f"The colleague reviewed the spec and asked for changes: {feedback}")
        return self._advance()

    # -- internals ----------------------------------------------------------
    def _reply_tool(self, content: str, is_error: bool = False) -> None:
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": self._pending_tool_id,
            "content": content,
        }
        if is_error:
            block["is_error"] = True
        blocks = [block]
        # Every tool_use needs a tool_result; we handle one call per turn.
        for extra in self._extra_pending:
            blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": extra["id"],
                    "content": "Ignored: please make one tool call per turn.",
                    "is_error": True,
                }
            )
        self.messages.append({"role": "user", "content": blocks})
        self._pending_tool_id = None
        self._extra_pending = []

    def _call(self) -> LLMResponse:
        self._model_turns += 1
        if self._model_turns > MAX_MODEL_TURNS:
            raise LLMError(
                "the interview did not converge; please describe the task in more detail"
            )
        response = self.provider.complete(
            LLMRequest(
                system=self.system,
                messages=self.messages,
                tools=self.tools,
                max_tokens=16000,
                session_key=self.session_key,
                stage="interview",
            )
        )
        self.messages.append(response.as_assistant_message())
        return response

    def _advance(self) -> InterviewTurn:
        invalid = 0
        while True:
            response = self._call()
            preface = response.text().strip()
            tool_uses = response.tool_uses()
            if not tool_uses:
                # Text-only reply: nudge the model back to the tools.
                self.messages.append(
                    {
                        "role": "user",
                        "content": "Please continue by calling ask_question or submit_spec.",
                    }
                )
                continue
            first, extra = tool_uses[0], tool_uses[1:]
            if extra:
                log.info("interview: ignoring %d extra tool calls", len(extra))
            self._pending_tool_id = first["id"]
            self._extra_pending = extra
            if first["name"] == "ask_question" and self.interactive:
                args = first.get("input", {})
                question = Question(
                    text=str(args.get("question", "")).strip(),
                    why=str(args.get("why", "")).strip(),
                    options=[str(o) for o in args.get("options", []) or []],
                )
                self.transcript.append(("agent", question.text))
                return InterviewTurn(question=question, preface=preface)
            if first["name"] == "submit_spec":
                try:
                    submission = SpecSubmission.model_validate(first.get("input", {}))
                except ValidationError as exc:
                    invalid += 1
                    if invalid > MAX_INVALID_SPECS:
                        raise LLMError("the model could not produce a valid spec") from exc
                    log.info("interview: spec invalid (%d errors)", exc.error_count())
                    self._reply_tool(_format_validation_error(exc), is_error=True)
                    continue
                self.submission = submission
                self.transcript.append(("agent", "Proposed spec: " + submission.spec.title))
                return InterviewTurn(submission=submission, preface=preface)
            self._reply_tool(
                "Nobody can answer questions in this mode. Make an assumption, list it in "
                "`assumptions`, and call submit_spec.",
                is_error=True,
            )


def spec_to_json(submission: SpecSubmission) -> str:
    return json.dumps(submission.spec.model_dump(mode="json"), indent=2)
