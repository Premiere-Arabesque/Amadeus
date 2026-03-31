from app.infra.settings import MCPSettings
from app.main import create_app


def test_mcp_settings_parse_stdio_and_streamable_http_servers(monkeypatch) -> None:
    monkeypatch.setenv(
        "AMADEUS_MCP_SERVERS_JSON",
        """
        [
          {
            "server_id": "local-notes",
            "transport": "stdio",
            "command": "python",
            "args": ["-m", "notes_server"]
          },
          {
            "server_id": "remote-browser",
            "transport": "streamable_http",
            "url": "http://127.0.0.1:8080/mcp",
            "headers": {"Authorization": "Bearer test-token"}
          }
        ]
        """,
    )

    settings = MCPSettings.from_env()

    assert len(settings.servers) == 2
    assert settings.servers[0].server_id == "local-notes"
    assert settings.servers[0].command == "python"
    assert settings.servers[1].server_id == "remote-browser"
    assert settings.servers[1].url == "http://127.0.0.1:8080/mcp"


def test_create_app_builds_mcp_provider_from_settings() -> None:
    app = create_app(
        mcp_settings=MCPSettings.model_validate(
            {
                "servers": [
                    {
                        "server_id": "local-notes",
                        "transport": "stdio",
                        "command": "python",
                        "args": ["-m", "notes_server"],
                    }
                ]
            }
        )
    )

    assert len(app.state.mcp_provider.servers) == 1
    assert app.state.mcp_provider.servers[0].server_id == "local-notes"
