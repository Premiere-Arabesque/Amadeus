import os
from pathlib import Path

from app.infra.env import load_project_env


def test_load_project_env_reads_dotenv_file(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("AMADEUS_QQ_APP_ID=qq-app-id-from-dotenv\n", encoding="utf-8")
    monkeypatch.delenv("AMADEUS_QQ_APP_ID", raising=False)

    loaded_path = load_project_env(env_file)

    assert loaded_path == env_file
    assert env_file.exists()
    assert Path(loaded_path).name == ".env"
    assert os.getenv("AMADEUS_QQ_APP_ID") == "qq-app-id-from-dotenv"
