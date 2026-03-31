from __future__ import annotations

from os import environ
from pathlib import Path
from typing import Mapping


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def project_env_path() -> Path:
    return project_root() / ".env"


def load_project_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    load_dotenv(project_env_path(), override=False)


def update_project_env(values: Mapping[str, str]) -> Path:
    env_path = project_env_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.touch(exist_ok=True)
    try:
        from dotenv import set_key
    except ImportError:
        _update_env_file_fallback(env_path, values)
    else:
        for key, value in values.items():
            set_key(str(env_path), key, value, quote_mode="never")
    return env_path


def sync_process_env(values: Mapping[str, str]) -> None:
    for key, value in values.items():
        environ[str(key)] = str(value)


def _update_env_file_fallback(env_path: Path, values: Mapping[str, str]) -> None:
    existing_lines = env_path.read_text(encoding="utf-8").splitlines()
    pending = {str(key): str(value) for key, value in values.items()}
    updated_lines: list[str] = []
    seen: set[str] = set()
    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            updated_lines.append(line)
            continue
        key, _, _ = line.partition("=")
        normalized_key = key.strip()
        if normalized_key in pending:
            updated_lines.append(f"{normalized_key}={pending[normalized_key]}")
            seen.add(normalized_key)
            continue
        updated_lines.append(line)
    for key, value in pending.items():
        if key in seen:
            continue
        updated_lines.append(f"{key}={value}")
    env_path.write_text("\n".join(updated_lines).strip() + "\n", encoding="utf-8")
