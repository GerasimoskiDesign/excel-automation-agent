"""Pick the provider for the current settings: replay, live, or live + recorder."""

from __future__ import annotations

import logging

from xlagent.config import Settings
from xlagent.llm.base import LLMProvider
from xlagent.llm.replay import RecordingProvider, ReplayProvider

log = logging.getLogger(__name__)


def make_provider(settings: Settings) -> LLMProvider:
    if settings.replay:
        log.info("model: replay mode (recorded responses, no API calls)")
        return ReplayProvider()
    from xlagent.llm.anthropic_provider import AnthropicProvider

    provider: LLMProvider = AnthropicProvider(
        model=settings.model,
        effort=settings.effort,
        refusal_fallback=settings.refusal_fallback,
    )
    log.info("model: live %s (effort=%s)", settings.model, settings.effort)
    if settings.record:
        log.info("model: recording cassettes to %s", settings.record_dir)
        provider = RecordingProvider(provider, settings.record_dir)
    return provider
