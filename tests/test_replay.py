import json

import pytest

from tests.conftest import ScriptedProvider, tool_message
from xlagent.config import Settings
from xlagent.llm.base import LLMRequest
from xlagent.llm.factory import make_provider
from xlagent.llm.replay import (
    CASSETTE_FORMAT,
    RecordingProvider,
    ReplayError,
    ReplayProvider,
    default_cassette_dir,
    load_cassettes,
    session_key,
)


def _req(key: str, stage: str) -> LLMRequest:
    return LLMRequest(
        system="s", messages=[{"role": "user", "content": "hi"}], session_key=key, stage=stage
    )


def test_session_key_ignores_whitespace_and_file_order():
    a = session_key("noninteractive", "Sum  the\nfiles ", ["b.xlsx", "a.xlsx"])
    b = session_key("noninteractive", "Sum the files", ["a.xlsx", "b.xlsx"])
    assert a == b
    assert a != session_key("interactive", "Sum the files", ["a.xlsx", "b.xlsx"])


def test_bundled_cassettes_are_valid_api_shaped_messages():
    cassettes = load_cassettes([default_cassette_dir()])
    assert len(cassettes) == 4
    for cassette in cassettes.values():
        assert cassette["format"] == CASSETTE_FORMAT
        assert cassette["source"] == "hand-authored"
        for entry in cassette["interactions"]:
            msg = entry["response"]
            assert msg["type"] == "message" and msg["role"] == "assistant"
            assert msg["stop_reason"] == "tool_use"
            tool_uses = [b for b in msg["content"] if b["type"] == "tool_use"]
            assert len(tool_uses) == 1 and tool_uses[0]["id"].startswith("toolu_")
            assert {"input_tokens", "output_tokens"} <= set(msg["usage"])


def test_unknown_session_gives_helpful_error():
    with pytest.raises(ReplayError, match="Replay mode only works for the recorded examples"):
        ReplayProvider().complete(_req("nope", "interview"))


def test_stage_mismatch_fails_loudly():
    provider = ReplayProvider()
    key = next(iter(provider.cassettes))
    with pytest.raises(ReplayError, match="recorded for stage 'interview'"):
        provider.complete(_req(key, "generate"))


def test_exhausted_cassette_fails_loudly(tmp_path):
    cassette = {
        "format": CASSETTE_FORMAT,
        "session_key": "k1",
        "label": "tiny",
        "interactions": [
            {"stage": "interview", "request": {}, "response": tool_message("ask_question", {})}
        ],
    }
    (tmp_path / "k1.json").write_text(json.dumps(cassette))
    provider = ReplayProvider([tmp_path])
    provider.complete(_req("k1", "interview"))
    with pytest.raises(ReplayError, match="only 1 recorded"):
        provider.complete(_req("k1", "interview"))


def test_recorder_output_replays_identically(tmp_path):
    responses = [tool_message("ask_question", {"question": "q", "why": "w"}, 1)]
    recorder = RecordingProvider(ScriptedProvider(responses), tmp_path)
    live = recorder.complete(_req("abc", "interview"))
    files = list(tmp_path.glob("*.json"))
    assert [f.name for f in files] == ["abc.json"]
    replayed = ReplayProvider([tmp_path]).complete(_req("abc", "interview"))
    assert replayed.content == live.content
    assert replayed.stop_reason == "tool_use"


def test_factory_defaults_to_replay():
    provider = make_provider(Settings())
    assert isinstance(provider, ReplayProvider)


def test_interactive_cassette_exposes_recorded_answers():
    provider = ReplayProvider()
    key = next(k for k, c in provider.cassettes.items() if c["mode"] == "interactive")
    assert provider.recorded_user_turns(key) == ["The month of the sale date", "Only net them off"]
