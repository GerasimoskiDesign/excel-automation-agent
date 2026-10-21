"""Live provider backed by the Anthropic Python SDK."""

from __future__ import annotations

import logging
from typing import Any

from xlagent.llm.base import LLMError, LLMRequest, LLMResponse

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider:
    """Calls the Messages API with adaptive thinking and an explicit effort level.

    Why streaming + get_final_message: generated scripts can be long, and
    streaming avoids HTTP timeouts on large outputs without us handling events.
    Why the refusal fallback: a declined request (stop_reason "refusal") would
    otherwise stall the interview; the server-side fallback retries on another
    model. It is a beta feature and can be turned off with
    XLAGENT_REFUSAL_FALLBACK=0.
    """

    name = "anthropic"

    def __init__(
        self,
        model: str,
        effort: str = "high",
        refusal_fallback: bool = True,
        client: Any | None = None,
    ) -> None:
        import anthropic  # imported lazily so replay mode never needs the SDK configured

        self.model = model
        self.effort = effort
        self.refusal_fallback = refusal_fallback
        self.client = client or anthropic.Anthropic()

    def complete(self, request: LLMRequest) -> LLMResponse:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": request.messages,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.effort},
        }
        if request.tools:
            params["tools"] = request.tools
            params["tool_choice"] = {"type": "auto"}
        log.info("anthropic: %s stage=%s", self.model, request.stage)
        if self.refusal_fallback:
            stream = self.client.beta.messages.stream(
                betas=[FALLBACK_BETA], fallbacks="default", **params
            )
        else:
            stream = self.client.messages.stream(**params)
        with stream as s:
            message = s.get_final_message()
        data = message.model_dump(mode="json", exclude_none=True)
        if data.get("stop_reason") == "refusal":
            raise LLMError(f"model declined the request: {data.get('stop_details')}")
        return LLMResponse.from_api_dict(data)
