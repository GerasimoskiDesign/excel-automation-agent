"""Replay and record model responses ("cassettes").

Why replay: tests, CI and the demo must run without an API key and must be
deterministic. A cassette stores the exact Messages API responses for one
build session (interview -> generate -> repairs) in order.

How a session is found: by a key derived from what the *user* provides (mode,
task text, input file names) - not from our prompt templates - so editing a
prompt does not silently invalidate every cassette. The stage of each call
("interview", "generate", "repair") is checked on replay, so a change in the
agent's control flow fails loudly instead of returning the wrong response.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from importlib import resources
from pathlib import Path
from typing import Any

from xlagent.llm.base import LLMError, LLMProvider, LLMRequest, LLMResponse

log = logging.getLogger(__name__)

CASSETTE_FORMAT = "xlagent-cassette/1"


class ReplayError(LLMError):
    """No recorded response fits this request."""


def normalise_task(task: str) -> str:
    return re.sub(r"\s+", " ", task).strip()


def session_key(mode: str, task: str, input_names: list[str]) -> str:
    """Stable id for a build session, independent of prompt wording."""
    payload = json.dumps(
        {"mode": mode, "task": normalise_task(task), "inputs": sorted(input_names)},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content
        parts = []
        for block in content or []:
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") == "tool_result":
                inner = block.get("content")
                if isinstance(inner, str):
                    parts.append(inner)
                else:
                    parts.extend(b.get("text", "") for b in inner or [] if b.get("type") == "text")
        return "\n".join(parts)
    return ""


def default_cassette_dir() -> Path:
    return Path(str(resources.files("xlagent") / "fixtures" / "cassettes"))


def load_cassettes(dirs: list[Path]) -> dict[str, dict[str, Any]]:
    cassettes: dict[str, dict[str, Any]] = {}
    for d in dirs:
        for path in sorted(Path(d).glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("format") != CASSETTE_FORMAT:
                log.warning("skipping %s: not an xlagent cassette", path)
                continue
            data["_path"] = str(path)
            cassettes[data["session_key"]] = data
    return cassettes


class ReplayProvider:
    """Returns recorded responses in order for the session named in each request."""

    name = "replay"

    def __init__(self, cassette_dirs: list[Path] | None = None) -> None:
        self.cassettes = load_cassettes(cassette_dirs or [default_cassette_dir()])
        self._cursor: dict[str, int] = {}

    def has_session(self, key: str) -> bool:
        return key in self.cassettes

    def complete(self, request: LLMRequest) -> LLMResponse:
        cassette = self.cassettes.get(request.session_key)
        if cassette is None:
            known = ", ".join(c.get("label", k) for k, c in self.cassettes.items())
            raise ReplayError(
                "No recorded session matches this task and these file names "
                f"(key {request.session_key}). Replay mode only works for the recorded "
                f"examples: {known}. Set XLAGENT_REPLAY=0 and ANTHROPIC_API_KEY to run live."
            )
        index = self._cursor.get(request.session_key, 0)
        interactions = cassette["interactions"]
        if index >= len(interactions):
            raise ReplayError(
                f"cassette '{cassette.get('label')}' has only {len(interactions)} recorded "
                f"responses; the agent asked for one more ({request.stage})"
            )
        entry = interactions[index]
        if entry["stage"] != request.stage:
            raise ReplayError(
                f"cassette '{cassette.get('label')}' step {index + 1} was recorded for stage "
                f"'{entry['stage']}' but the agent is in stage '{request.stage}'"
            )
        recorded_user = entry.get("request", {}).get("last_user_message")
        placeholder = recorded_user is None or recorded_user.startswith("<")
        if not placeholder and request.stage == "interview":
            actual = _last_user_text(request.messages)
            if normalise_task(actual)[:200] != normalise_task(recorded_user)[:200]:
                log.warning(
                    "replay: user turn differs from the recording (replaying anyway): %r",
                    actual[:80],
                )
        self._cursor[request.session_key] = index + 1
        return LLMResponse.from_api_dict(entry["response"])

    def recorded_user_turns(self, key: str) -> list[str]:
        """User answers stored in a cassette - lets the demo UI suggest them."""
        cassette = self.cassettes.get(key, {})
        turns = []
        for entry in cassette.get("interactions", [])[1:]:
            if entry["stage"] == "interview":
                turns.append(entry.get("request", {}).get("last_user_message", ""))
        return [t for t in turns if t]


class RecordingProvider:
    """Wraps a live provider and writes every exchange into a cassette file.

    Cassettes are written after every call, so a crashed session still leaves
    a usable partial recording for debugging.
    """

    name = "recording"

    def __init__(self, inner: LLMProvider, out_dir: Path, source: str = "recorded") -> None:
        self.inner = inner
        self.out_dir = Path(out_dir)
        self.source = source
        self._sessions: dict[str, dict[str, Any]] = {}

    def complete(self, request: LLMRequest) -> LLMResponse:
        response = self.inner.complete(request)
        cassette = self._sessions.setdefault(
            request.session_key,
            {
                "format": CASSETTE_FORMAT,
                "session_key": request.session_key,
                "label": request.session_key,
                "source": self.source,
                "model": response.model,
                "interactions": [],
            },
        )
        user_text = _last_user_text(request.messages)
        cassette["interactions"].append(
            {
                "stage": request.stage,
                "request": {"last_user_message": user_text[:2000]},
                "response": response.raw
                or {
                    "type": "message",
                    "role": "assistant",
                    "model": response.model,
                    "content": response.content,
                    "stop_reason": response.stop_reason,
                    "usage": response.usage,
                },
            }
        )
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / f"{request.session_key}.json"
        path.write_text(
            json.dumps(cassette, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n"
        )
        log.info("recorder: %s now has %d interactions", path, len(cassette["interactions"]))
        return response
