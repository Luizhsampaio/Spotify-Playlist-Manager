from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from music_curator.cli import cli
from music_curator.config import load_settings


def test_config_reads_only_test_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPOTIFY_AI_POLICY_MODE", raising=False)
    assert load_settings().client_id == ""
    assert load_settings().policy_mode == "blocked"
    (tmp_path / ".env").write_text("SPOTIFY_CLIENT_ID=" + "a" * 32, encoding="utf-8")
    assert load_settings().client_id == "a" * 32
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "b" * 32)
    assert load_settings().client_id == "b" * 32


def test_config_rejects_bad_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "not-a-client-id")
    with pytest.raises(ValidationError):
        load_settings()
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "0" * 32)
    monkeypatch.setenv("SPOTIFY_AI_POLICY_MODE", "allowed")
    with pytest.raises(ValidationError):
        load_settings()


def test_cli_start_is_loopback_and_logs_no_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPOTIFY_AI_POLICY_MODE", raising=False)
    server = Mock()
    monkeypatch.setattr("music_curator.cli.uvicorn.run", server)
    result = CliRunner().invoke(cli, ["serve"])
    assert result.exit_code == 0
    assert "127.0.0.1:8765" in result.output
    assert server.call_args.kwargs["host"] == "127.0.0.1"
    assert server.call_args.kwargs["access_log"] is False


def test_cli_demo_ignores_real_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "invalid")
    server = Mock()
    monkeypatch.setattr("music_curator.cli.uvicorn.run", server)
    result = CliRunner().invoke(cli, ["serve", "--demo"])
    assert result.exit_code == 0
    assert "DEMONSTRACAO" in result.output


def test_cli_config_error_does_not_echo_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "synthetic-private-value")
    result = CliRunner().invoke(cli, ["serve"])
    assert result.exit_code == 1
    assert "Configuracao invalida" in result.output
    assert "synthetic-private-value" not in result.output
