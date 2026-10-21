"""Provider-neutral request/response types for the model layer.

Why a tiny interface of our own: the agent logic should not care whether a
response came from the live API, a replayed cassette or a test double. The
shapes deliberately mirror the Anthropic Messages API (content blocks with
`type`, `text`, `tool_use`), so the Anthropic provider is a thin adapter and
cassettes store the raw API response unchanged. Another provider would
translate its own format into these shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class LLMRequest:
    system: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] = field(default_factory=list)
    max_tokens: int = 16000
    # Not sent to the model: identifies the conversation and step, used by
    # replay (to find the right cassette entry) and by the recorder.
    session_key: str = ""
    stage: str = ""


@dataclass
class LLMResponse:
    content: list[dict[str, Any]]
    stop_reason: str | None
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def tool_uses(self) -> list[dict[str, Any]]:
        return [b for b in self.content if b.get("type") == "tool_use"]

    def text(self) -> str:
        return "\n".join(b.get("text", "") for b in self.content if b.get("type") == "text")

    def as_assistant_message(self) -> dict[str, Any]:
        """The full content goes back into the history unchanged (thinking blocks included)."""
        return {"role": "assistant", "content": self.content}

    @classmethod
    def from_api_dict(cls, data: dict[str, Any]) -> LLMResponse:
        return cls(
            content=list(data.get("content", [])),
            stop_reason=data.get("stop_reason"),
            model=data.get("model", ""),
            usage=dict(data.get("usage") or {}),
            raw=data,
        )


class LLMError(RuntimeError):
    """Raised when the model cannot produce a usable answer."""


class LLMProvider(Protocol):
    name: str

    def complete(self, request: LLMRequest) -> LLMResponse: ...
