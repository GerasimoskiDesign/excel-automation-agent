"""Settings from environment variables (optionally loaded from a .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_MODEL = "claude-opus-5-5"


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    model: str = DEFAULT_MODEL
    effort: str = "high"
    refusal_fallback: bool = True
    replay: bool = True
    record: bool = False
    record_dir: Path = Path("recorded_cassettes")
    sandbox_timeout_s: float = 60.0
    work_dir: Path = Path(".xlagent_jobs")
    max_repairs: int = 3

    @classmethod
    def from_env(cls, load_env_file: bool = True) -> Settings:
        """Read settings. Replay defaults to ON so a fresh clone works without a key."""
        if load_env_file:
            load_dotenv(override=False)
        return cls(
            model=os.getenv("XLAGENT_MODEL", DEFAULT_MODEL),
            effort=os.getenv("XLAGENT_EFFORT", "high"),
            refusal_fallback=_flag("XLAGENT_REFUSAL_FALLBACK", "1"),
            replay=_flag("XLAGENT_REPLAY", "1"),
            record=_flag("XLAGENT_RECORD", "0"),
            record_dir=Path(os.getenv("XLAGENT_RECORD_DIR", "recorded_cassettes")),
            sandbox_timeout_s=float(os.getenv("XLAGENT_SANDBOX_TIMEOUT", "60")),
            work_dir=Path(os.getenv("XLAGENT_WORK_DIR", ".xlagent_jobs")),
        )
