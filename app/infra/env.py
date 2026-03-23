from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_project_env(dotenv_path: Path | None = None) -> Path:
    env_path = dotenv_path or project_root() / ".env"
    load_dotenv(env_path, override=False)
    return env_path
